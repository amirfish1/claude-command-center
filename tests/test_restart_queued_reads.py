"""A background admission backlog must not hold up a safe restart."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import threading

import pytest
import server
from ccc_server.http_read_admission import ReadAdmission, ReadDeferredForRestart


@pytest.mark.parametrize('gate', ['route', 'slots', 'anonymous'])
def test_restart_releases_waiting_reads_without_executing_them(monkeypatch, gate):
    waiting = threading.Event()
    stopping = ReadAdmission(1, 1)
    monkeypatch.setattr(server, '_ACCEPT_PAUSED', stopping)
    monkeypatch.setattr(server, '_BG_ROUTE_CPU', {})
    monkeypatch.setattr(server, '_save_bg_route_cpu', lambda: None)
    executed = []
    holder_route = '/queued' if gate == 'route' else None
    request_route = None if gate == 'anonymous' else '/queued'
    with ThreadPoolExecutor() as pool, stopping.slot(holder_route):
        held = stopping._routes['/queued'] if holder_route else stopping._shared
        wait = held.ready.wait

        def wait_for_slot():
            waiting.set()
            return wait()

        monkeypatch.setattr(held.ready, 'wait', wait_for_slot)
        future = pool.submit(server._run_api_request, True,
                             lambda: executed.append(True), request_route)
        try:
            assert waiting.wait(5)
        finally:
            stopping.set()
        stopping.clear()
        with pytest.raises(ReadDeferredForRestart):
            future.result(timeout=5)
        assert held.available == 0, 'another request still owns the admission gate'
    assert not executed
    assert '/queued' not in server._BG_ROUTE_CPU
    assert server._run_api_request(True, lambda: 'ok', request_route) == 'ok'


def test_http_drain_retries_queued_reads_and_finishes_running_read_and_write(monkeypatch):
    stopping = ReadAdmission(2, 256)
    reading = threading.Event()
    writing = threading.Event()
    queued = threading.Event()
    finish = threading.Event()
    monkeypatch.setattr(server, '_ACCEPT_PAUSED', stopping)
    monkeypatch.setattr(server, '_INFLIGHT_REQUESTS', {})
    monkeypatch.setattr(server, '_BG_ROUTE_CPU', {})
    monkeypatch.setattr(server, '_save_bg_route_cpu', lambda: None)
    executed = []

    class Handler(server.CommandCenterHandler):
        def _check_proxy_secret(self):
            return True

        def _phone_pin_blocked(self, path):
            return False

        def do_GET(self):
            if reading.is_set():
                queued.set()
            return super().do_GET()

        def _do_GET(self):
            executed.append('read')
            reading.set()
            assert finish.wait(10)
            self.send_json({'read': True})

        def do_POST(self):
            writing.set()
            assert finish.wait(10)
            executed.append('saved')
            self.send_json({'saved': True})

        def log_message(self, *args):
            pass

    httpd = server.ThreadedHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def request(method):
        conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=10)
        try:
            conn.request(method, '/api/session-status', body=b'' if method == 'POST' else None)
            reply = conn.getresponse()
            return reply.status, reply.getheader('X-CCC-Restarting'), json.loads(reply.read())
        finally:
            conn.close()

    try:
        with ThreadPoolExecutor() as pool:
            active = pool.submit(request, 'GET')
            assert reading.wait(5)
            write = pool.submit(request, 'POST')
            assert writing.wait(5)
            pending = pool.submit(request, 'GET')
            assert queued.wait(5)
            draining = pool.submit(server._quiesce_http_for_stop, 5)
            status, header, body = pending.result(timeout=5)
            assert (status, header, body['restarting']) == (503, '1', True)
            assert not draining.done(), 'active read and write must finish first'
            finish.set()
            assert active.result(timeout=5) == (200, None, {'read': True})
            assert write.result(timeout=5) == (200, None, {'saved': True})
            assert draining.result(timeout=5) == []
        assert executed == ['read', 'saved']
        assert server._inflight_snapshot() == []
    finally:
        finish.set()
        stopping.clear()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
