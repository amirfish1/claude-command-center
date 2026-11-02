"""Actual staged-policy HTTP API, disposable files and atomic-write failures."""
import copy
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import server


@pytest.fixture
def api(monkeypatch, tmp_path):
    state = tmp_path / 'spawn-defaults.json'
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, 'SPAWN_DEFAULTS_FILE', state)
    monkeypatch.setattr(server, '_worker_fallback_catalog_models', lambda engine: [
        {'id': name} for name in {'claude': ['claude-sonnet-4-6', 'claude-opus-4-6'],
                                  'codex': ['gpt-5-codex']}.get(engine, [])])
    monkeypatch.setattr(server, '_model_policy_blocks', lambda model: False)
    monkeypatch.setattr(server, '_model_policy_blocked_models', lambda: set())
    monkeypatch.setattr(server, '_model_catalog_allows_model', lambda engine, model: True)
    monkeypatch.setattr(server, '_codex_context_1m_enabled', lambda: False)
    monkeypatch.setattr(server, '_codex_context_config', lambda: {'model_context_window': 1000000})
    raw = server._factory_spawn_defaults()
    raw.update(engine='claude', reasoning_effort='high', worker_engine='codex', worker_model='gpt-5-codex',
               worker_reasoning_effort='medium', future_policy={'keep': [1, 2]}, worker_fallback={
                   'enabled': True, 'future_guard': {'keep': True}, 'models': [
                       {'engine': 'claude', 'model': 'claude-sonnet-4-6', 'effort': 'high', 'future_row': 'first'},
                       {'engine': 'codex', 'model': 'gpt-5-codex', 'effort': 'medium', 'future_row': 'second'}]},
               model_profiles={'fast': {'models': [], 'future_profile': True},
                               'future_capability': {'opaque': ['retain']}})
    raw['models'].update(claude='claude-sonnet-4-6', codex='gpt-5-codex')
    state.write_text(json.dumps(raw))
    class Handler(server.CommandCenterHandler):
        def log_message(self, *args): pass
        def _phone_pin_blocked(self, path): return False
        def _check_proxy_secret(self): return True
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    def request(payload=None):
        url = f'http://127.0.0.1:{httpd.server_address[1]}/api/spawn-defaults' + ('?policy=1' if payload is None else '')
        req = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    yield request, state, raw
    httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)


def staged(request, changes, origins=None):
    return {'policy_action': 'preview', 'expected_revision': request()[1]['policy_revision'],
            'changes': changes, 'origins': origins or {}}


def apply(request, payload):
    code, view = request(payload)
    assert code == 200, view
    return request({**payload, 'policy_action': 'apply', 'preview_token': view['preview_token']})


def test_policy_read_preview_do_not_seed_migrate_or_write(api):
    request, state, _ = api
    state.unlink()
    code, view = request(); assert code == 200 and not state.exists()
    payload = staged(request, {'reasoning_effort': 'high'})
    assert request(payload)[0] == 200 and not state.exists()
    legacy = {'engine': 'claude', 'models': {'claude': 'claude-sonnet-4-6'}, 'opaque': {'keep': True}}
    state.write_text(json.dumps(legacy)); before = state.read_bytes()
    payload = staged(request, {'auto_compact_k': 400})
    assert request(payload)[0] == 200 and state.read_bytes() == before
    assert apply(request, payload)[0] == 200
    assert json.loads(state.read_text()) == {**legacy, 'auto_compact_k': 400}


def test_combined_policy_apply_preserves_metadata_and_row_identity(api):
    request, state, raw = api
    routes = [{'engine': 'codex', 'model': 'gpt-5-codex', 'effort': 'high'},
              {'engine': 'claude', 'model': 'claude-opus-4-6', 'effort': 'medium'}]
    payload = staged(request, {'engine': 'codex', 'reasoning_effort': 'high',
                              'worker_fallback': {'enabled': True, 'models': routes},
                              'model_profiles': {'fast': {'models': [routes[1]]}}},
                     {'worker_fallback': [1, 0], 'model_profiles': {'fast': [None]}})
    before = state.read_bytes(); code, preview = request(payload)
    assert code == 200 and state.read_bytes() == before
    assert {row['key'] for row in preview['changes']} >= {'engine', 'worker_fallback', 'model_profiles'}
    code, result = apply(request, payload); assert code == 200 and result['applied']
    saved = json.loads(state.read_text())
    assert saved['future_policy'] == raw['future_policy']
    assert saved['worker_fallback']['future_guard'] == raw['worker_fallback']['future_guard']
    assert [row['future_row'] for row in saved['worker_fallback']['models']] == ['second', 'first']
    assert saved['model_profiles']['future_capability'] == raw['model_profiles']['future_capability']
    assert saved['model_profiles']['fast']['future_profile'] is True
    assert saved['engine'] == 'codex' and saved['reasoning_effort'] == 'high'


@pytest.mark.parametrize('change,origins', [
    ({'engine': 'unsupported'}, {}),
    ({'auto_compact_k': 10}, {}),
    ({'worker_overrides': {}}, {}),
    ({'worker_inherit_session': 'true'}, {}),
    ({'worker_fallback': {'enabled': True, 'models': []}}, {'worker_fallback': []}),
    ({'worker_fallback': {'enabled': True, 'models': [
        {'engine': 'claude', 'model': 'claude-sonnet-4-6'}, {'engine': 'claude', 'model': 'claude-opus-4-6'}]}}, {'worker_fallback': [0, None]}),
    ({'model_profiles': {'fast': {'models': [{'engine': 'codex', 'model': 'gpt-5-codex'}]}}}, {'model_profiles': {'fast': [None]}}),
    ({'model_profiles': {'fast': {'models': [{'engine': 'claude', 'model': 'not-in-catalog'}]}}}, {'model_profiles': {'fast': [None]}}),
    ({'model_profiles': {'fast': {'models': [{'engine': 'claude', 'model': 'claude-sonnet-4-6', 'effort': 'bad'}]}}}, {'model_profiles': {'fast': [None]}}),
    ({'model_profiles': {'fast': {'models': []}}}, {'model_profiles': ['bad']}),
    ({'worker_fallback': {'enabled': True, 'models': [{'engine': 'claude', 'model': 'claude-sonnet-4-6'}]}}, {'worker_fallback': [99]}),
])
def test_invalid_combination_never_partially_saves(api, change, origins):
    request, state, _ = api
    before = state.read_bytes()
    payload = staged(request, {'reasoning_effort': 'medium', **change}, origins)
    assert request(payload)[0] == 400
    assert state.read_bytes() == before


def test_revision_preview_and_validation_changes_reject_without_write(api, monkeypatch):
    request, state, raw = api
    payload = staged(request, {'reasoning_effort': 'medium'})
    code, preview = request(payload); assert code == 200
    before = state.read_bytes()
    assert request({**payload, 'policy_action': 'apply', 'preview_token': 'wrong'})[0] == 409
    assert state.read_bytes() == before
    raw['future_policy'] = {'newer': True}; state.write_text(json.dumps(raw)); before = state.read_bytes()
    assert request({**payload, 'policy_action': 'apply', 'preview_token': preview['preview_token']})[0] == 409
    assert state.read_bytes() == before
    payload = staged(request, {'worker_model': 'new-identifier'})
    code, preview = request(payload); assert code == 200
    monkeypatch.setattr(server, '_model_policy_blocks', lambda model: model == 'new-identifier')
    assert request({**payload, 'policy_action': 'apply', 'preview_token': preview['preview_token']})[0] == 400
    assert state.read_bytes() == before


def test_atomic_replace_failure_and_retry(api, monkeypatch):
    request, state, _ = api
    payload = staged(request, {'engine': 'codex', 'reasoning_effort': 'high', 'worker_inherit_session': True})
    before = state.read_bytes(); original = Path.replace
    def fail(source, target):
        if target == state: raise OSError('simulated replace failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail)
        assert apply(request, payload)[0] == 503
    assert state.read_bytes() == before
    assert not list(state.parent.glob('spawn-defaults-*.tmp'))
    assert apply(request, payload)[0] == 200
    saved = json.loads(state.read_text())
    assert (saved['engine'], saved['worker_engine'], saved['worker_model'], saved['worker_reasoning_effort']) == ('codex', 'codex', 'gpt-5-codex', 'high')
    assert apply(request, staged(request, {'worker_inherit_session': False}))[0] == 200
    restored = json.loads(state.read_text())
    assert restored['worker_engine'] == 'codex' and restored['worker_reasoning_effort'] == 'medium'


def test_noop_apply_does_not_rewrite_file(api):
    request, state, raw = api
    before = state.read_bytes(); stamp = state.stat().st_mtime_ns
    code, response = apply(request, staged(request, {'engine': raw['engine']}))
    assert code == 200 and not response['applied']
    assert state.read_bytes() == before and state.stat().st_mtime_ns == stamp


def test_engine_effort_combination_and_read_race_are_validated(api, monkeypatch):
    request, state, raw = api
    raw['reasoning_effort'] = 'max'; state.write_text(json.dumps(raw)); before = state.read_bytes()
    assert request(staged(request, {'engine': 'codex'}))[0] == 400
    assert state.read_bytes() == before
    load = server._load_spawn_defaults
    def concurrent(**kwargs):
        value = load(**kwargs)
        current = json.loads(state.read_text()); current['future_policy']['changed'] = True
        state.write_text(json.dumps(current))
        return value
    monkeypatch.setattr(server, '_load_spawn_defaults', concurrent)
    assert request()[0] == 409


def test_unknown_independent_tuple_and_metadata_restore_exactly(api):
    request, state, raw = api
    raw['worker_inherit_session'] = True
    override = {'worker_engine': 'future-worker', 'worker_model': 'future-model',
                'worker_reasoning_effort': 'future-effort', 'worker_auto_compact_k': 777,
                'engine': 'metadata-only', 'future': {'keep': True}}
    raw['worker_overrides'] = copy.deepcopy(override); state.write_text(json.dumps(raw))
    assert apply(request, staged(request, {'worker_inherit_session': False}))[0] == 200
    saved = json.loads(state.read_text())
    assert saved['worker_overrides'] == override
    assert saved['engine'] == raw['engine']
    for key in ('worker_engine', 'worker_model', 'worker_reasoning_effort', 'worker_auto_compact_k'):
        assert saved[key] == override[key]


def test_saved_policy_is_readable_by_distinct_native_consumers(api, monkeypatch):
    wt = pytest.importorskip('watchtower.config')
    request, state, _ = api
    from watchtower import models
    monkeypatch.setattr(models, 'entries', lambda engine: [
        {'id': row['id'], 'label': row['id'], 'efforts': ['low', 'medium', 'high']}
        for row in server._worker_fallback_catalog_models(engine)])
    monkeypatch.delenv('WATCHTOWER_BLOCKED_MODELS', raising=False)
    monkeypatch.delenv('CCC_BLOCKED_MODELS', raising=False)
    monkeypatch.setattr(wt, 'CCC_SPAWN_DEFAULTS_FILE', state)
    monkeypatch.setattr(wt, 'CCC_MODEL_POLICY_FILE', state.parent / 'policy.json')
    state.parent.joinpath('policy.json').write_text('{"blocked_models": []}')
    rows = [{'engine': 'claude', 'model': 'claude-sonnet-4-6', 'effort': 'high'},
            {'engine': 'claude', 'model': 'claude-opus-4-6', 'effort': 'medium'}]
    assert apply(request, staged(request, {'model_profiles': {'deep': {'models': rows}}},
                                 {'model_profiles': {'deep': [None, None]}}))[0] == 200
    assert wt.model_profile('deep')['models'] == rows
    policy = wt.worker_fallback_policy()
    assert [row['engine'] for row in policy['models']] == ['claude', 'codex']
    assert policy['enabled'] is True
    assert wt.fallback_chain() == ['claude', 'codex']
