"""Offline coordinator negatives and an ordinary Python stdio worker.

Synthetic proof packets below test refusal/ordering, not native continuity.
No native daemon, provider, fixture server, root bootstrap or live restart.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.test_codex_managed_proof import packet
from ccc_server import execution_restart as restart
from ccc_server import managed_restart_control as control
from ccc_server import codex_restart_gate as gate
from ccc_server import codex_managed_recovery as recovery
from ccc_server.execution_artifact import ProcessIdentity, Refused, _verified_launch, canonical, digest
from ccc_server.execution_worker import worker_profile


@pytest.fixture
def harness(packet):
    manifest, evidence, _, _ = packet
    binary, (version, schema) = next(iter(recovery.SUPPORTED.items()))
    manifest['codex'] = {'version': version, 'binary_sha256': binary, 'schema_sha256': schema}
    identities = {role: ProcessIdentity(char * 64, pid, pid * 10, 'boot', 'test-start', pid + 100, pid * 10 + 100)
                  for role, char, pid in [('worker', 'a', 101), ('replacement', 'b', 102)]}
    authority = ProcessIdentity('c' * 64, 103, 1030, 'boot', 'test-start')
    facts = {role: _verified_launch(identity, 'sha256:' + 'd' * 64, '1' * 64, '2' * 64, '')
             for role, identity in identities.items()}
    raw = canonical(manifest)
    anchor = {'format': 'ccc-root-authority-v2', 'image_id': 'sha256:' + 'd' * 64,
              'executed_build_sha256': '1' * 64, 'execution_profile_sha256': '2' * 64,
              'managed_acceptance': {'manifest_sha256': digest(raw), 'reference': 'synthetic offline acceptance'}}
    daemon = {'pid': 201, 'start_ticks': 2010, 'boot_id': 'boot', 'socket_device': 1,
              'socket_inode': 2, 'directory_device': 1, 'directory_inode': 3,
              'binary_sha256': binary, 'version': version, 'protocol_sha256': schema}
    snapshot = {'worker': {'pid': 101, 'start_ticks': 1010, 'boot_id': 'boot'}, 'worker_epoch': 'epoch',
                'daemon': daemon, 'daemon_epoch': recovery._sha(daemon),
                'threads': {'thread-a': {'active_turn_ids': ['turn-a'], 'queue_ids': ['queue-a'],
                                         'goal_state': 'active', 'claims': []}}}
    events, mutate = [], {}
    class Verifier:
        processes = SimpleNamespace(stopped=lambda _: not mutate.get('already-running'))
        def observe(self, ident, profile, expected):
            role = next(r for r, i in identities.items() if i.container_id == ident)
            events.append(('observe', role))
            result = facts[role]
            if mutate.get('drift') and sum(e == ('observe', 'worker') for e in events) >= 3:
                return _verified_launch(result.identity, result.image_id, '9' * 64, '2' * 64, '')
            return result
        def release(self, fact, profile):
            events.append(('release', fact.identity.container_id))
    class Docker:
        def control(self, ident, request):
            operation = request['operation']
            events.append(('control', operation))
            if mutate.get('uncertain') == operation:
                raise Refused('uncertain reply')
            stage = {'begin': 'captured', 'final': 'sealed', 'abort': 'aborted',
                     'ready': 'sealed', 'resume': 'resumed'}[operation]
            value = {'stage': stage, 'lease': 'lease', 'transition_id': request['transition_id'],
                     'manifest_sha256': request['manifest_sha256'], 'observation': copy.deepcopy(snapshot)}
            if operation in ('ready', 'resume'):
                value['observation']['worker'] = {'pid': 102, 'start_ticks': 1020, 'boot_id': 'boot'}
            if operation == 'final' and mutate.get('epoch'):
                value['observation']['worker_epoch'] = 'changed'
            if operation == 'ready' and mutate.get('replacement-daemon'):
                value['observation']['daemon']['socket_inode'] += 1
            if operation == 'ready' and mutate.get('replacement-worker'):
                value['observation']['worker']['start_ticks'] += 1
            return value
        def stop_exact(self, fact):
            events.append(('stop', fact.identity.container_id))
    profiles = {r: worker_profile('/private/state', '1001:1001', control_stdio=True) for r in identities}
    coordinator = restart.Coordinator(anchor, authority, Docker(), Verifier(), profiles, identities,
        lambda: authority, lambda: anchor)
    return SimpleNamespace(coordinator=coordinator, manifest=manifest, raw=raw, evidence=evidence,
                           events=events, mutate=mutate, anchor=anchor, snapshot=snapshot, identities=identities)


def test_root_coordinator_orders_two_freeze_observations_stop_and_admission(harness):
    result = harness.coordinator.run(harness.raw, harness.evidence)
    assert result['ok'] and result['restarted']
    events = harness.events
    assert events.index(('control', 'final')) < events.index(('stop', 'a' * 64))
    assert events.index(('stop', 'a' * 64)) < events.index(('release', 'b' * 64))
    assert events.index(('release', 'b' * 64)) < events.index(('control', 'ready'))
    assert events[-1] == ('control', 'resume')
    assert events.count(('observe', 'worker')) == 3


@pytest.mark.parametrize('failure', ['acceptance', 'fixture', 'missing-proof', 'evidence', 'control',
    'already-running', 'drift', 'epoch', 'goal', 'outstanding', 'native', 'unknown-thread'])
def test_unproven_initial_or_final_inputs_never_stop_or_release(harness, failure):
    h = harness
    if failure == 'acceptance':
        h.anchor['managed_acceptance']['manifest_sha256'] = '0' * 64
    elif failure == 'fixture':
        h.manifest['scope'] = 'fixture'
    elif failure == 'missing-proof':
        del h.manifest['proofs']['P6']
    elif failure == 'evidence':
        h.evidence.clear()
    elif failure == 'control':
        h.anchor['execution_profile_sha256'] = '9' * 64
    elif failure in ('already-running', 'drift', 'epoch'):
        h.mutate[failure] = True
    elif failure == 'goal':
        h.snapshot['threads']['thread-a']['goal_state'] = 'budgetLimited'
    elif failure == 'outstanding':
        h.snapshot['threads']['thread-a']['claims'] = [{'phase': 'send_intent'}]
    elif failure == 'native':
        h.snapshot['daemon']['binary_sha256'] = '0' * 64
    else:
        h.snapshot['threads']['thread-a']['unexpected'] = True
    if failure in ('fixture', 'missing-proof'):
        h.raw = canonical(h.manifest)
        h.anchor['managed_acceptance']['manifest_sha256'] = digest(h.raw)
    with pytest.raises((Refused, ValueError, OSError)):
        h.coordinator.run(h.raw, h.evidence)
    assert not any(e[0] in ('stop', 'release') for e in h.events)


@pytest.mark.parametrize('operation', ['begin', 'final'])
def test_uncertain_freeze_reply_is_not_retried_or_treated_as_a_failed_write(harness, operation):
    harness.mutate['uncertain'] = operation
    with pytest.raises(Refused, match='uncertain'):
        harness.coordinator.run(harness.raw, harness.evidence)
    assert harness.events.count(('control', operation)) == 1
    assert not any(e[0] in ('stop', 'release') for e in harness.events)
    if operation == 'final':
        assert ('control', 'abort') not in harness.events


def test_changed_root_acceptance_at_final_barrier_keeps_replacement_stopped(harness):
    calls = []
    def anchor():
        calls.append(1)
        return harness.anchor if len(calls) < 6 else dict(harness.anchor, format='changed')
    harness.coordinator.read_anchor = anchor
    with pytest.raises(Refused, match='acceptance changed'):
        harness.coordinator.run(harness.raw, harness.evidence)
    assert not any(e[0] in ('stop', 'release') for e in harness.events)


def test_missing_root_acceptance_cannot_be_replaced_by_a_json_boolean(harness):
    harness.anchor.pop('managed_acceptance')
    with pytest.raises(Refused, match='not accepted'):
        harness.coordinator.run(harness.raw, harness.evidence)
    assert harness.events == []


def test_non_stdio_profile_and_aliased_role_identities_refuse(harness):
    harness.coordinator.profiles['worker'] = worker_profile('/private/state', '1001:1001')
    with pytest.raises(Refused, match='fixed measured'):
        harness.coordinator.run(harness.raw, harness.evidence)
    assert harness.events == []


def test_worker_and_replacement_cannot_alias_the_same_application(harness):
    harness.coordinator.identities['replacement'] = harness.coordinator.identities['worker']
    with pytest.raises(Refused, match='not distinct'):
        harness.coordinator.run(harness.raw, harness.evidence)
    assert not any(e[0] in ('stop', 'release') for e in harness.events)


def test_root_acceptance_change_during_final_validation_refuses_exact_stop(harness, monkeypatch):
    calls = []
    validate = harness.coordinator._validate
    changed = False
    def check(*args):
        nonlocal changed
        result = validate(*args)
        calls.append(1)
        if len(calls) == 2:
            changed = True
        return result
    monkeypatch.setattr(harness.coordinator, '_validate', check)
    harness.coordinator.read_anchor = lambda: dict(harness.anchor, format='changed') if changed else harness.anchor
    with pytest.raises(Refused, match='before exact stop'):
        harness.coordinator.run(harness.raw, harness.evidence)
    assert not any(e[0] in ('stop', 'release') for e in harness.events)


def test_replacement_observation_must_match_its_actual_application_before_thaw(harness):
    harness.mutate['replacement-worker'] = True
    with pytest.raises(Refused, match='snapshot identity mismatch'):
        harness.coordinator.run(harness.raw, harness.evidence)
    assert ('control', 'resume') not in harness.events


def test_stdio_protocol_cannot_dispatch_arbitrary_worker_methods():
    request = {'id': 'a' * 32, 'request': {'operation': 'engine.execute'}}
    result = io.StringIO()
    control.serve_stdio(SimpleNamespace(), io.StringIO(json.dumps(request) + '\n'), result)
    value = json.loads(result.getvalue()[len(control.PREFIX):])
    assert value['id'] == request['id'] and value['ok'] is False


def test_stdio_protocol_discards_oversized_request_without_parsing_its_tail():
    result = io.StringIO()
    control.serve_stdio(SimpleNamespace(), io.StringIO('x' * (control.MAX_LINE + 1) + '\n'), result)
    assert result.getvalue() == ''


@pytest.fixture
def clean_gate(monkeypatch, tmp_path):
    import server
    monkeypatch.setattr(gate, '_owner', '')
    monkeypatch.setattr(gate, '_committed', False)
    monkeypatch.setattr(gate, '_authorities', [])
    monkeypatch.setattr(gate, '_frozen_until', 0)
    monkeypatch.setattr(gate, '_fence_path', str(tmp_path / 'fence.json'))
    return server


def test_managed_capture_is_not_a_legacy_restart_permission(clean_gate):
    value = gate.capture_managed(lambda: ({'fact': 'offline'}, lambda: True))
    assert gate.frozen() and gate.blocks('turn/start')
    assert not value.get('frozen') and not value.get('committed')
    gate.release(value['lease'])


def test_expired_freeze_cannot_be_sealed(clean_gate, monkeypatch):
    captured = gate.capture_managed(lambda: ({}, lambda: True))
    monkeypatch.setattr(gate, '_frozen_until', gate._now() - 1)
    with pytest.raises(ValueError, match='lease_lost'):
        gate.capture_managed(lambda: ({}, lambda: True), token=captured['lease'], seal=True)
    assert not gate.status()['committed']


def test_revision_drift_and_missing_durable_fence_cannot_be_sealed(clean_gate, monkeypatch):
    captured = gate.capture_managed(lambda: ({}, lambda: True))
    with pytest.raises(ValueError, match='changed'):
        gate.capture_managed(lambda: ({}, lambda: False), token=captured['lease'], seal=True)
    captured = gate.capture_managed(lambda: ({}, lambda: True))
    monkeypatch.setattr(gate, '_fence_path', None)
    with pytest.raises(ValueError, match='durable'):
        gate.capture_managed(lambda: ({}, lambda: True), token=captured['lease'], seal=True)


def test_observation_permission_is_fixed_thread_local_and_restored(clean_gate):
    captured = gate.capture_managed(lambda: ({}, lambda: True))
    with gate.managed_observation():
        assert not gate.blocks('thread/goal/get')
        assert gate.blocks('turn/start') and gate.blocks('thread/resume')
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(gate.blocks, 'thread/goal/get').result()
    assert gate.blocks('thread/goal/get')
    gate.release(captured['lease'])


def test_actual_ordinary_python_worker_serves_fixed_stdio_protocol(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, CCC_STATE_DIR=str(tmp_path), CCC_MEMORY_RELEASE='0', HOME=str(tmp_path))
    process = subprocess.Popen([sys.executable, '-B', str(root / 'ccc_worker.py'), '--socket',
        str(tmp_path / 'worker.sock'), '--managed-control-stdio'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, cwd=tmp_path, text=True)
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(15), 'ordinary worker did not start'
        assert 'CCC worker running' in process.stdout.readline()
        process.stdin.write(json.dumps({'id': 'a' * 32, 'request': {'operation': 'engine.execute'}}) + '\n')
        process.stdin.flush()
        assert selector.select(15), 'ordinary worker did not reject fixed protocol request'
        line = process.stdout.readline()
        assert line.startswith(control.PREFIX), line
        assert json.loads(line[len(control.PREFIX):])['ok'] is False
        assert process.poll() is None
        selector.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)


@pytest.fixture
def worker_control(clean_gate, monkeypatch, tmp_path):
    from control_plane import WorkLedger
    monkeypatch.setenv('CCC_STATE_DIR', str(tmp_path))
    runtime = SimpleNamespace(epoch='ordinary-python-epoch', ledger=WorkLedger(tmp_path / 'ledger.sqlite3'))
    observation = {'worker': recovery.process_identity(), 'worker_epoch': runtime.epoch,
                   'daemon': {}, 'daemon_epoch': 'a' * 64, 'threads': {}}
    monkeypatch.setattr(control, 'native_snapshot', lambda _: (copy.deepcopy(observation), lambda: True))
    request = {'operation': 'begin', 'transition_id': 'a' * 32, 'manifest_sha256': 'b' * 64,
               'owner_pid': os.getpid(), 'goal_states': [], 'receipt_sha256': None}
    return control.WorkerControl(runtime), request, observation


def test_real_ledger_and_fence_keep_sealed_receipts_across_replay(worker_control):
    worker, request, _ = worker_control
    first = worker.dispatch(request)
    assert worker.runtime.ledger.drain_state()['enabled'] and gate.frozen()
    final_request = dict(request, operation='final', receipt_sha256=digest(canonical(first)))
    final = worker.dispatch(final_request)
    assert final['stage'] == 'sealed'
    assert gate.status()['committed']
    assert worker._load() == final
    with pytest.raises(Refused, match='receipt changed'):
        worker.dispatch(final_request)
    with pytest.raises(Refused, match='sealed'):
        worker.dispatch(dict(final_request, operation='abort', receipt_sha256=digest(canonical(final))))
    with pytest.raises(Refused, match='prior-worker death'):
        worker.dispatch(dict(final_request, operation='ready', receipt_sha256=digest(canonical(final))))
    assert worker.runtime.ledger.drain_state()['enabled'] and gate.frozen()


def test_mutated_receipt_and_foreign_transaction_refuse_before_seal(worker_control):
    worker, request, _ = worker_control
    first = worker.dispatch(request)
    final = dict(request, operation='final', receipt_sha256=digest(canonical(first)))
    with pytest.raises(Refused, match='different transition'):
        worker.dispatch(dict(final, transition_id='c' * 32))
    changed = dict(first, goal_states=['active'])
    worker._save(changed)
    with pytest.raises(Refused, match='receipt changed'):
        worker.dispatch(final)
    assert not gate.status()['committed']


def test_unproven_goal_or_expired_freeze_cannot_persist_seal(worker_control, monkeypatch):
    worker, request, observation = worker_control
    first = worker.dispatch(request)
    observation['threads']['thread'] = {'goal_state': 'budgetLimited'}
    with pytest.raises(Refused, match='unproven class'):
        worker.dispatch(dict(request, operation='final', goal_states=['active'],
                             receipt_sha256=digest(canonical(first))))
    assert worker._load()['stage'] == 'captured' and not gate.status()['committed']


def test_unsealed_abort_releases_only_its_own_ledger_drain(worker_control):
    worker, request, _ = worker_control
    first = worker.dispatch(request)
    newer = worker.runtime.ledger.set_drain(True, 'other-owner')
    worker.dispatch(dict(request, operation='abort', receipt_sha256=digest(canonical(first))))
    assert worker.runtime.ledger.drain_state() == newer
    assert not gate.frozen()


@pytest.fixture
def native_context(clean_gate, monkeypatch, tmp_path, harness):
    server = clean_gate
    from ccc_server import codex
    from ccc_server.codex_requests import CODEX_REQUESTS
    monkeypatch.setenv('CCC_WORKER_PROCESS', '1')
    monkeypatch.setattr(server, '_read_registry_pruned', lambda: [])
    monkeypatch.setattr(server, 'CODEX_APP_SERVER_STATE_FILE', tmp_path / 'state.json')
    monkeypatch.setattr(server, 'PENDING_INPUT_HANDOFF_DIR', tmp_path / 'handoffs')
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_THREAD_STATE', {})
    monkeypatch.setattr(codex, '_CODEX_APP_SERVER_MUTATING_PENDING', {})
    monkeypatch.setattr(CODEX_REQUESTS, 'snapshot', lambda: {'requests': []})
    daemon = copy.deepcopy(harness.snapshot['daemon'])
    transport = SimpleNamespace(kind='managed-unix', alive=lambda: True, managed_recovering=False,
        managed_identity=daemon, managed_epoch=recovery._sha(daemon), managed_revisions={},
        managed_subscriptions={'thread-a'}, sock=None, managed_socket_path='/synthetic/socket')
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_TRANSPORT', transport)
    monkeypatch.setattr(recovery, 'observe_peer', lambda *_: copy.deepcopy(daemon))
    with recovery._state(True) as state:
        state['daemon'] = daemon
        state['threads']['thread-a'] = {'epoch': transport.managed_epoch, 'status': 'ready', 'path': '/synthetic/rollout'}
    calls = []
    def rpc(_transport, method, params, timeout):
        calls.append(method)
        if method == 'thread/loaded/list':
            result = {'data': ['thread-a']}
        elif method == 'thread/turns/list':
            result = {'data': [{'id': 'turn-a', 'status': 'inProgress'}]}
        elif method == 'thread/queue/list':
            result = {'data': [{'id': 'queue-a'}]}
        elif method == 'thread/goal/get':
            result = {'goal': {'status': 'active'}}
        else:
            raise AssertionError(method)
        return {'result': result}
    monkeypatch.setattr(server, '_codex_app_server_request_to_transport', rpc)
    return SimpleNamespace(server=server, transport=transport, calls=calls, codex=codex, requests=CODEX_REQUESTS)


def test_native_snapshot_reads_real_boundaries_and_rejects_later_revision(native_context):
    observation, check = control.native_snapshot(SimpleNamespace(epoch='worker-epoch'))
    assert observation['threads']['thread-a']['queue_ids'] == ['queue-a']
    assert observation['threads']['thread-a']['active_turn_ids'] == ['turn-a']
    assert observation['threads']['thread-a']['goal_state'] == 'active'
    assert native_context.calls == ['thread/loaded/list', 'thread/turns/list', 'thread/queue/list', 'thread/goal/get']
    assert check()
    native_context.transport.managed_revisions['thread-a'] = 1
    assert not check()


@pytest.mark.parametrize('failure', ['recovering', 'stdio', 'pending', 'approval', 'epoch', 'unknown-peer'])
def test_native_snapshot_never_infers_unknown_state_is_idle(native_context, monkeypatch, failure):
    c = native_context
    if failure == 'recovering':
        c.transport.managed_recovering = True
    elif failure == 'stdio':
        c.transport.kind = 'stdio'
    elif failure == 'pending':
        c.codex._CODEX_APP_SERVER_MUTATING_PENDING[1] = ('turn/start', c.transport)
    elif failure == 'approval':
        c.server._CODEX_APP_SERVER_THREAD_STATE['thread-a'] = {'thread_needs_approval': True}
    elif failure == 'epoch':
        c.transport.managed_epoch = '0' * 64
    else:
        monkeypatch.setattr(recovery, 'observe_peer', lambda *_: {})
    with pytest.raises(Refused):
        control.native_snapshot(SimpleNamespace(epoch='worker-epoch'))


def test_measured_stdio_profiles_do_not_grant_root_sockets_or_mutable_config():
    from ccc_server.execution_authority import _profile
    from ccc_server.execution_guard import SourceGuard
    profile = worker_profile('/private/state', '1001:1001', control_stdio=True,
                             managed_socket='/state/native/control.sock')
    assert _profile(profile.value()).value() == profile.value()
    assert '/var/run/docker.sock' not in profile.unix_sockets
    assert profile.commands == ()
    assert dict(profile.environment)['CCC_CODEX_APP_SERVER_SOCKET'] == '/state/native/control.sock'
    guard = SourceGuard((), managed_control=True)
    guard.audit('open', ('/state/pending-input-handoffs/claim-recovery/' + 'a' * 64 + '.json', 'r', os.O_RDONLY))
    guard.audit('open', ('/state/codex-restart-fence.json.done.' + 'a' * 32, 'r', os.O_RDONLY))
    guard.audit('open', ('/state', 'r', os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)))
    for filename in ('/state/config.json', '/state/settings.json',
                     '/state/pending-input-handoffs/claim-recovery/plugin.py',
                     '/state/pending-input-handoffs/claim-recovery/unknown.json'):
        with pytest.raises(RuntimeError, match='undeclared mutable'):
            guard.audit('open', (filename, 'r', os.O_RDONLY))
    with pytest.raises(Refused):
        worker_profile('/private/state', '1001:1001', control_stdio=True, managed_socket='/var/run/docker.sock')


def test_cli_unknown_anchor_refuses_without_creating_an_authority(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / 'scripts/managed_restart_control.py')],
        input=json.dumps({'anchor_path': str(tmp_path / 'absent'), 'request': {}}),
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    value = json.loads(result.stdout)
    assert value['outcome'] == 'unknown' and value['fallback'] == 'queue'
    assert list(tmp_path.iterdir()) == []


def test_real_post_handler_reports_unproven_outcome_without_legacy_kill(monkeypatch, tmp_path):
    import server
    body = canonical({'anchor_path': str(tmp_path / 'missing'), 'request': {}})
    handler = server.CommandCenterHandler.__new__(server.CommandCenterHandler)
    handler.path = '/api/restart/managed'
    handler.headers = {'Content-Length': str(len(body))}
    handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
    handler._check_proxy_secret = handler._check_same_origin = lambda: True
    handler._phone_pin_blocked = lambda _: False
    replies = []
    handler.send_json = lambda *args: replies.append(args)
    def forbidden(*args, **kwargs):
        raise AssertionError('legacy service kill must not run')
    monkeypatch.setattr(server, '_restart_worker_process', forbidden)
    server.CommandCenterHandler.do_POST(handler)
    assert replies[0][1] == 409
    assert replies[0][0]['outcome'] == 'unknown'
    assert replies[0][0]['code'] == 'codex_restart_pending'
    assert list(tmp_path.iterdir()) == []


def test_actual_source_guard_can_import_and_reject_fixed_protocol_without_proof_codegen():
    root = Path(__file__).resolve().parents[1]
    program = f'''
import sys
sys.path.insert(0,{str(root)!r})
from ccc_server import execution_guard
from ccc_server.execution_artifact import Refused
execution_guard.EXCLUDED=('/state','/proc','/sys')
generated=execution_guard.measured_generated_sources((('<catalog-v1>',{str(root / 'ccc_server/execution_generated_sources.json')!r}),))
guard=execution_guard.SourceGuard((), generated_sources=generated)
guard.install()
from ccc_server.managed_restart_control import WorkerControl
try:
 WorkerControl(None).dispatch({{'operation':'engine.execute','transition_id':'a'*32,
 'manifest_sha256':'b'*64,'owner_pid':1,'goal_states':[],'receipt_sha256':None}})
except Refused as error:
 assert str(error)=='unsupported fixed managed operation', str(error)
else:
 raise AssertionError('generic dispatch accepted')
assert 'ccc_server.codex_managed_proof' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', program],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_generic_client_release_cannot_admit_managed_recovery_without_proof(tmp_path):
    from ccc_server.execution_authority import AttestationClient
    profile = worker_profile('/private/state', '1001:1001', control_stdio=True)
    with pytest.raises(Refused, match='accepted root coordinator'):
        AttestationClient(tmp_path / 'absent').release(None, profile)
    assert list(tmp_path.iterdir()) == []


def test_root_generic_release_rejects_managed_profile_before_observing_json_claim(monkeypatch):
    from ccc_server import execution_authority as authority
    profile = worker_profile('/private/state', '1001:1001', control_stdio=True)
    monkeypatch.setattr(authority, 'read_anchor', lambda _: {})
    monkeypatch.setattr(authority, 'RootDocker', lambda: object())
    monkeypatch.setattr(authority, 'LinuxProcesses', lambda: object())
    monkeypatch.setattr(authority, 'authenticate_self', lambda *_: object())
    monkeypatch.setenv('CCC_ATTESTATION_QUERY', json.dumps({'operation': 'release', 'fact': {}, 'profile': profile.value()}))
    with pytest.raises(Refused, match='accepted root coordinator'):
        authority.main()
