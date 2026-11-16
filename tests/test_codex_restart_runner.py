"""Offline adapters only: never start native, fixture, authority or network."""
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import time

import pytest

from ccc_server.execution_artifact import canonical, digest
from ccc_server.codex_managed_proof import ISOLATION_CHECKS
from scripts import codex_restart_preflight as prep
from scripts import codex_restart_runner as run
from scripts.codex_restart_fixture import Budget, Stop
from scripts.codex_restart_harness import OuterControl


@pytest.fixture
def grant():
    return {'reference': 'offline-tests-only', 'max_lifetimes': 2, 'max_completions': 48,
            'max_lifetime_seconds': 1200,
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}


@pytest.fixture
def preparation(tmp_path, monkeypatch, grant):
    binary, schema = 'a' * 64, canonical([{'path': 'request.json', 'sha256': 'b' * 64}])
    monkeypatch.setattr(prep, 'SUPPORTED', {binary: ('offline-test', digest(schema))})
    index = tmp_path / 'schema-index.json'
    index.write_bytes(schema)
    entries = [{'path': p, 'mode': 0o755, 'type': 'directory'}
               for p in ('usr', 'usr/local', 'usr/local/bin')]
    entries += [{'path': 'usr/local/bin/python3', 'mode': 0o777,
                 'type': 'symlink', 'target': 'python3.12'},
                {'path': 'usr/local/bin/python3.12', 'mode': 0o755, 'type': 'file', 'sha256': 'c' * 64}]
    entries += [{'path': name, 'mode': 0o755 if name.endswith('/codex') else 0o644,
                 'type': 'file', 'sha256': sha} for name, sha in (
        ('opt/ccc-native/bin/codex', binary), ('opt/ccc-native/schema-index.json', digest(schema)),
        ('opt/ccc-native/schema/request.json', 'b' * 64))]
    source = Path(__file__).resolve().parents[1]
    entries += [{'path': 'opt/ccc/' + name, 'type': 'file', 'mode': 0o644,
                 'sha256': digest((source / name).read_bytes())} for name in prep.SOURCES]
    manifest = canonical({'format': 'ccc-execution-rootfs-v1', 'entries': entries})
    manifest_file = tmp_path / 'manifest.json'
    manifest_file.write_bytes(manifest)
    image = {'image_id': 'sha256:' + 'd' * 64, 'artifact_sha256': digest(manifest), 'manifest': str(manifest_file)}
    return {'format': prep.FORMAT, 'grant': grant, 'state_root': str(tmp_path / 'state'),
            'user': '65532:65532', 'images': {role: dict(image) for role in ('worker', 'replacement', 'control', 'native')},
            'native': {'version': 'offline-test', 'binary_sha256': binary,
                       'schema_sha256': digest(schema), 'schema_index': str(index)}}


def test_preparation_has_no_launches_counters_or_invented_facts(preparation, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('preparation attempted execution')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    packet = prep.prepare(preparation)
    assert packet['execution_ready'] is False
    assert packet['runtime_authorized'] is False
    assert all(value is None for value in packet['actual'].values())
    assert all(value is None for value in packet['acceptance'].values())
    assert packet['rebuild_required'] == []
    assert sum(c['completion_ceiling'] for c in packet['schedule']) == 48
    assert packet['harness_launch']['HostConfig']['NetworkMode'] == 'none'
    assert packet['harness_launch']['HostConfig']['CapAdd'] == []
    assert packet['requested_worker_profile']['commands'] == []
    assert not Path(preparation['state_root']).exists()


@pytest.mark.parametrize('mutate,reason', [
    (lambda d: d.update(user='0:0'), 'nonroot'),
    (lambda d: d.update(state_root='/state/../host'), 'absolute'),
    (lambda d: d['images']['native'].update(image_id='latest'), 'immutable'),
    (lambda d: d['images']['native'].update(artifact_sha256='e' * 64), 'changed'),
    (lambda d: d['native'].update(binary_sha256='f' * 64), 'unsupported'),
    (lambda d: d['native'].update(schema_sha256='f' * 64), 'unsupported'),
    (lambda d: d['grant'].update(max_lifetimes=3), 'cap'),
    (lambda d: d['grant'].update(max_completions=49), 'cap'),
    (lambda d: d['grant'].update(max_lifetime_seconds=True), 'cap'),
    (lambda d: d.update(accepted=True), 'inputs'),
])
def test_preparation_refuses_policy_and_pin_drift(preparation, mutate, reason):
    mutate(preparation)
    with pytest.raises(Stop, match=reason):
        prep.prepare(preparation)


def test_missing_helper_is_rebuild_required_not_native_ready(preparation, tmp_path):
    manifest = Path(preparation['images']['native']['manifest'])
    value = json.loads(manifest.read_bytes())
    value['entries'] = [r for r in value['entries'] if not r['path'].endswith('codex_restart_runner.py')]
    raw = canonical(value)
    changed = tmp_path / 'changed.json'
    changed.write_bytes(raw)
    preparation['images']['native'].update(manifest=str(changed), artifact_sha256=digest(raw))
    result = prep.prepare(preparation)
    assert result['rebuild_required'] == ['scripts/codex_restart_runner.py']
    assert result['execution_ready'] is False


def test_actual_cli_prepares_without_execution_and_never_overwrites(preparation, tmp_path):
    source, output = tmp_path / 'input.json', tmp_path / 'packet.json'
    source.write_bytes(canonical(preparation))
    assert prep.main(['prepare', str(source), str(output)]) == 0
    assert json.loads(output.read_bytes())['execution_ready'] is False
    assert output.stat().st_mode & 0o777 == 0o600
    original = output.read_bytes()
    assert prep.main(['prepare', str(source), str(output)]) == 2
    assert output.read_bytes() == original
    assert prep.main(['execute']) == 2


class OfflineAdapter:
    def __init__(self, fail=None):
        self.events, self.fail = [], fail

    def event(self, name):
        self.events.append(name)
        if self.fail == name:
            raise Stop('offline failure ' + name)

    def verify_closure(self, packet): self.event('closure')
    def structural(self): self.event('structural')
    def start_fixture(self, budget, record): self.event('fixture')
    def new_run_id(self, lifetime): return 'offline-' + str(lifetime)
    def begin_case(self, name, ceiling): self.event('case:' + name)
    def start_native(self, lifetime, remaining): self.event('native:' + str(lifetime))
    def stop_native(self): self.event('stop-native')
    def verify_outer_isolation(self): self.event('outer-isolation')
    def cleanup(self): self.event('cleanup')
    def verify_cleanup(self): self.event('absence')

    def isolation(self, phase, run_id):
        self.event(phase + ':' + run_id)
        raw = canonical({'offline_only': True, 'phase': phase, 'run_id': run_id})
        receipt = {'format': 1, 'phase': phase, 'run_id': run_id, 'verdict': 'pass',
            'raw_sha256': digest(raw), 'checks': dict.fromkeys(ISOLATION_CHECKS, True),
            'descendant_checks': dict.fromkeys(ISOLATION_CHECKS, True)}
        return receipt, raw


def workflow(tmp_path, grant, adapter, monkeypatch):
    tmp_path.chmod(0o700)
    budget = Budget(tmp_path, grant)
    packet = {'scope': 'disposable-proof-preparation', 'execution_ready': True,
              'runtime_authorized': False, 'grant': grant}
    acceptance = run.Acceptance(digest(canonical(packet)), 'offline-only', time.time() + 60)
    journal = io.BytesIO()
    runner = run.Workflow(packet, acceptance, budget, adapter, run.Journal(journal))
    def protocol(self):
        adapter.event('protocol')
        budget.completion()
        return {'P1': {'offline_only': True}}
    monkeypatch.setattr(run.Protocol, 'run', protocol)
    monkeypatch.setattr(runner, 'integration', lambda suite: adapter.event('integration') or {'offline_only': True})
    return runner, budget, journal


def test_two_lifetimes_share_durable_counters_and_order(tmp_path, grant, monkeypatch):
    adapter = OfflineAdapter()
    runner, budget, journal = workflow(tmp_path, grant, adapter, monkeypatch)
    result = runner.run()
    assert result['runtime_authorized'] is False
    assert adapter.events == ['closure', 'structural', 'fixture', 'E4:offline-1', 'native:1',
        'protocol', 'stop-native', 'E5:offline-1', 'outer-isolation', 'E4:offline-2', 'native:2',
        'case:integration', 'integration', 'stop-native', 'E5:offline-2', 'outer-isolation', 'cleanup', 'absence']
    state = json.loads((tmp_path / 'budget.json').read_bytes())
    assert (state['lifetimes'], state['completions'], state['active_until']) == (2, 1, 0)
    assert b'evidence' in journal.getvalue()


@pytest.mark.parametrize('failure', ['closure', 'structural', 'fixture', 'E4:offline-1',
    'native:1', 'protocol', 'E5:offline-1', 'outer-isolation', 'E4:offline-2', 'native:2'])
def test_failure_halts_without_retry_and_always_cleans_exact_scope(tmp_path, grant, monkeypatch, failure):
    adapter = OfflineAdapter(failure)
    runner, budget, journal = workflow(tmp_path, grant, adapter, monkeypatch)
    with pytest.raises(Stop, match='offline failure'):
        runner.run()
    assert adapter.events[-2:] == ['cleanup', 'absence']
    assert adapter.events.count(failure) == 1
    state = json.loads((tmp_path / 'budget.json').read_bytes())
    assert state['halted']
    if failure in ('closure', 'structural', 'fixture', 'E4:offline-1'):
        assert state['lifetimes'] == 0
        assert not any(e.startswith('native:') for e in adapter.events)
    with pytest.raises(Stop, match='halted'):
        Budget(tmp_path, grant)


def test_bad_descendant_receipt_cannot_start_native(tmp_path, grant, monkeypatch):
    adapter = OfflineAdapter()
    original = adapter.isolation
    def invalid(*args):
        receipt, raw = original(*args)
        receipt['descendant_checks']['no_host_sockets'] = False
        return receipt, raw
    adapter.isolation = invalid
    runner, _, _ = workflow(tmp_path, grant, adapter, monkeypatch)
    with pytest.raises(Stop, match='descendant'):
        runner.run()
    assert 'native:1' not in adapter.events


def test_missing_acceptance_precedes_adapter_and_budget_access(grant):
    packet = {'scope': 'disposable-proof-preparation', 'execution_ready': True,
              'runtime_authorized': False, 'grant': grant}
    adapter = OfflineAdapter()
    with pytest.raises(Stop, match='acceptance'):
        run.Workflow(packet, run.Acceptance('e' * 64, 'offline', time.time() + 60), None, adapter, None)
    assert adapter.events == []
    packet['execution_ready'] = False
    with pytest.raises(Stop, match='preparation-only'):
        run.Acceptance(digest(canonical(packet)), 'offline', time.time() + 60).check(packet)


def test_cleanup_failure_invalidates_success(tmp_path, grant, monkeypatch):
    adapter = OfflineAdapter('absence')
    runner, _, journal = workflow(tmp_path, grant, adapter, monkeypatch)
    with pytest.raises(Stop, match='absence'):
        runner.run()
    assert json.loads((tmp_path / 'budget.json').read_bytes())['halted']
    assert b'cleanup_failure' in journal.getvalue()


def test_raw_receipt_bound_refuses_instead_of_truncating():
    stream = io.BytesIO()
    journal = run.Journal(stream, max_bytes=80)
    with pytest.raises(Stop, match='budget'):
        journal('oversized', raw='x' * 100)
    assert stream.getvalue() == b''


def test_outer_refusal_is_unknown_and_never_retried():
    import os
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, 'rb', buffering=0) as incoming, os.fdopen(write_fd, 'wb', buffering=0) as sender:
        sender.write(b'{"id":1,"error":"lost action"}\n')
        outgoing = io.BytesIO()
        channel = OuterControl(incoming, outgoing, lambda *a, **k: None)
        with pytest.raises(Stop, match='unknown'):
            channel.request('integration-action', {'case': 'planned'})
        first = outgoing.getvalue()
        with pytest.raises(Stop, match='failed'):
            channel.request('integration-action', {'case': 'planned'})
        assert outgoing.getvalue() == first


def test_two_pass_reader_detects_revision_and_item_changes():
    class Client:
        notifications = []
        revision = 1
        def request(self, method, params):
            if method == 'thread/read':
                return {'thread': {'id': 'offline-thread', 'status': {'type': 'idle'}}, 'readState': {'revision': self.revision}}
            ordinal = '1' if params['cursor'] is None else '2'
            item = {'id': method + '-' + ordinal, 'status': 'completed'}
            if method == 'thread/items/list':
                item = {'item': item, 'turnId': 'offline-turn'}
            return {'data': [item], 'nextCursor': 'two' if ordinal == '1' else None}
    class BudgetStub:
        def remaining_seconds(self): return 60
    client = Client()
    protocol = run.Protocol(None, BudgetStub(), None)
    one, two, stable = protocol.stable(client, {'id': 'offline-thread'})
    assert stable and len(two['items']) == 2
    _, _, stable = protocol.stable(client, {'id': 'offline-thread'}, lambda: setattr(client, 'revision', 2))
    assert not stable


@pytest.mark.parametrize('change', ['daemon', 'thread_id', 'worker_identity', 'history', 'consumed_keys', 'inject_receipt_keys'])
def test_p6_rejects_identity_history_and_exactly_once_drift(change):
    from tests.test_codex_managed_recovery import identity
    worker = dict(container_id='a' * 64, pid=124, start_ticks=101, boot_id='offline-boot',
                  started_at='offline-start', launcher_pid=126, launcher_start_ticks=103)
    before = {'daemon': identity(), 'thread_id': 'offline-thread',
              'worker_identity': worker,
              'history': ['turn-one'], 'accepted_keys': ['one', 'two']}
    after = {**before, 'worker_identity': dict(worker, container_id='b' * 64, pid=125, start_ticks=102),
             'history': ['turn-one', 'turn-two'], 'consumed_keys': ['one', 'two'], 'inject_receipt_keys': ['one', 'two']}
    after['activity_markers'] = ['offline input delivered']
    action = {'old_worker_absent': True, 'replacement_attested': True}
    run.check_integration('queue', 'crash', before, action, after)
    if change == 'worker_identity':
        after[change] = before[change]
    else:
        after[change] = []
    with pytest.raises(Stop):
        run.check_integration('queue', 'crash', before, action, after)


def test_goal_accounting_requires_observed_usage_and_nonregressing_time():
    before = {'objective': 'offline fixture goal', 'status': 'active', 'timeUsedSeconds': 1}
    after = {**before, 'tokensUsed': 15, 'timeUsedSeconds': 2}
    run.check_goal(before, after, 15)
    with pytest.raises(Stop):
        run.check_goal(before, after, 0)
    with pytest.raises(Stop):
        run.check_goal(before, dict(after, timeUsedSeconds=0), 15)

@pytest.mark.parametrize('failure', [None, 'duplicates', 'lost-write'])
def test_actual_key_protocol_conserves_setup_responses_and_rejects_uncertainty(tmp_path, grant, failure):
    """Stateful offline native adapter, not a stub returning proof verdicts."""
    from tests.test_codex_managed_recovery import identity
    budget = Budget(tmp_path, grant)
    budget.begin_lifetime()
    class Native:
        def __init__(self):
            self.turns, self.items, self.queued, self.clients = [], [], [], []
            self.held, self.followup, self.revision = False, False, 0
            self.races, self.dedupes = [], []
        def native_identity(self): return identity()
        def begin_case(self, name, ceiling): pass
        def phase(self, value): self.held = value == 'held'
        def fixture_completions(self):
            with budget._locked() as value: return value['completions']
        def client(self):
            owner = self
            class Client:
                notifications = []
                def initialize(self): return {'userAgent': 'offline 0.169.0'}
                def close(self): pass
                def wait_notification(self, method, predicate, **kwargs):
                    return {'method': method, 'params': {'threadId': 'thread', 'turn': owner.turns[-1]}}
                def request(self, method, params): return owner.request(self, method, params)
            client = Client()
            client.notifications = []
            self.clients.append(client)
            return client
        def notify(self, method):
            for client in self.clients: client.notifications.append({'method': method})
        def request(self, client, method, p):
            if method == 'thread/start': return {'thread': {'id': 'thread'}}
            if method in ('thread/read', 'thread/resume'):
                return {'thread': {'id': 'thread', 'path': '/state/rollout.jsonl',
                    'status': {'type': 'busy' if self.held else 'idle'}}, 'readState': self.revision}
            if method.endswith('/list'):
                assert p['limit'] == 1
                rows = self.items if method == 'thread/items/list' else self.turns if method == 'thread/turns/list' else self.queued
                if method != 'thread/queue/list': assert p['sortDirection'] == 'asc'
                offset = int(p['cursor'] or 0)
                return {'data': rows[offset:offset + 1], 'nextCursor': str(offset + 1) if offset + 1 < len(rows) else None}
            key = p['clientUserMessageId']
            known = any(row['item'].get('clientId') == key for row in self.items)
            if known and failure != 'duplicates':
                self.dedupes.append((method, key))
                return {'turn': self.turns[-1]}
            self.revision += 1
            if method == 'thread/queue/add' and self.held:
                self.queued.append({'clientUserMessageId': key})
                return {}
            if method == 'turn/steer':
                assert self.held and p['expectedTurnId'] == self.turns[-1]['id']
                self.followup = True
                turn = self.turns[-1]
            else:
                budget.completion()
                turn = {'id': 'turn-' + str(len(self.turns)), 'status': 'inProgress' if self.held else 'completed'}
                self.turns.append(turn)
                self.notify('turn/started')
            self.items.append({'turnId': turn['id'], 'item': {'id': 'item-' + str(len(self.items)),
                'type': 'userMessage', 'clientId': key}})
            self.notify('item/started')
            return {'turn': turn}
        def hold_for_method(self, c, t, method):
            self.phase('text' if method == 'turn/start' else 'held')
            if method == 'turn/start': return None
            return c.request('turn/start', {'clientUserMessageId': 'setup-' + str(len(self.turns))})['turn']['id']
        def settle(self, c, t):
            if self.followup:
                budget.completion()
                self.followup = False
            if self.turns: self.turns[-1]['status'] = 'completed'
            self.held = False
            queued, self.queued = self.queued, []
            for row in queued: self.request(c, 'turn/start', row)
        def ack_loss(self, frame):
            self.races.append(frame)
            if failure != 'lost-write': self.request(self.clients[-1], frame['method'], frame['params'])
    native = Native()
    protocol = run.Protocol(native, budget, lambda *a, **k: None)
    if failure:
        with pytest.raises(Stop, match='dedupe failed|remains unknown'):
            protocol.keys()
    else:
        result = protocol.keys()
        assert all(result['lookup_exact'].values()) and len(result['native_dedupe']) == 5
        assert len(native.races) == 6 and len(result['observations']) == 11
        assert native.fixture_completions() == 15
        assert len(native.dedupes) == 11
        assert result['negative_absent'] and result['intervening_change_detected']


def test_preparation_keeps_unknown_grant_expiry_unknown_and_execution_refuses(preparation):
    preparation['grant']['expires_at'] = None
    packet = prep.prepare(preparation)
    assert packet['grant']['expires_at'] is None and packet['execution_ready'] is False
    with pytest.raises(Stop, match='expiry'):
        prep.grant(packet['grant'])
