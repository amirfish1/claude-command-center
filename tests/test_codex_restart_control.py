"""Offline controller and root probe checks; never launch native or a service."""
import base64
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from ccc_server.execution_artifact import canonical, digest, Refused, ProcessIdentity
from ccc_server.execution_authority import AuthorityRefused
from ccc_server import execution_proof_probe as probe
from scripts import codex_restart_bridge as relay
from scripts import codex_restart_preflight as prep
from scripts.codex_restart_fixture import Stop
from scripts.codex_restart_p6 import Scenarios
from tests.test_codex_restart_runner import preparation, grant


@pytest.fixture
def launch(preparation, monkeypatch):
    packet = prep.prepare(preparation)
    packet.update(execution_ready=True, run_id='offline-only')
    packet['actual']['host_netns'] = 'host-network'
    anchor = {'format': probe.FORMAT, 'image_id': packet['images']['control']['image_id'],
              'executed_build_sha256': packet['images']['control']['artifact_sha256'],
              'execution_acceptance': {'manifest_sha256': digest(canonical(packet)), 'reference': 'offline only'}}
    config = packet['harness_launch']
    container = {'Id': 'a' * 64, 'Image': packet['images']['native']['image_id'],
        'Config': {k: v for k, v in config.items() if k not in ('HostConfig', 'Image')},
        'HostConfig': copy.deepcopy(config['HostConfig']), 'State': {'Running': True, 'Pid': 123},
        'Mounts': [{'Destination': '/etc', 'Type': 'image', 'RW': False},
                   {'Destination': '/state', 'Type': 'bind', 'RW': True, 'Source': packet['state_root']}]}
    artifact = SimpleNamespace(executed_build_sha256=packet['images']['native']['artifact_sha256'],
                              manifest=Path(preparation['images']['native']['manifest']).read_bytes())
    identity = ProcessIdentity('a' * 64, 123, 100, '00000000-0000-4000-8000-000000000001', '2026-01-01T00:00:00Z', 123, 100)
    class Docker:
        def inspect_container(self, name):
            assert name == 'a' * 64
            return container
        def measure(self, image): return artifact
        def verify_runtime_etc(self, *_): pass
    class Processes:
        def identity(self, value): return identity
        def security(self, value):
            return {'Uid': '65532 65532 65532 65532', 'NoNewPrivs': '1',
                    **dict.fromkeys(('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'), '0')}
        def mounts(self, value): return {'/': {'ro'}, '/etc': {'ro'}, '/state': {'noexec', 'nodev', 'nosuid'}}
        def network_namespace(self, value): return 'private-network'
        def environment(self, value): return config['Env']
        def executable(self, value): return 'c' * 64
    monkeypatch.setattr(probe.os, 'readlink', lambda name: 'host-network')
    value = SimpleNamespace(packet=packet, anchor=anchor, container=container, artifact=artifact,
                            docker=Docker(), processes=Processes(), identity=identity)
    def run():
        value.anchor['execution_acceptance']['manifest_sha256'] = digest(canonical(value.packet))
        return probe.observe({'packet': base64.b64encode(canonical(value.packet)).decode(),
            'container_id': 'a' * 64, 'worker': None, 'native': None}, anchor=value.anchor,
            docker=value.docker, processes=value.processes)
    value.run = run
    return value


def test_root_probe_observes_actual_scope_and_kernel_profile(launch):
    result = launch.run()
    assert result['scope'] == 'proof-observation' and result['runtime_authorized'] is False
    assert result['native_netns'] == 'private-network'
    assert result['harness'] == vars(launch.identity)
    assert result['measured_profile_sha256'] != launch.packet['requested_profile_sha256']


@pytest.mark.parametrize('change', ['scope', 'runtime', 'not-ready', 'rebuild', 'source',
    'image', 'rootfs', 'environment', 'namespace', 'root', 'cap', 'interpreter', 'mount', 'host-socket'])
def test_root_probe_refuses_drift_before_any_action(launch, change):
    p, c = launch.packet, launch.container
    if change == 'scope': p['scope'] = 'runtime'
    elif change == 'runtime': p['runtime_authorized'] = True
    elif change == 'not-ready': p['execution_ready'] = False
    elif change == 'rebuild': p['rebuild_required'] = ['unmeasured']
    elif change == 'source': p['source_sha256'].pop(next(iter(p['source_sha256'])))
    elif change == 'image': c['Image'] = 'sha256:' + '9' * 64
    elif change == 'rootfs': launch.artifact.executed_build_sha256 = '9' * 64
    elif change == 'environment': c['Config']['Env'] = ['HOME=/host']
    elif change == 'namespace': launch.processes.network_namespace = lambda _: 'host-network'
    elif change == 'root': launch.processes.security = lambda _: {'Uid': '0 0 0 0'}
    elif change == 'cap': c['HostConfig']['CapAdd'] = ['SYS_ADMIN']
    elif change == 'interpreter': launch.processes.executable = lambda _: '9' * 64
    elif change == 'mount': launch.processes.mounts = lambda _: {'/': {'ro'}, '/etc': {'ro'}, '/state': set()}
    elif change == 'host-socket': c['Mounts'].append({'Destination': '/run/docker.sock', 'Type': 'bind', 'RW': True})
    with pytest.raises((Refused, Stop)):
        launch.run()


def test_root_probe_independent_acceptance_is_mandatory(launch):
    launch.anchor['execution_acceptance']['manifest_sha256'] = '0' * 64
    with pytest.raises(Refused, match='accepted'):
        probe.observe({'packet': base64.b64encode(canonical(launch.packet)).decode(),
            'container_id': 'a' * 64, 'worker': None, 'native': None}, anchor=launch.anchor,
            docker=launch.docker, processes=launch.processes)


@pytest.fixture
def controller(launch):
    p = launch.packet
    identity = vars(ProcessIdentity('b' * 64, 124, 101, 'offline-boot', 'start', 125, 102))
    profile = p['requested_worker_profile']
    old = {'role': 'worker', 'profile': profile, 'identity': identity}
    new = {'role': 'replacement', 'profile': profile, 'identity': dict(identity, container_id='c' * 64, pid=126)}
    c = {'format': 'ccc-disposable-proof-controller-v1', 'packet': p, 'packet_sha256': digest(canonical(p)),
        'harness_container_id': 'a' * 64, 'proof_anchor_path': 'offline-proof', 'fixture_anchor_path': 'offline-fixture',
        'roles': {'worker': old, 'replacement': new}, 'owned_roles': [old, new],
        'scenarios': {'corrupt/refusal': {'old': old, 'replacement': new, 'request': {},
                                      'expected_refusal': 'corrupt managed recovery state'}}}
    calls, deleted = [], []
    class Client:
        failure = None
        def __init__(self, anchor): self.anchor = anchor
        def observe_proof_harness(self, raw, ident, **kwargs):
            calls.append(('observe-proof', kwargs))
            assert digest(raw) == c['packet_sha256']
            return {'scope': 'proof-observation', 'runtime_authorized': False, 'images': p['images'],
                'source_sha256': p['source_sha256'], 'native_netns': 'private-network',
                'accepted_packet_sha256': c['packet_sha256'], 'reference': 'offline only'}
        def fixture_restart(self, query):
            calls.append(('restart', query))
            raise self.failure or Refused('managed control reply uncertain; do not retry')
        def cleanup_proof_workers(self, raw, ident, roles):
            calls.append(('root-cleanup', [r['identity']['container_id'] for r in roles]))
            return {'removed': [r['identity']['container_id'] for r in roles], 'runtime_authorized': False}
        def observe(self, ident, profile, expected):
            calls.append(('observe', ident))
            return SimpleNamespace(identity=expected)
    class Docker:
        def request(self, method, endpoint):
            calls.append((method, endpoint))
            if method == 'GET': return []
            deleted.append(endpoint)
        def stop_exact(self, fact): pytest.fail('absent container was stopped')
    bridge = relay.Bridge('offline-controller', read=lambda _: c, client=Client, docker=Docker())
    return SimpleNamespace(bridge=bridge, contract=c, calls=calls, client=Client, deleted=deleted)


def test_relay_never_retries_or_converts_lost_root_reply_to_refusal(controller):
    message = {'id': 1, 'operation': 'integration-action', 'data': {'case': 'corrupt', 'path': 'refusal'}}
    with pytest.raises(Refused, match='uncertain'):
        controller.bridge.handle(message)
    assert [c[0] for c in controller.calls] == ['restart']
    with pytest.raises(Stop, match='sequence'):
        controller.bridge.handle(dict(message, id=2))
    assert len(controller.calls) == 1


def test_relay_only_confirms_specific_observed_refusal_and_unchanged_exact_worker(controller):
    controller.client.failure = AuthorityRefused('immutable authority refused: measured worker refused: corrupt managed recovery state')
    result = controller.bridge.handle({'id': 1, 'operation': 'integration-action',
        'data': {'case': 'corrupt', 'path': 'refusal'}})['result']
    assert result['refused'] is True and result['killed'] is False
    assert [c[0] for c in controller.calls] == ['restart', 'observe']


def test_relay_nonzero_exit_is_not_automatically_a_safe_refusal(controller):
    controller.client.failure = AuthorityRefused('immutable authority refused: managed control reply uncertain; do not retry')
    with pytest.raises(Stop, match='expected measured refusal'):
        controller.bridge.handle({'id': 1, 'operation': 'integration-action',
            'data': {'case': 'corrupt', 'path': 'refusal'}})
    assert len(controller.calls) == 1


def test_cleanup_uses_only_root_contract_full_ids_and_observed_absence(controller):
    result = controller.bridge.dispatch('cleanup-outer', {'run_id': 'offline-only'})
    assert result['owned_roles_stopped'] is True
    result = controller.bridge.dispatch('verify-cleanup', {'run_id': 'offline-only'})
    assert result['owned_containers_absent'] is True
    assert controller.bridge.deleted == {'b' * 64, 'c' * 64}
    assert controller.deleted == []


def test_relay_rejects_changed_packet_and_replayed_sequence(controller):
    b = controller.bridge
    b.handle({'id': 1, 'operation': 'launch-acceptance', 'data': {'packet_sha256': controller.contract['packet_sha256']}})
    with pytest.raises(Stop, match='sequence'):
        b.handle({'id': 1, 'operation': 'launch-acceptance', 'data': {}})
    controller.contract['packet']['user'] = '0:0'
    controller.contract['packet_sha256'] = digest(canonical(controller.contract['packet']))
    with pytest.raises(Stop, match='mid-run'):
        b.dispatch('attest-closure', {})


def test_missing_inject_receipts_never_copy_expected_keys_into_p6_success(tmp_path):
    native = {'items': [{'id': 'native-item', 'type': 'userMessage', 'clientId': 'offline-input'}],
              'turns': [{'id': 'offline-turn', 'status': 'completed'}], 'queue': [], 'thread': {}}
    class Worker:
        def call(self, method, params):
            if params['operation'] == 'thread_states':
                return {'states': {'offline-thread': {'status': 'idle'}}}
            return {'status': {'active_item': None}}
    h = SimpleNamespace(budget=None, record=lambda *a, **k: None, root=tmp_path,
                        native_identity=lambda: {'pid': 123, 'start_ticks': 100}, begin_case=lambda *a: None)
    scenarios = Scenarios(h)
    scenarios.protocol.snapshot = lambda *a: native
    scenarios.current = {'thread': {'id': 'offline-thread'}, 'client': None, 'worker': Worker(),
        'fact': {'identity': {'pid': 124, 'start_ticks': 101}}, 'keys': ['offline-input'],
        'turn': 'offline-turn', 'case': 'ack-loss'}
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs/activity.log').write_text('offline-thread input observed\n')
    result = scenarios.snapshot(True)
    assert result['consumed_keys'] == ['offline-input']
    assert result['inject_receipt_keys'] == []
    assert result['ui_terminal'] == 'completed'
    (tmp_path / 'inject-receipts.json').write_bytes(canonical({'_landed': [
        {'sid': 'offline-thread', 'inject_id': 'offline-input'}]}))
    assert scenarios.snapshot(True)['inject_receipt_keys'] == ['offline-input']


def test_requested_binding_preserves_closed_policy_and_no_kernel_facts(launch):
    profiles = prep.bound_profiles(launch.packet, 'a' * 64)
    assert profiles['worker'].network == 'container:' + 'a' * 64
    assert profiles['replacement'] == profiles['worker']
    assert launch.packet['actual']['native_identity'] is None
    with pytest.raises(Stop, match='exact container'):
        prep.bound_profiles(launch.packet, 'short')
    launch.packet['requested_worker_profile']['commands'] = [['/bin/sh', 'unbounded']]
    with pytest.raises(Stop, match='policy drift'):
        prep.bound_profiles(launch.packet, 'a' * 64)


@pytest.mark.parametrize('change', [None, 'source', 'image', 'start', 'alive', 'network', 'duplicate', 'absent-alive', 'absent-dead', 'malformed-id', 'malformed-boot', 'malformed-pid'])
def test_root_cleanup_checks_exact_stopped_targets_before_delete(launch, monkeypatch, change):
    from ccc_server import codex_managed_recovery as recovery
    p = launch.packet
    role_id = ProcessIdentity('b' * 64, 124, 101, '00000000-0000-4000-8000-000000000001', '2026-01-01T00:00:01Z', 125, 102)
    profile = prep.bound_profiles(p, 'a' * 64)['worker']
    config = profile.config(p['images']['worker']['image_id'])
    role = {'Id': role_id.container_id, 'Image': config['Image'],
        'State': {'Running': False, 'StartedAt': role_id.started_at},
        'Config': {k: v for k, v in config.items() if k not in ('Image', 'HostConfig')},
        'HostConfig': copy.deepcopy(config['HostConfig'])}
    containers = {'a' * 64: launch.container, 'b' * 64: role}
    calls = []
    def request(method, endpoint):
        calls.append((method, endpoint))
        if method == 'GET': return [{'Id': ident} for ident in containers]
        assert method == 'DELETE' and endpoint == '/containers/' + 'b' * 64
        containers.pop('b' * 64)
    launch.docker.request = request
    launch.docker.inspect_container = lambda ident: containers[ident]
    monkeypatch.setattr(recovery, 'exact_process_dead', lambda _: change not in ('alive', 'absent-alive'))
    binding = {'role': 'worker', 'identity': vars(role_id), 'profile': profile.value()}
    if change == 'source': role['HostConfig']['Mounts'][-1]['Source'] = '/different-private-root'
    elif change == 'image': role['Image'] = 'sha256:' + '9' * 64
    elif change == 'start': role['State']['StartedAt'] = 'reused'
    elif change == 'network': binding['profile']['network'] = 'host'
    if change in ('absent-alive', 'absent-dead', 'malformed-id', 'malformed-boot', 'malformed-pid'): containers.pop('b' * 64)
    if change == 'malformed-id': binding['identity']['container_id'] = 'short-id'
    elif change == 'malformed-boot': binding['identity']['boot_id'] = 'unknown'
    elif change == 'malformed-pid': binding['identity']['pid'] = True
    roles = [binding, binding] if change == 'duplicate' else [binding]
    query = {'packet': base64.b64encode(canonical(p)).decode(), 'container_id': 'a' * 64, 'roles': roles}
    launch.anchor['execution_acceptance']['manifest_sha256'] = digest(canonical(p))
    if change and change != 'absent-dead':
        with pytest.raises((Refused, Stop)):
            probe.cleanup(query, anchor=launch.anchor, docker=launch.docker, processes=launch.processes)
        assert not any(method == 'DELETE' for method, _ in calls)
    else:
        result = probe.cleanup(query, anchor=launch.anchor, docker=launch.docker, processes=launch.processes)
        assert result['removed'] == ['b' * 64] and result['scope'] == 'proof-cleanup'
        assert result['runtime_authorized'] is False
        assert 'a' * 64 in containers


def test_post_run_receipt_separately_removes_exact_harness(launch, monkeypatch):
    from ccc_server import codex_managed_recovery as recovery
    launch.container['State'].update(Running=False, StartedAt=launch.identity.started_at)
    rows = {launch.identity.container_id: launch.container}
    launch.docker.inspect_container = lambda ident: rows[ident]
    def request(method, endpoint):
        if method == 'GET': return [{'Id': ident} for ident in rows]
        assert endpoint == '/containers/' + launch.identity.container_id
        rows.pop(launch.identity.container_id)
    launch.docker.request = request
    monkeypatch.setattr(recovery, 'exact_process_dead', lambda _: True)
    result = probe.finish({'packet': base64.b64encode(canonical(launch.packet)).decode(),
        'container_id': launch.identity.container_id, 'identity': vars(launch.identity), 'roles': []},
        anchor=launch.anchor, docker=launch.docker, processes=launch.processes)
    assert result['owned_containers_absent'] is True and rows == {}
    assert result['runtime_authorized'] is False


def test_approval_tool_uses_observed_native_schema_and_has_no_fallback_execution():
    from scripts.codex_restart_fixture import approval_tool
    tool = {'type': 'function', 'name': 'exec_command', 'parameters': {'properties': {
        'cmd': {'type': 'string'}, 'sandbox_permissions': {'type': 'string'}}}}
    actual = approval_tool({'tools': [tool]})
    assert actual['name'] == 'exec_command'
    assert json.loads(actual['arguments'])['cmd'] == 'printf fixture'
    assert json.loads(actual['arguments'])['sandbox_permissions'] == 'require_escalated'
    with pytest.raises(Stop, match='unavailable'):
        approval_tool({'tools': [{'type': 'function', 'name': 'other'}]})


def test_input_handler_refuses_local_fallback_and_observes_actual_reply(tmp_path):
    from scripts.codex_restart_p6 import InputHandler
    calls = []
    class Handler:
        def do_POST(self):
            payload = json.load(self.rfile)
            calls.append(payload)
            result = module._control_plane_engine_call('codex', 'resume', {})
            self.send_json(result)
    module = SimpleNamespace(COMMAND_CENTER_STATE_DIR=tmp_path,
        _control_plane_engine_call=lambda *a, **k: None, CommandCenterHandler=Handler)
    h = SimpleNamespace(root=tmp_path, record=lambda *a, **k: None)
    worker = SimpleNamespace(call=lambda *a, **k: {'ok': True})
    handler = InputHandler(h, module=module)
    with pytest.raises(Stop, match='fallback refused'):
        handler.send(worker, {'id': 'offline-thread'}, 'offline-input')
    assert len(calls) == 1
    module._control_plane_engine_call = lambda *a, **k: {'ok': True, 'queued': True, 'delivery_id': 'offline-input'}
    assert handler.send(worker, {'id': 'offline-thread'}, 'offline-input')['queued'] is True
    assert calls[-1]['idempotency_key'] == 'offline-input'
    assert calls[-1]['skip_wt'] is True


def test_native_output_is_bounded_before_durable_stop():
    from scripts.codex_restart_harness import LocalHarness
    harness = LocalHarness.__new__(LocalHarness)
    harness.native = SimpleNamespace(stdout=io.BytesIO(b'x' * (4 * 1024 * 1024 + 1)))
    harness.stderr = io.BytesIO()
    harness.uncertain_native = False
    reasons = []
    harness.expire = reasons.append
    harness.capture_native_output()
    assert len(harness.stderr.getvalue()) == 4 * 1024 * 1024
    assert harness.uncertain_native and reasons == ['native output exceeded fixed byte limit']


def test_input_adapter_enters_actual_http_handler_without_a_listener(tmp_path, monkeypatch):
    import server
    from scripts.codex_restart_p6 import InputHandler
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, '_control_plane_engine_call', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(server, '_resolve_bridge_session_alias', lambda sid: sid)
    monkeypatch.setattr(server, '_report_routes', SimpleNamespace(resolve=lambda sid, **k: sid))
    from ccc_server import continuation
    monkeypatch.setattr(continuation, 'forward_target', lambda sid: sid)
    monkeypatch.setattr(server, '_log_activity', lambda *a, **k: None)
    seen = []
    def inject(sid, text, **kwargs):
        seen.append((sid, text, kwargs))
        return {'ok': True, 'queued': True, 'delivery_id': kwargs['idempotency_key']}
    monkeypatch.setattr(server, '_inject_text_into_session', inject)
    handler = InputHandler(SimpleNamespace(root=tmp_path, record=lambda *a, **k: None), module=server)
    result = handler.send(SimpleNamespace(call=lambda *a, **k: {'ok': True}),
        {'id': 'offline-thread'}, 'offline-key')
    assert result['queued'] is True
    assert seen[0][0] == 'offline-thread' and seen[0][2]['skip_wt'] is True
    assert seen[0][2]['idempotency_key'] == 'offline-key'


def test_c6_calls_actual_runtime_validator_and_retains_scoped_refusal():
    from scripts.codex_restart_harness import LocalHarness
    harness = LocalHarness.__new__(LocalHarness)
    harness.record = lambda *a, **k: None
    harness.budget = SimpleNamespace(remaining_seconds=lambda: 60)
    harness.control = SimpleNamespace(request=lambda *a, **k: {'refused': True, 'killed': False})
    proposal = {'manifest': canonical({'format': 1, 'scope': 'fixture'}), 'acceptance': {},
                'codex': {}, 'transition': {}, 'fixture_identity': {}}
    result = harness.integration_action('C6', 'refusal', proposal)
    assert result['runtime_scope_refusal'] == 'wrong proof scope'
