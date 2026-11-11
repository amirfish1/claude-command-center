"""On-call answers must remain bound to the request that raised the alert."""
from types import SimpleNamespace

import server
from ccc_server import approval_hook, codex, session_graph
from worker_engines import EngineHost


def pending(request_id):
    return {"id": str(request_id), "request_id": str(request_id),
            "request_id_raw": request_id, "approval_nonce": str(request_id) + "nonce", "needs_approval": True,
            "approval_method": "execCommandApproval"}


def test_codex_replacement_rejected_under_resolver_lock(monkeypatch):
    monkeypatch.setattr(server, '_control_plane_engine_call', lambda *a: None)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_THREAD_STATE',
                        {'s': {'pending_approval_request': pending(2)}})
    def no_transport():
        raise AssertionError('replacement must not be sent')
    monkeypatch.setattr(server, '_ensure_codex_app_server', no_transport)
    result = codex._codex_app_server_resolve_approval('s', 'accept', '1')
    assert result['code'] == 'codex_approval_changed'


def test_codex_matching_id_sent_and_new_request_preserved(monkeypatch):
    state = {'pending_approval_request': pending(1), 'thread_needs_approval': True,
             'active_flags': ['waitingOnApproval']}
    monkeypatch.setattr(server, '_control_plane_engine_call', lambda *a: None)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_THREAD_STATE', {'s': state})
    monkeypatch.setattr(server, '_save_codex_app_server_state_unlocked', lambda: None)
    sent = []
    def send(message):
        sent.append(message)
        state['pending_approval_request'] = pending(2)
    monkeypatch.setattr(server, '_ensure_codex_app_server',
                        lambda: SimpleNamespace(send_json=send))
    result = codex._codex_app_server_resolve_approval('s', 'accept', '1')
    assert result['ok'] and sent[0]['id'] == 1
    assert state['pending_approval_request']['request_id'] == '2'
    assert state['thread_needs_approval']


def test_control_plane_carries_expected_id():
    calls = []
    host = EngineHost(SimpleNamespace(ledger=None))
    host._module = SimpleNamespace(_codex_app_server_resolve_approval=lambda *a, **kw:
                                   calls.append((a, kw)) or {'ok': True})
    result = host._call_unbound('codex', 'approval',
                                {'session_id': 's', 'decision': 'accept',
                                 'expected_request_id': 'old'})
    assert result['ok']
    assert calls == [(('s', 'accept'), {'expected_request_id': 'old', 'expected_nonce': None})]


def test_claude_exact_hash_only_and_no_terminal_fallback(monkeypatch):
    old, new = 'a' * 64, 'b' * 64
    monkeypatch.setattr(session_graph, 'isClaudeSource_backend', lambda sid: True)
    monkeypatch.setattr(approval_hook, 'waiting', lambda sid, sha, **kw: sha == new)
    delivered = []
    monkeypatch.setattr(approval_hook, 'deliver', lambda *args, **kw: delivered.append((args, kw)) or 'applied')
    monkeypatch.setattr(session_graph, '_answer_via_approval_hook',
                        lambda *a: (_ for _ in ()).throw(AssertionError('fallback')))
    assert not session_graph.respond_claude_permission('s', 'accept', expected_hook_sha=old)['ok']
    assert session_graph.respond_claude_permission('s', 'decline', expected_hook_sha=new, expected_hook_nonce='nonce')['ok']
    assert delivered == [(('s', new, 'deny'), {'nonce': 'nonce'})]


def test_claude_unbound_prompt_does_not_raise_alert(monkeypatch):
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'claude')
    monkeypatch.setattr(server, '_read_notification_state', lambda sid: {'message': 'tool'})
    monkeypatch.setattr(server, '_notification_is_blocking', lambda row: True)
    monkeypatch.setattr(approval_hook, 'waiting', lambda sid, sha, **kw: False)
    assert server._OncallDeps().permission('s') is None


def test_claude_unbound_prompt_alerts_but_cannot_be_approved(monkeypatch):
    from ccc_server.oncall import alerts
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'claude')
    monkeypatch.setattr(server, '_read_notification_state',
                        lambda sid: {'message': 'Claude needs your permission to use Bash', 'started_at': 12.5})
    monkeypatch.setattr(server, '_notification_is_blocking', lambda row: True)
    monkeypatch.setattr(approval_hook, 'waiting', lambda sid, sha, **kw: False)
    monkeypatch.setattr(server, 'respond_claude_permission',
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not answer')))
    deps = server._OncallDeps()
    signal = deps.permission('s')
    assert signal['kind'] == 'permission' and signal['meta'] == {'engine': 'claude', 'at_keyboard': True}
    assert signal['stamp'] == 'keyboard:12.5'
    ok, detail = deps.approve('s', 'allow', signal['meta'])
    assert not ok and 'in the session' in detail
    _subject, text, choices = alerts.render({'id': 3, 'kind': 'permission', 'title': 'a session',
                                             'body': signal['body'], 'meta': signal['meta']})
    assert choices == () and 'cannot be approved from here' in text and 'allow or deny' not in text


def test_codex_unbound_prompt_does_not_raise_alert(monkeypatch):
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'codex')
    monkeypatch.setattr(server, '_codex_thread_state_resolved', lambda sid: {})
    monkeypatch.setattr(server, '_codex_app_server_pending_approval_item',
                        lambda state: {'id': 'item-only', 'command': 'echo hello'})
    assert server._OncallDeps().permission('s') is None


def test_hook_badge_binds_message_to_its_call(tmp_path, monkeypatch):
    import json
    import runpy
    from pathlib import Path

    hook = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'hooks' / 'permission-request.py'))
    monkeypatch.setenv('CCC_LIVE_STATE_DIR', str(tmp_path))
    tool_input = {'command': 'echo hello'}
    badge = hook['raise_badge']('s', 'Bash', tool_input)
    row = json.loads(Path(badge).read_text())
    assert row['sha256'] == hook['input_sha256']('Bash', tool_input)
    assert 'echo hello' in row['message']


def test_oncall_race_carries_alert_id_to_resolver(monkeypatch):
    state = {'pending_approval_request': pending(1)}
    state['pending_approval_request']['command'] = 'echo hello'
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'codex')
    monkeypatch.setattr(server, '_codex_thread_state_resolved', lambda sid: state)
    monkeypatch.setattr(server, '_control_plane_engine_call', lambda *a: None)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_THREAD_STATE', {'s': state})
    original = codex._codex_app_server_resolve_approval
    def replace_before_resolve(sid, action, **kwargs):
        state['pending_approval_request'] = pending(2)
        return original(sid, action, **kwargs)
    monkeypatch.setattr(server, '_codex_app_server_resolve_approval', replace_before_resolve)
    deps = server._OncallDeps()
    signal = deps.permission('s')
    ok, _ = deps.approve('s', 'allow', signal['meta'])
    assert not ok
    assert state['pending_approval_request']['request_id'] == '2'


def test_identical_hook_replacement_cannot_consume_bound_decision(tmp_path, monkeypatch):
    import runpy
    import time
    from pathlib import Path
    hook = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'hooks' / 'permission-request.py'))
    monkeypatch.setenv('CCC_APPROVAL_HOOK_DIR', str(tmp_path))
    sha = hook['input_sha256']('Bash', {'command': 'echo hello'})
    old, new = 'a' * 32, 'b' * 32
    approval_hook.post_decision('s', sha, 'allow', nonce=old)
    path = tmp_path / 'decisions' / approval_hook._decision_name('s', sha, old)
    assert hook['take_decision'](str(path), sha, time.time(), new) is None
    assert path.exists()
    assert not (tmp_path / 'decisions' / approval_hook._name('s', sha)).exists()
    assert hook['take_decision'](str(path), sha, time.time(), old) == 'allow'


def test_codex_zero_and_reused_id_new_transport_rejected(monkeypatch):
    summary = codex._codex_app_server_approval_request_summary(0, 'execCommandApproval', {'command': 'echo hello'}, 1)
    assert summary['request_id'] == '0'
    state = {'pending_approval_request': pending(0)}
    state['pending_approval_request']['approval_generation'] = 'old-generation'
    transport = SimpleNamespace(_ccc_approval_generation='new-generation', send_json=lambda msg: (_ for _ in ()).throw(AssertionError('sent')))
    monkeypatch.setattr(server, '_control_plane_engine_call', lambda *a: None)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_THREAD_STATE', {'s': state})
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_TRANSPORT', transport)
    monkeypatch.setattr(server, '_ensure_codex_app_server', lambda: transport)
    result = codex._codex_app_server_resolve_approval('s', 'accept', '0', '0nonce')
    assert result['code'] == 'codex_approval_changed'


def test_codex_zero_id_oncall_visible(monkeypatch):
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'codex')
    monkeypatch.setattr(server, '_codex_thread_state_resolved', lambda sid: {'pending_approval_request': {**pending(0), 'command': 'echo hello'}})
    assert server._OncallDeps().permission('s')['meta']['prompt'] == '0'


def test_codex_zero_id_matching_generation_is_sent(monkeypatch):
    state = {'pending_approval_request': {**pending(0), 'approval_generation': 'gen'}}
    sent = []
    transport = SimpleNamespace(_ccc_approval_generation='gen', send_json=sent.append)
    monkeypatch.setattr(server, '_control_plane_engine_call', lambda *a: None)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_THREAD_STATE', {'s': state})
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_TRANSPORT', transport)
    monkeypatch.setattr(server, '_ensure_codex_app_server', lambda: transport)
    monkeypatch.setattr(server, '_save_codex_app_server_state_unlocked', lambda: None)
    assert codex._codex_app_server_resolve_approval('s', 'accept', '0', '0nonce')['ok']
    assert sent[0]['id'] == 0


def test_old_hook_waiting_record_refused(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('CCC_APPROVAL_HOOK_DIR', str(tmp_path))
    sha = 'a' * 64
    folder = tmp_path / 'waiting'
    folder.mkdir()
    (folder / approval_hook._name('s', sha)).write_text(json.dumps({'sha256': sha}))
    assert approval_hook.waiting('s', sha)
    assert not approval_hook.waiting('s', sha, nonce='b' * 32)


def test_identical_claude_replacement_rejects_old_alert(tmp_path, monkeypatch):
    import json
    import runpy
    from pathlib import Path
    hook = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'hooks' / 'permission-request.py'))
    monkeypatch.setenv('CCC_APPROVAL_HOOK_DIR', str(tmp_path))
    sha = hook['input_sha256']('Bash', {'command': 'echo hello'})
    old, new = 'a' * 32, 'b' * 32
    folder = tmp_path / 'waiting'
    folder.mkdir()
    waiting = folder / approval_hook._name('s', sha)
    waiting.write_text(json.dumps({'sha256': sha, 'nonce': new}))
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'claude')
    monkeypatch.setattr(server, '_notification_is_blocking', lambda row: True)
    monkeypatch.setattr(server, '_read_notification_state', lambda sid: {'sha256': sha, 'nonce': old, 'message': 'Bash: echo hello'})
    assert server._OncallDeps().permission('s') is None
    monkeypatch.setattr(server, '_read_notification_state', lambda sid: {'sha256': sha, 'nonce': new, 'message': 'Bash: echo hello'})
    assert server._OncallDeps().permission('s')['meta']['nonce'] == new
