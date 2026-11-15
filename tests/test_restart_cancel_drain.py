"""A cancelled dashboard restart releases only the drain it acquired."""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

import server
from ccc_worker import WorkerRuntime, WorkerServer
from control_plane import ControlPlaneClient, WorkLedger


@pytest.mark.parametrize('replacement', [None, 'other', 'same-reason'])
def test_cancelled_restart_releases_only_its_drain(tmp_path, monkeypatch, replacement):
    runtime = WorkerRuntime(WorkLedger(tmp_path / 'work.sqlite'), token='test')
    engines = Mock()
    engines.dispatch_queued.return_value = []
    runtime._engine_host = engines
    monkeypatch.setattr(server, '_dashboard_owned_active_executions', lambda: [])
    # Adoption itself belongs to the engine host, not the restart mechanics.
    def request(method, params=None, **kwargs):
        if method == 'engine.adopt':
            return {'ok': True, 'available': True}
        return runtime.dispatch(method, params or {})
    monkeypatch.setattr(server, '_control_plane_request', request)
    ok, _, _, protected = server._safe_worker_restart_precheck('dashboard-restart', restarts_worker=False)
    assert ok
    owned = runtime.ledger.drain_state()
    if replacement:
        reason = owned['reason'] if replacement == 'same-reason' else 'operator pause'
        current = runtime.ledger.set_drain(True, reason)
        assert current != owned
    else:
        current = owned
    handler = SimpleNamespace(path='/accepted-write')
    monkeypatch.setattr(server, '_HTTPD_SERVER', object())
    assert server._inflight_begin(handler)
    quiesce = server._quiesce_http_for_restart
    monkeypatch.setattr(server, '_quiesce_http_for_restart', lambda: quiesce(0))
    monkeypatch.setattr(server.threading, 'Timer',
                        lambda delay, fn: SimpleNamespace(start=fn))
    execute = Mock()
    monkeypatch.setattr(server.os, 'execvp', execute)
    try:
        server._schedule_restart(protected=protected)
        execute.assert_not_called()
        assert not server._ACCEPT_PAUSED.is_set()
        assert '/accepted-write' in server._inflight_snapshot()
        if replacement:
            assert runtime.ledger.drain_state() == current
            engines.dispatch_queued.assert_not_called()
        else:
            assert not runtime.ledger.drain_state()['enabled']
            engines.dispatch_queued.assert_called_once()
    finally:
        server._inflight_end(handler)
        server._ACCEPT_PAUSED.clear()


def test_release_without_ownership_cannot_resume_worker(tmp_path):
    runtime = WorkerRuntime(WorkLedger(tmp_path / 'work.sqlite'), token='test')
    current = runtime.ledger.set_drain(True, 'operator pause')
    token = tmp_path / 'token'
    token.write_text('test')
    worker = WorkerServer(tmp_path / 'worker.sock', runtime)
    thread = threading.Thread(target=worker.serve_forever, daemon=True)
    thread.start()
    client = ControlPlaneClient(path=tmp_path / 'worker.sock', token_file=token)
    try:
        for expected in (None, {}, {'enabled': False}):
            result = client.request('drain.release', {'expected': expected})
            assert result['released'] is False
            assert runtime.ledger.drain_state() == current
        result = client.request('drain.release', {'expected': current})
        assert result['released'] is True
        assert not client.request('health')['drain']['enabled']
    finally:
        worker.shutdown()
        worker.server_close()
        thread.join(timeout=5)
