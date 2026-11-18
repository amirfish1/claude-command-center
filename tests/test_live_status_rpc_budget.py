"""Slow worker reads cannot consume one engine deadline per live row."""
import json
import socket
import threading
import time
from unittest import mock

import pytest

import server
from ccc_server import codex
from control_plane import ControlPlaneClient


def test_live_build_shares_budget_across_rows(tmp_path):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock_path = tmp_path / 'worker.sock'
    listener.bind(str(sock_path))
    listener.listen()
    release = threading.Event()
    requests = []
    token = tmp_path / 'token'
    token.write_text('test-local-auth')

    def stalled_worker():
        conn, _ = listener.accept()
        with conn:
            requests.append(json.loads(conn.makefile('rb').readline()))
            release.wait(3)

    worker = threading.Thread(target=stalled_worker, daemon=True)
    worker.start()
    replies = []

    def probe(_sid):
        replies.append(server._control_plane_engine_call(
            'codex', 'thread_states', {}, mutate=False))
        # The real probe can fall back to current rollout/process evidence.
        return True

    try:
        with mock.patch.object(server, '_CONTROL_PLANE_ENGINE_CLIENT',
                               ControlPlaneClient(path=sock_path, token_file=token, timeout=45)), \
             mock.patch.object(server, '_discover_live_session_ids', return_value={'one', 'two', 'three'}), \
             mock.patch.object(server, '_codex_pool_candidate_sids', return_value=set()), \
             mock.patch.object(server, '_archive_session_is_live', side_effect=probe), \
             mock.patch.object(server, '_live_activity_entry_for_session', return_value={'is_live': True}):
            started = time.monotonic()
            result = server._build_live_sessions_activity_uncached()
            elapsed = time.monotonic() - started
        assert elapsed < 1, f'worker stalled live build for {elapsed:.3f}s'
        assert len(result) == 3
        assert all(row['is_live'] for row in result.values())
        assert len(requests) == 1
        assert len(replies) == 3 and not any(reply['ok'] for reply in replies)
        assert replies[-1]['error'] == 'Live-status worker read budget exhausted'
        assert getattr(server._CONTROL_PLANE_REQUEST_CONTEXT, 'status_deadline', None) is None
    finally:
        release.set()
        worker.join(2)
        listener.close()


def test_status_budget_does_not_shorten_actions_or_other_threads():
    calls = []
    def request(method, payload, **kwargs):
        calls.append((method, kwargs))
        return {'ok': True}

    with mock.patch.object(server, '_control_plane_request', side_effect=request):
        with server._control_plane_status_budget():
            server._control_plane_engine_call('codex', 'resume', {}, mutate=True)
            thread = threading.Thread(target=lambda: server._control_plane_engine_call(
                'codex', 'thread_states', {}, mutate=False))
            thread.start()
            thread.join(2)
        server._control_plane_engine_call('codex', 'thread_states', {}, mutate=False)
    assert calls == [('engine.execute', {'engine_timeout': True}),
                     ('engine.query', {'engine_timeout': True}),
                     ('engine.query', {'engine_timeout': True})]


def test_status_budget_restored_after_nested_error():
    with server._control_plane_status_budget():
        outer = server._CONTROL_PLANE_REQUEST_CONTEXT.status_deadline
        with pytest.raises(RuntimeError):
            with server._control_plane_status_budget():
                assert server._CONTROL_PLANE_REQUEST_CONTEXT.status_deadline == outer
                raise RuntimeError('test')
        assert server._CONTROL_PLANE_REQUEST_CONTEXT.status_deadline == outer
    assert server._CONTROL_PLANE_REQUEST_CONTEXT.status_deadline is None


def test_timed_out_status_read_does_not_poison_action_snapshot():
    cached = {'ts': time.time() - 10, 'map': {'test-session': {'status': 'idle'}}}
    with mock.patch.object(codex, '_codex_thread_state_remote', dict(cached)), \
         mock.patch.object(server, '_control_plane_routes_engines', return_value=True), \
         mock.patch.object(server, '_control_plane_engine_call', side_effect=[
             {'ok': False, 'available': True, 'error': 'timed out'},
             {'ok': True, 'states': {'test-session': {'status': 'active'}}},
         ]) as rpc:
        with server._control_plane_status_budget():
            codex._codex_thread_state_map_resolved()
        assert codex._codex_thread_state_remote == cached
        result = codex._codex_thread_state_map_resolved()
        assert result['test-session']['status'] == 'active'
        assert rpc.call_count == 2
