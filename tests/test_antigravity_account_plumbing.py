"""Account defaults and routing stay explicit, additive, and credential-free."""
import argparse
import importlib.machinery
import importlib.util
import io
import json

import pytest

import server
from ccc_server import antigravity_accounts as accounts, configuration_history, continuation


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, 'SPAWN_DEFAULTS_FILE', tmp_path / 'spawn-defaults.json')
    monkeypatch.setattr(server, '_spawn_fallback_model_for_engine', lambda engine: 'auto')
    monkeypatch.setattr(server, '_publish_codex_worker_homes', lambda payload: payload)
    monkeypatch.setattr(server, '_model_policy_blocks', lambda model: False)
    monkeypatch.setattr(server, '_model_catalog_allows_model', lambda engine, model: True)
    return server.SPAWN_DEFAULTS_FILE


def test_account_round_trip_and_additive_updates(state):
    server._load_spawn_defaults()
    saved = server._save_spawn_defaults({'antigravity_account_id': 'secondary', 'worker_antigravity_account_id': 'primary'})
    assert saved['ok']
    assert server._save_spawn_defaults({'auto_compact_k': 300})['ok']
    loaded = server._load_spawn_defaults()
    assert loaded['antigravity_account_id'] == 'secondary'
    assert loaded['worker_antigravity_account_id'] == 'primary'
    assert server._resolve_antigravity_spawn_account({})['antigravity_account_id'] == 'secondary'
    assert server._resolve_antigravity_spawn_account({'antigravity_account_id': 'primary'})['antigravity_account_id'] == 'primary'


@pytest.mark.parametrize('key', ['antigravity_account_id', 'worker_antigravity_account_id'])
@pytest.mark.parametrize('value', ['unknown', '__current__', None, 1, []])
def test_invalid_defaults_never_write(state, key, value):
    assert not server._save_spawn_defaults({key: value})['ok']
    assert not state.exists()


def test_parent_binding_beats_machine_default(state):
    server._save_spawn_defaults({'antigravity_account_id': 'primary'})
    accounts.bind('parent', 'secondary')
    assert server._resolve_antigravity_spawn_account({'parent_session_id': 'parent'})['antigravity_account_id'] == 'secondary'
    state.write_text(json.dumps({'antigravity_account_id': 'invalid'}))
    with pytest.raises(ValueError):
        server._resolve_antigravity_spawn_account({})
    with pytest.raises(ValueError):
        server._resolve_antigravity_spawn_account({'antigravity_account_id': 'invalid'})


def test_worker_inheritance_restores_accounts_and_codex_home(state, monkeypatch):
    def publish(payload):
        payload['worker_codex_home'] = payload.get('worker_codex_account_id', '')
        return payload
    monkeypatch.setattr(server, '_publish_codex_worker_homes', publish)
    server._save_spawn_defaults({'antigravity_account_id': 'secondary', 'worker_antigravity_account_id': 'primary'})
    raw = json.loads(state.read_text())
    raw.update(codex_account_id='session-login', worker_codex_account_id='worker-login')
    state.write_text(json.dumps(raw))
    linked = server._save_spawn_defaults({'worker_inherit_session': True})
    assert linked['ok']
    assert linked['worker_antigravity_account_id'] == 'secondary'
    assert linked['worker_codex_account_id'] == linked['worker_codex_home'] == 'session-login'
    assert not server._save_spawn_defaults({'worker_antigravity_account_id': 'primary'})['ok']
    assert server._save_spawn_defaults({'antigravity_account_id': 'primary'})['ok']
    assert server._load_spawn_defaults()['worker_antigravity_account_id'] == 'primary'
    restored = server._save_spawn_defaults({'worker_inherit_session': False})
    assert restored['worker_antigravity_account_id'] == 'primary'
    assert restored['worker_codex_account_id'] == restored['worker_codex_home'] == 'worker-login'


def test_history_tracks_and_undoes_fixed_accounts(state):
    server._load_spawn_defaults()
    saved = server._save_spawn_defaults({'antigravity_account_id': 'secondary', 'worker_antigravity_account_id': 'secondary'})
    assert saved['ok']
    store = configuration_history.History(server)
    entry = store.snapshot()['entries'][0]
    assert entry['reversible']
    assert {c['key'] for c in entry['changes']} == {'antigravity_account_id', 'worker_antigravity_account_id'}
    assert store.undo(store.preview(entry['id'])[0])['applied']
    assert server._load_spawn_defaults()['antigravity_account_id'] == ''


def handler(route, payload=None):
    instance = object.__new__(server.CommandCenterHandler)
    instance.path = route
    body = json.dumps(payload or {}).encode()
    instance.headers = {'Content-Length': str(len(body))}
    instance.rfile = io.BytesIO(body)
    instance._check_proxy_secret = lambda: True
    instance._check_same_origin = lambda: True
    instance._phone_pin_blocked = lambda path: False
    instance._extract_spawned_via = lambda: 'test'
    instance._note_typed_spawn = lambda *args: None
    instance.send_json = lambda data, status=200: setattr(instance, 'result', (status, data))
    return instance


def test_accounts_get_is_public_and_session_bound(state):
    accounts.bind('selected', 'secondary')
    request = handler('/api/antigravity/accounts?session_id=selected')
    request._do_GET()
    status, result = request.result
    assert status == 200 and result['account_id'] == 'secondary'
    assert [row['id'] for row in result['accounts']] == ['primary', 'secondary']
    assert all(set(row) <= {'id', 'label', 'current'} for row in result['accounts'])


def test_typed_spawn_forwards_default_and_parent_binding(state, monkeypatch):
    server._save_spawn_defaults({'antigravity_account_id': 'secondary'})
    calls = []
    monkeypatch.setattr(server, 'spawn_session_antigravity', lambda *args, **kwargs: calls.append(kwargs) or {'ok': False})
    request = handler('/api/sessions/spawn-antigravity', {'prompt': 'hello'})
    request.do_POST()
    assert calls[-1]['antigravity_account_id'] == 'secondary'
    accounts.bind('parent', 'primary')
    request = handler('/api/sessions/spawn-antigravity', {'prompt': 'hello', 'parent_session_id': 'parent'})
    request.do_POST()
    assert calls[-1]['antigravity_account_id'] == 'primary'


@pytest.mark.parametrize('route', ['/api/sessions/spawn', '/api/sessions/spawn-antigravity'])
def test_invalid_api_account_cannot_launch(state, monkeypatch, route):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid account launched')
    monkeypatch.setattr(server, 'spawn_session_antigravity', forbidden)
    request = handler(route, {'engine': 'agy', 'prompt': 'hello', 'antigravity_account_id': 'unknown'})
    request.do_POST()
    assert request.result[0] == 400
    assert request.result[1]['code'] == 'invalid_antigravity_account'


@pytest.fixture
def cli():
    loader = importlib.machinery.SourceFileLoader('ccc_antigravity_test', str(server.Path(__file__).resolve().parents[1] / 'ccc'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize('engine', ['antigravity', 'agy', 'gemini'])
def test_cli_posts_selected_account(cli, monkeypatch, engine):
    calls = []
    monkeypatch.setattr(cli, '_resolve_server', lambda _: 'http://localhost:8099')
    monkeypatch.setattr(cli, '_post_json', lambda base, route, payload: calls.append(payload) or (200, {'ok': True}))
    args = argparse.Namespace(server=None, prompt=['hello'], continue_from=None, antigravity_account='secondary',
                              engine=engine, model=None, effort=None, name=None, runtime=None, cwd=None,
                              worktree=False, report_to=None, confirm_blocked_model=False, json=True, task_key=None, dedupe=False)
    assert cli.cmd_spawn(args) == 0
    assert calls[0]['antigravity_account_id'] == 'secondary'


def test_cli_rejects_override_on_continuation_and_wrong_engine(cli, monkeypatch):
    monkeypatch.setattr(cli, '_resolve_server', lambda _: 'http://localhost:8099')
    args = argparse.Namespace(server=None, prompt=['hello'], continue_from='old', antigravity_account='secondary')
    assert cli.cmd_spawn(args) == 2
    args.continue_from = None
    args.engine = 'codex'
    assert cli.cmd_spawn(args) == 2


def context():
    return dict(session_id='original', latest='latest', engine='antigravity', cwd=None, model=None,
                effort='', report_to='', title='Task', context_tokens=0, idle_seconds=0)


def test_continuation_preserves_latest_binding_and_preview(state, monkeypatch):
    accounts.bind('latest', 'secondary')
    server._save_spawn_defaults({'antigravity_account_id': 'primary'})
    monkeypatch.setattr(continuation, 'session_context', lambda query: context())
    monkeypatch.setattr(continuation, 'build_continuation_prompt', lambda *args: 'brief')
    calls = []
    monkeypatch.setattr(server, 'spawn_session_antigravity', lambda *args, **kwargs: calls.append(kwargs) or {'ok': False})
    preview = continuation.spawn_continuation('original', dry_run=True)
    assert preview['antigravity_account_id'] == 'secondary'
    continuation.spawn_continuation('original', rebind_chain=False)
    assert calls[0]['parent_session_id'] == 'latest'
    assert calls[0]['antigravity_account_id'] == 'secondary'


def test_continuation_task_key_rejects_wrong_account(state, monkeypatch):
    accounts.bind('latest', 'secondary')
    accounts.bind('existing', 'primary')
    monkeypatch.setattr(continuation, 'session_context', lambda query: context())
    monkeypatch.setattr(server, '_spawn_dedupe_lookup', lambda **kwargs: ({'session_id': 'existing'}, None))
    monkeypatch.setattr(server, '_dedupe_existing_response', lambda *args, **kwargs: {'ok': True, 'session_id': 'existing'})
    result = continuation.spawn_continuation('original', task_key='task')
    assert not result['ok'] and result['code'] == 'antigravity_account_conflict'


@pytest.mark.parametrize('engine', ['antigravity', 'agy', 'gemini'])
def test_generic_spawn_alias_dedupe_respects_effective_account(state, monkeypatch, engine):
    accounts.bind('parent', 'secondary')
    accounts.bind('existing', 'primary')
    server._save_spawn_defaults({'antigravity_account_id': 'primary'})
    monkeypatch.setattr(server, '_normalize_return_address', lambda payload: ('', None))
    monkeypatch.setattr(server, '_normalize_spawn_parent_session_id', lambda *args, **kwargs: ('parent', None))
    monkeypatch.setattr(server, '_model_policy_error', lambda model: None)
    monkeypatch.setattr(server, 'byok_spawn_env', lambda *args: {})
    def lookup(**kwargs):
        assert any(key[-2:] == ('antigravity', 'secondary') for key in server._SPAWN_DEDUPE_INFLIGHT)
        return {'session_id': 'existing'}, None
    monkeypatch.setattr(server, '_spawn_dedupe_lookup', lookup)
    monkeypatch.setattr(server, '_dedupe_existing_response', lambda *args, **kwargs: {'ok': True, 'session_id': 'existing'})
    from ccc_server import outcome_api
    monkeypatch.setattr(outcome_api, 'prepare_spawn', lambda *args: None)
    monkeypatch.setattr(outcome_api, 'associate_spawn', lambda *args: None)
    request = handler('/api/sessions/spawn', {'engine': engine, 'prompt': 'hello', 'task_key': 'same-task'})
    request.do_POST()
    assert request.result[1]['code'] == 'antigravity_account_conflict'


def test_account_inheritance_history_can_restore_independent_selection(state):
    server._load_spawn_defaults()
    server._save_spawn_defaults({'antigravity_account_id': 'secondary', 'worker_antigravity_account_id': 'primary'})
    assert server._save_spawn_defaults({'worker_inherit_session': True})['ok']
    store = configuration_history.History(server)
    entry = store.snapshot()['entries'][0]
    assert entry['reversible']
    assert store.undo(store.preview(entry['id'])[0])['applied']
    assert server._load_spawn_defaults()['worker_antigravity_account_id'] == 'primary'
    assert server._load_spawn_defaults().get('worker_inherit_session') is not True


def test_cli_parser_rejects_unknown_account(cli, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(['spawn', '--engine', 'antigravity', '--antigravity-account', 'unknown', 'hello'])
    assert exc.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err


@pytest.mark.parametrize('entry_point', ['spawn', 'continuation'])
@pytest.mark.parametrize('requested', ['primary', 'secondary'])
@pytest.mark.parametrize('native_id', ['', 'native-session'])
def test_pending_secondary_retry_and_conflict_use_persisted_launch_account(
    state, monkeypatch, entry_point, requested, native_id,
):
    # Persist the exact registry shape produced before AGY reports its native ID:
    # the account binding is in the pending store, absent from the registry row.
    log = state.parent / 'pending-spawn.log'
    accounts.bind_pending(str(log), 'secondary')
    registry = state.parent / 'spawned-pids.json'
    registry.write_text(json.dumps([dict(
        engine='antigravity', task_key='pending-task', pid=None,
        session_id=native_id, log=str(log), name='Pending launch',
    )]))
    monkeypatch.setattr(server, 'SPAWNED_PIDS_FILE', registry)
    accounts.bind('latest', requested)
    if native_id:
        # Once a native ID exists, its immutable binding beats the old log.
        accounts.bind(native_id, 'primary')
    def forbidden(*args, **kwargs):
        pytest.fail('dedupe retry must never launch another process')
    monkeypatch.setattr(server, 'spawn_session_antigravity', forbidden)
    monkeypatch.setattr(server, '_normalize_return_address', lambda payload: ('', None))
    monkeypatch.setattr(server, '_normalize_spawn_parent_session_id', lambda *args, **kwargs: ('latest', None))
    monkeypatch.setattr(server, '_model_policy_error', lambda model: None)
    monkeypatch.setattr(server, 'byok_spawn_env', lambda *args: {})
    from ccc_server import outcome_api
    monkeypatch.setattr(outcome_api, 'prepare_spawn', lambda *args: None)
    monkeypatch.setattr(outcome_api, 'associate_spawn', lambda *args: None)
    monkeypatch.setattr(continuation, 'session_context', lambda query: context())
    expected_account = 'primary' if native_id else 'secondary'
    for _ in range(2):
        if entry_point == 'spawn':
            request = handler('/api/sessions/spawn', {
                'engine': 'agy', 'prompt': 'hello', 'task_key': 'pending-task',
                'antigravity_account_id': requested,
            })
            request.do_POST()
            assert request.result[0] == 200
            result = request.result[1]
        else:
            result = continuation.spawn_continuation('original', task_key='pending-task')
        if requested == expected_account:
            assert result['ok'] and result['existing']
            assert result['session_id_pending'] is (not bool(native_id))
            assert result['session_id'] == native_id
        else:
            assert not result['ok']
            assert result['code'] == 'antigravity_account_conflict'
