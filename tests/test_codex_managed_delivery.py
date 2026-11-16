"""Managed delivery without local executable authority; offline native adapter."""
from pathlib import Path
from unittest.mock import Mock

import pytest
import server
from ccc_server import codex_managed_recovery as recovery
from ccc_server import codex_client
from tests.test_codex_managed_recovery import isolated, identity, Native


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    monkeypatch.setenv('CCC_WORKER_PROCESS', '1')
    socket_path = tmp_path / 'native.sock'
    monkeypatch.setenv('CCC_CODEX_APP_SERVER_SOCKET', str(socket_path))
    monkeypatch.setattr(server, '_read_registry_pruned', lambda: [])
    monkeypatch.setattr(server, '_codex_managed_app_server_enabled', lambda: True)
    native = Native()
    native.sock = object()
    native.managed_socket_path = str(socket_path)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_TRANSPORT', native)
    monkeypatch.setattr(server, '_CODEX_APP_SERVER_INITIALIZED', True)
    ensure = Mock(return_value=native)
    monkeypatch.setattr(server, '_ensure_codex_app_server', ensure)
    monkeypatch.setattr(recovery, 'observe_peer', lambda *a: dict(native.managed_identity))
    cli = Mock(side_effect=AssertionError('managed delivery attempted local CLI resolution'))
    monkeypatch.setattr(server, '_resolve_codex_bin', cli)
    monkeypatch.setattr(server, '_spawned_sessions', [])
    monkeypatch.setattr(server, '_codex_thread_row', lambda _: {'cwd': str(tmp_path), 'model': 'fixture'})
    monkeypatch.setattr(server, '_spawn_registry_entry_for_session', lambda *a: {})
    monkeypatch.setattr(server, '_codex_capture_thread_row', lambda _: {})
    monkeypatch.setattr(server, '_resolve_codex_rollout_path', lambda _: None)
    monkeypatch.setattr(server, '_get_session_override', lambda _: None)
    monkeypatch.setattr(server, '_model_policy_blocks', lambda _: False)
    monkeypatch.setattr(server, '_resume_ledger_append', lambda *a, **k: None)
    monkeypatch.setattr(server, '_extract_pasted_image_paths', lambda _: [])
    monkeypatch.setattr(server, '_codex_relay_subagent_input', lambda *a, **k: None)
    desktop = Mock(side_effect=AssertionError('authenticated managed delivery changed to desktop'))
    monkeypatch.setattr(codex_client, 'resume_desktop_conversation', desktop)
    calls = []
    def send(sid, text, **kwargs):
        calls.append((sid, text))
        # Actual managed before_send/response persist and acknowledge the key.
        result = server._codex_app_server_request_to_transport(native, 'turn/start', {
            'threadId': sid, 'clientUserMessageId': 'offline-delivery',
            'input': [{'type': 'text', 'text': text}]}, timeout=.02)
        return {'ok': 'result' in result, 'response': result}
    monkeypatch.setattr(server, '_codex_resume_or_steer_via_app_server', send)
    return native, ensure, cli, calls, socket_path, desktop


def test_authenticated_managed_delivery_uses_no_local_cli_and_preserves_claim(delivery):
    native, ensure, cli, calls, _, desktop = delivery
    result = server.resume_session_codex('thread-a', 'Disposable input', _native_delivery=True, _from_queue=True)
    assert result['ok'] is True
    ensure.assert_called_once_with(allow_stdio=False)
    assert calls == [('thread-a', 'Disposable input')]
    cli.assert_not_called()
    desktop.assert_not_called()
    assert len(native.sent) == 1
    assert native.sent[0]['params']['clientUserMessageId'] == 'offline-delivery'
    rows = recovery._records('thread-a')
    assert len(rows) == 1
    assert rows[0][1]['managed']['phase'] == 'acknowledged'
    assert rows[0][1]['managed']['daemon'] == native.managed_identity


@pytest.mark.parametrize('failure', ['absent', 'stdio', 'dead', 'uninitialized', 'recovering',
    'path', 'identity', 'epoch', 'unsupported', 'incompatible', 'disabled', 'connect-error'])
def test_configured_socket_never_substitutes_for_authenticated_ready_peer(delivery, monkeypatch, failure):
    native, ensure, cli, calls, socket_path, desktop = delivery
    # A configured existing filesystem entry still does not attest a daemon.
    socket_path.touch()
    if failure == 'absent': ensure.return_value = None
    elif failure == 'stdio': native.kind = 'stdio'
    elif failure == 'dead': native.alive = lambda: False
    elif failure == 'uninitialized': monkeypatch.setattr(server, '_CODEX_APP_SERVER_INITIALIZED', False)
    elif failure == 'recovering': native.managed_recovering = True
    elif failure == 'path': native.managed_socket_path = str(socket_path.parent / 'other.sock')
    elif failure == 'identity': monkeypatch.setattr(recovery, 'observe_peer', lambda *a: dict(native.managed_identity, socket_inode=999))
    elif failure == 'epoch': native.managed_epoch = '0' * 64
    elif failure == 'unsupported': native.managed_identity['binary_sha256'] = '0' * 64
    elif failure == 'incompatible': monkeypatch.setattr(server, '_read_registry_pruned', lambda: [{'pid': 999999, 'managed_recovery_protocol': 0}])
    elif failure == 'disabled': monkeypatch.setattr(server, '_codex_managed_app_server_enabled', lambda: False)
    elif failure == 'connect-error': ensure.side_effect = OSError('offline connection refused')
    result = server.resume_session_codex('thread-a', 'Disposable input', _native_delivery=True, _from_queue=True)
    assert result['ok'] is False and result['code'] == 'managed_needs_reconciliation'
    assert result['sent'] is False and result['fallback'] == 'queue'
    assert calls == [] and native.sent == [] and recovery._records('thread-a') == []
    cli.assert_not_called()
    desktop.assert_not_called()


def test_stdio_cli_requirement_is_unchanged(delivery, monkeypatch):
    native, ensure, cli, calls, _, desktop = delivery
    monkeypatch.delenv('CCC_CODEX_APP_SERVER_SOCKET')
    native.kind = 'stdio'
    cli.side_effect = None
    cli.return_value = {'available': False, 'reason': 'offline CLI missing', 'code': 'codex_unavailable'}
    result = server.resume_session_codex('thread-a', 'Disposable input', _native_delivery=True, _from_queue=True)
    assert result == {'ok': False, 'error': 'offline CLI missing', 'code': 'codex_unavailable'}
    cli.assert_called_once()
    ensure.assert_not_called()
    desktop.assert_not_called()
    assert not calls
