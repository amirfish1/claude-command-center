"""Server adapters use the existing guarded approval entry points."""
import importlib

import pytest

server = importlib.import_module('server')


@pytest.mark.parametrize('engine', ['claude', 'codex'])
@pytest.mark.parametrize('decision,expected', [('allow', 'accept'), ('deny', 'decline')])
def test_approval_adapter_routes_and_refuses_stale(monkeypatch, engine, decision, expected):
    calls = []
    monkeypatch.setattr(server, '_detect_session_engine', lambda sid: engine)
    monkeypatch.setattr(server, '_read_notification_state', lambda sid: {
        'started_at': 'request-1', 'message': 'Bash: echo hello', 'sha256': 'request-1', 'nonce': 'abc'})
    monkeypatch.setattr(server, '_notification_is_blocking', lambda row: True)
    from ccc_server import approval_hook
    monkeypatch.setattr(approval_hook, 'waiting', lambda sid, sha, **kw: sha == 'request-1')
    monkeypatch.setattr(server, '_codex_thread_state_resolved', lambda sid: {})
    monkeypatch.setattr(server, '_codex_app_server_pending_approval_item', lambda state: {
        'request_id': 'request-1', 'approval_nonce': 'abc', 'command': 'echo hello'})
    def resolve(sid, action, **kwargs):
        calls.append((sid, action, kwargs))
        return {'ok': True}
    monkeypatch.setattr(server, '_codex_app_server_resolve_approval', resolve)
    monkeypatch.setattr(server, 'respond_claude_permission', resolve)
    deps = server._OncallDeps()
    signal = deps.permission('s')
    assert deps.approve('s', decision, signal['meta']) == (True, '')
    key = 'expected_hook_sha' if engine == 'claude' else 'expected_request_id'
    nonce_key = 'expected_hook_nonce' if engine == 'claude' else 'expected_nonce'
    assert calls == [('s', expected, {key: 'request-1', nonce_key: 'abc'})]
    assert not deps.approve('s', decision, {'engine': engine, 'prompt': 'old'})[0]
    assert len(calls) == 1


def test_ephemeral_background_loop_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv('CCC_ONCALL_DISABLED', raising=False)
    monkeypatch.delenv('CCC_EPHEMERAL', raising=False)
    monkeypatch.delenv('CCC_ONCALL_EPHEMERAL', raising=False)
    assert server._oncall_background_enabled()
    monkeypatch.setenv('CCC_EPHEMERAL', '1')
    assert not server._oncall_background_enabled()
    monkeypatch.setenv('CCC_ONCALL_EPHEMERAL', '1')
    assert server._oncall_background_enabled()
    monkeypatch.setenv('CCC_ONCALL_DISABLED', '1')
    assert not server._oncall_background_enabled()
