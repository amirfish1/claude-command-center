"""Restart recovery with abandoned reads and genuinely stuck handlers."""
import socket
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import server
from tests.test_ccc_restart_cli import _load_ccc_cli


@pytest.mark.parametrize('method,connected,pending', [
    ('GET', False, False), ('HEAD', False, False),
    ('POST', False, True), ('GET', True, True),
])
def test_disconnected_reads_do_not_hold_drain(monkeypatch, method, connected, pending):
    monkeypatch.setattr(server, '_INFLIGHT_REQUESTS', {})
    monkeypatch.setattr(server, '_INFLIGHT_READ_SOCKETS', {})
    monkeypatch.setattr(server, '_ACCEPT_PAUSED', server.threading.Event())
    conn, peer = socket.socketpair()
    try:
        h = SimpleNamespace(path='/stuck', command=method, connection=conn)
        assert server._inflight_begin(h)
        if not connected:
            peer.close()
        assert bool(server._drain_inflight_requests(timeout=0)) is pending
        server._inflight_end(h)
        assert not server._INFLIGHT_READ_SOCKETS
    finally:
        peer.close()
        conn.close()


@pytest.mark.parametrize('force', [False, True])
def test_explicit_recovery_proceeds_after_timeout_but_routine_cancels(monkeypatch, force):
    monkeypatch.setattr(server, '_INFLIGHT_REQUESTS', {1: '/stuck'})
    monkeypatch.setattr(server, '_INFLIGHT_READ_SOCKETS', {})
    monkeypatch.setattr(server, '_ACCEPT_PAUSED', server.threading.Event())
    monkeypatch.setattr(server, '_RESTART_OUTCOME', {})
    monkeypatch.setattr(server, '_HTTPD_SERVER', SimpleNamespace(socket=None))
    quiesce = server._quiesce_http_for_restart
    monkeypatch.setattr(server, '_quiesce_http_for_restart', lambda **kw: quiesce(0, **kw))
    monkeypatch.setattr(server.threading, 'Timer', lambda delay, fn: SimpleNamespace(start=fn))
    execute = Mock()
    monkeypatch.setattr(server.os, 'execvp', execute)
    monkeypatch.setattr(server.sys, 'platform', 'linux')
    from ccc_server import restart_audit
    monkeypatch.setattr(restart_audit, 'record_restart', Mock())
    server._schedule_restart(force=force)
    assert execute.called is force
    assert server._ACCEPT_PAUSED.is_set() is force
    if not force:
        assert server._RESTART_OUTCOME == {'state': 'cancelled', 'reason': 'requests are still running'}


@pytest.mark.parametrize('outcome,status', [('cancelled', 503), ('failed', 503), (None, 504)])
def test_cli_never_reports_cancelled_or_unverified_restart_as_success(monkeypatch, capsys, outcome, status):
    cli = _load_ccc_cli()
    monkeypatch.setattr(cli, '_post_restart', lambda *a, **kw: (200, {'ok': True, 'before': {'pid': 7}}))
    back = {'pid': 7, 'restart_outcome': {'state': outcome, 'reason': 'stuck requests'}} if outcome else None
    monkeypatch.setattr(cli, '_wait_dashboard_back', lambda *a, **kw: back)
    note = Mock()
    monkeypatch.setattr(cli, '_note_dashboard_back', note)
    assert cli._restart_now('base', 'dashboard')[0] == status
    note.assert_not_called()
    assert 'dashboard back' not in capsys.readouterr().out


def test_cli_observes_cancellation_from_version_endpoint(monkeypatch):
    cli = _load_ccc_cli()
    versions = iter([
        {'pid': 7, 'started_at': 10},
        {'pid': 7, 'started_at': 10, 'restart_outcome': {'state': 'cancelled', 'reason': 'busy'}},
    ])
    monkeypatch.setattr(cli, '_get_json', lambda *a, **kw: next(versions))
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    result = cli._wait_dashboard_back('base', 7, before_started_at=10)
    assert result['restart_outcome']['state'] == 'cancelled'
