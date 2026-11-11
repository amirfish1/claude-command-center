"""On-call answers must remain bound to the request that raised the alert."""
from types import SimpleNamespace

import server
from ccc_server import approval_hook, codex, session_graph
from worker_engines import EngineHost


def pending(request_id):
    return {"id": str(request_id), "request_id": str(request_id),
            "request_id_raw": request_id, "needs_approval": True,
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
    assert calls == [(('s', 'accept'), {'expected_request_id': 'old'})]


def test_claude_exact_hash_only_and_no_terminal_fallback(monkeypatch):
    old, new = 'a' * 64, 'b' * 64
    monkeypatch.setattr(session_graph, 'isClaudeSource_backend', lambda sid: True)
    monkeypatch.setattr(approval_hook, 'waiting', lambda sid, sha: sha == new)
    delivered = []
    monkeypatch.setattr(approval_hook, 'deliver', lambda *args: delivered.append(args) or 'applied')
    monkeypatch.setattr(session_graph, '_answer_via_approval_hook',
                        lambda *a: (_ for _ in ()).throw(AssertionError('fallback')))
    assert not session_graph.respond_claude_permission('s', 'accept', expected_hook_sha=old)['ok']
    assert session_graph.respond_claude_permission('s', 'decline', expected_hook_sha=new)['ok']
    assert delivered == [('s', new, 'deny')]


def test_claude_unbound_prompt_does_not_raise_alert(monkeypatch):
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: 'claude')
    monkeypatch.setattr(server, '_read_notification_state', lambda sid: {'message': 'tool'})
    monkeypatch.setattr(server, '_notification_is_blocking', lambda row: True)
    monkeypatch.setattr(approval_hook, 'waiting', lambda sid, sha: False)
    assert server._OncallDeps().permission('s') is None


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
