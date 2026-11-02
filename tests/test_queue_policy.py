"""Native queue endpoint with disposable files; no worker/service actions."""
import copy
import json
import threading
import urllib.error
import urllib.request

import pytest
import server
from ccc_server import queue_policy


@pytest.fixture
def api(monkeypatch, tmp_path):
    state = tmp_path / 'spawn-defaults.json'
    queues = tmp_path / 'queue-config.json'
    defaults = server._factory_spawn_defaults()
    defaults.update(worker_engine='codex', worker_model='gpt-5-codex', worker_reasoning_effort='high',
                    worker_fallback={'enabled': True, 'models': []}, worker_headroom_dispatch=True)
    state.write_text(json.dumps(defaults))
    config = {'DEMO': {'engine': 'claude', 'model': 'claude-sonnet-4-6', 'effort': 'high',
                       'fallback_to_default_worker': False, 'headroom_dispatch': False,
                       'auto_drain': True, 'desired_workers': 3, 'roles': {'reviewer': {'future': True}},
                       'future_policy': {'keep': [False, None, 'opaque']}},
              'OTHER': {'auto_drain': False, 'engine': 'codex'}, 'RETIRED': {'archived': True}}
    queues.write_text(json.dumps(config))
    queues.chmod(0o640)
    monkeypatch.setattr(server, 'SPAWN_DEFAULTS_FILE', state)
    monkeypatch.setattr(server, 'COMMAND_CENTER_STATE_DIR', tmp_path)
    monkeypatch.setattr(server, '_wt_config_path', lambda: queues)
    monkeypatch.setattr(server, '_WT_CONFIG_AVAILABLE', False)
    monkeypatch.setattr(server, '_model_policy_error', lambda model: 'Model is blocked' if model == 'blocked' else None)
    monkeypatch.setattr(server, '_model_policy_blocks', lambda model: False)
    monkeypatch.setattr(server, '_reconcile_once_async', lambda: pytest.fail('Must not launch/reconcile workers'))
    monkeypatch.setattr(server, '_log_activity', lambda *args: pytest.fail('No unrelated state writes'))
    class Handler(server.CommandCenterHandler):
        def log_message(self, *args): pass
        def _phone_pin_blocked(self, path): return False
        def _check_proxy_secret(self): return True
    httpd = server.http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    def request(payload=None):
        url = f'http://127.0.0.1:{httpd.server_address[1]}/api/queue/config'
        body = {'queue': 'DEMO', 'policy_action': 'read'} if payload is None else payload
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    yield request, queues, state, config
    httpd.shutdown(); httpd.server_close(); thread.join(timeout=5)


def preview(request, changes):
    code, current = request(); assert code == 200, current
    payload = {'queue': 'DEMO', 'policy_action': 'preview', 'expected_revision': current['policy_revision'], 'changes': changes}
    code, view = request(payload); assert code == 200, view
    return payload, view


def apply(request, payload, view):
    return request({**payload, 'policy_action': 'apply', 'preview_token': view['preview_token']})


def test_read_and_preview_never_write_and_sources_distinguish_off(api):
    request, queues, state, config = api
    before = queues.read_bytes(), state.read_bytes()
    code, view = request(); assert code == 200
    assert view['overrides']['fallback_to_default_worker'] is False
    assert view['plan']['fallback']['source'] == 'queue_pin'
    payload, view = preview(request, {'fallback_to_default_worker': None, 'headroom_dispatch': None})
    assert view['plan']['basis'] == 'configuration_only'
    assert view['plan']['fallback']['enabled'] is True
    assert view['plan']['fallback']['headroom'] is True
    assert view['changes'][0] == {'key': 'fallback_to_default_worker', 'before': False, 'before_set': True, 'after': None, 'after_set': False}
    assert (queues.read_bytes(), state.read_bytes()) == before


def test_apply_only_explicit_exceptions_preserves_every_other_value(api):
    request, queues, state, original = api
    before_defaults = state.read_bytes()
    payload, view = preview(request, {'engine': None, 'model': None, 'effort': None})
    code, result = apply(request, payload, view); assert code == 200 and result['applied']
    expected = copy.deepcopy(original)
    for key in ('engine', 'model', 'effort'): expected['DEMO'].pop(key)
    assert json.loads(queues.read_text()) == expected
    assert state.read_bytes() == before_defaults
    assert queues.stat().st_mode & 0o777 == 0o640
    code, saved = request(); assert code == 200
    assert saved['plan']['fields']['engine']['value'] == 'codex'
    assert saved['plan']['fallback']['source'] == 'queue_pin'  # explicit Off preserved


def test_explicit_off_and_follow_default_have_different_durable_semantics(api):
    request, queues, _, _ = api
    payload, view = preview(request, {'fallback_to_default_worker': None})
    assert apply(request, payload, view)[0] == 200
    assert 'fallback_to_default_worker' not in json.loads(queues.read_text())['DEMO']
    assert request()[1]['plan']['fallback']['enabled']
    payload, view = preview(request, {'fallback_to_default_worker': False})
    assert apply(request, payload, view)[0] == 200
    assert json.loads(queues.read_text())['DEMO']['fallback_to_default_worker'] is False
    assert not request()[1]['plan']['fallback']['enabled']


@pytest.mark.parametrize('target', ['queue', 'defaults'])
def test_concurrent_edits_reject_apply_before_writes(api, target):
    request, queues, state, _ = api
    payload, view = preview(request, {'model': 'custom-model'})
    path = queues if target == 'queue' else state
    obj = json.loads(path.read_text()); obj['future_concurrent'] = True; path.write_text(json.dumps(obj))
    before = queues.read_bytes(), state.read_bytes()
    assert apply(request, payload, view)[0] == 409
    assert (queues.read_bytes(), state.read_bytes()) == before


@pytest.mark.parametrize('changes', [{}, {'roles': {}}, {'engine': 'invalid'}, {'effort': 'invalid'},
                                    {'model': 'blocked'}, {'model': 7}, {'model': 'bad\nmodel'},
                                    {'fallback_to_default_worker': 'off'}, {'headroom_dispatch': 0},
                                    {'engine': 'codex', 'effort': 'max'}])
def test_all_validation_before_any_write(api, changes):
    request, queues, state, _ = api
    rev = request()[1]['policy_revision']; before = queues.read_bytes(), state.read_bytes()
    code, result = request({'queue': 'DEMO', 'policy_action': 'preview', 'expected_revision': rev, 'changes': changes})
    assert code == 400, result
    assert (queues.read_bytes(), state.read_bytes()) == before


def test_exact_preview_required_and_noop_does_not_write(api, monkeypatch):
    request, queues, _, _ = api
    payload, view = preview(request, {'effort': 'low'})
    assert request({**payload, 'policy_action': 'apply', 'preview_token': 'wrong'})[0] == 409
    assert apply(request, {**payload, 'changes': {'effort': 'medium'}}, view)[0] == 409
    monkeypatch.setattr(queue_policy.os, 'replace', lambda *args: pytest.fail('No-op must not replace'))
    payload, view = preview(request, {'fallback_to_default_worker': False})
    assert view['changes'] == []
    assert apply(request, payload, view)[1]['applied'] is False


def test_failed_atomic_replace_keeps_entire_file_and_removes_temporary(api, monkeypatch):
    request, queues, state, _ = api
    payload, view = preview(request, {'engine': 'codex', 'model': 'gpt-5-codex', 'effort': 'low'})
    before = queues.read_bytes(), state.read_bytes()
    def fail(*args): raise OSError('simulated publication failure')
    monkeypatch.setattr(queue_policy.os, 'replace', fail)
    assert apply(request, payload, view)[0] == 503
    assert (queues.read_bytes(), state.read_bytes()) == before
    assert not list(queues.parent.glob('queue-policy-*.tmp'))


def test_final_external_edit_recheck_prevents_overwrite(api, monkeypatch):
    request, queues, _, _ = api
    payload, view = preview(request, {'effort': 'low'})
    original = queue_policy.os.fsync
    def concurrent(fd):
        original(fd); data=json.loads(queues.read_text()); data['OTHER']['future']=True; queues.write_text(json.dumps(data))
    monkeypatch.setattr(queue_policy.os, 'fsync', concurrent)
    assert apply(request, payload, view)[0] == 409
    assert json.loads(queues.read_text())['DEMO']['effort'] == 'high'
    assert json.loads(queues.read_text())['OTHER']['future'] is True


def test_missing_archived_ambiguous_and_invalid_config_never_write(api):
    request, queues, _, _ = api
    for name in ('RETIRED', 'MISSING'):
        assert request({'queue': name, 'policy_action': 'read'})[0] == 400
    cfg=json.loads(queues.read_text()); cfg['demo']={}; queues.write_text(json.dumps(cfg))
    assert request()[0] == 400
    queues.write_text('invalid JSON'); before=queues.read_bytes()
    assert request()[0] == 400 and queues.read_bytes() == before


def test_unknown_existing_policy_values_are_not_normalized_by_other_edits(api):
    request, queues, _, _ = api
    cfg=json.loads(queues.read_text()); cfg['DEMO'].update(engine='future-engine',model='opaque-model',effort='future-effort',headroom_dispatch='future-boolean')
    queues.write_text(json.dumps(cfg))
    payload, view=preview(request, {'fallback_to_default_worker': True})
    assert apply(request,payload,view)[0] == 200
    saved=json.loads(queues.read_text())['DEMO']
    for key in ('engine','model','effort','headroom_dispatch'): assert saved[key] == cfg['DEMO'][key]


def test_saved_api_matches_actual_watchtower_consumers_after_scoped_apply(api, monkeypatch):
    wt = pytest.importorskip('watchtower.config')
    from watchtower import models
    request, queues, state, _ = api
    monkeypatch.setattr(server, '_WT_CONFIG_AVAILABLE', True)
    monkeypatch.setattr(server, '_wt_config', wt)
    monkeypatch.setattr(wt, 'CONFIG_FILE', queues)
    monkeypatch.setattr(wt, 'CCC_SPAWN_DEFAULTS_FILE', state)
    monkeypatch.setattr(wt, 'CCC_MODEL_POLICY_FILE', queues.parent / 'model-policy.json')
    monkeypatch.setattr(models, 'entries', lambda engine: [{'id': model, 'label': model, 'efforts': ['low', 'medium', 'high']}
        for model in {'claude': ['claude-sonnet-4-6'], 'codex': ['gpt-5-codex']}.get(engine, [])])
    for changes in ({'fallback_to_default_worker': None, 'headroom_dispatch': None},
                    {'engine': None, 'model': None, 'effort': None},
                    {'engine': 'claude', 'model': 'claude-sonnet-4-6', 'effort': 'low',
                     'fallback_to_default_worker': False, 'headroom_dispatch': False}):
        payload, view = preview(request, changes)
        assert apply(request, payload, view)[0] == 200
        code, saved = request(); assert code == 200
        plan = saved['plan']; assert plan['basis'] == 'consumer'
        assert tuple(plan['fields'][k]['value'] for k in ('engine', 'model', 'effort')) == (wt.engine('DEMO'), wt.model('DEMO'), wt.effort('DEMO'))
        assert plan['fallback']['enabled'] == wt.fallback_to_default_worker('DEMO')
        assert plan['fallback']['headroom'] == wt.headroom_dispatch('DEMO')
        assert saved['config']['future_policy'] == {'keep': [False, None, 'opaque']}


def test_missing_defaults_never_seeded_by_read_preview_or_apply(api):
    request, queues, state, _ = api
    state.unlink(); before=queues.read_bytes()
    assert request()[0] == 200 and not state.exists()
    payload, view=preview(request, {'fallback_to_default_worker': None})
    assert queues.read_bytes() == before and not state.exists()
    assert apply(request,payload,view)[0] == 200 and not state.exists()


def test_other_queue_configuration_retains_contextual_exceptions_and_unknown_metadata(api, monkeypatch):
    request, queues, _, original = api
    monkeypatch.setattr(server, '_reconcile_once_async', lambda: None)
    monkeypatch.setattr(server, '_wt_log_queue_config_change', lambda *args, **kwargs: None)
    body = {'queue': 'DEMO', 'workers': 3, 'backend': 'file', 'engine': 'claude',
            'model': 'claude-sonnet-4-6', 'effort': 'high', 'auto_drain': True,
            'claim_types': [], 'product_gate': False, 'fallback_to_default_worker': False}
    code, result = request(body); assert code == 200, result
    saved=json.loads(queues.read_text())
    for key in ('future_policy', 'roles', 'headroom_dispatch'):
        assert saved['DEMO'][key] == original['DEMO'][key]
    assert saved['OTHER'] == original['OTHER']
    assert saved['DEMO']['engine'] == 'claude'
    assert saved['DEMO']['fallback_to_default_worker'] is False
