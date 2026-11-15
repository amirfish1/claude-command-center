"""A background admission backlog must not hold up a safe restart."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import threading

import pytest
import server
from ccc_server.http_read_admission import ReadDeferredForRestart


@pytest.mark.parametrize('gate', ['route', 'slots', 'anonymous'])
def test_restart_releases_waiting_reads_without_executing_them(monkeypatch, gate):
    waiting = threading.Event()
    stopping = threading.Event()
    lock = threading.Lock()
    lock.acquire()

    class Gate:
        def acquire(self, **kwargs):
            waiting.set()
            return lock.acquire(**kwargs)

        def release(self):
            lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *args):
            self.release()

    monkeypatch.setattr(server, '_ACCEPT_PAUSED', stopping)
    monkeypatch.setattr(server, '_BG_ROUTE_LOCKS', {'/queued': Gate()} if gate == 'route' else {})
    monkeypatch.setattr(server, '_background_api_read_slots',
                        threading.Semaphore(1) if gate == 'route' else Gate())
    monkeypatch.setattr(server, '_BG_ROUTE_CPU', {})
    executed = []
    pool = ThreadPoolExecutor()
    try:
        future = pool.submit(server._run_api_request, True,
                             lambda: executed.append(True),
                             None if gate == 'anonymous' else '/queued')
        assert waiting.wait(5)
        stopping.set()
        with pytest.raises(ReadDeferredForRestart):
            future.result(timeout=5)
        assert lock.locked(), 'another request still owns the admission gate'
    finally:
        stopping.set()
        if lock.locked():
            lock.release()
        pool.shutdown()
    assert not executed
    assert '/queued' not in server._BG_ROUTE_CPU
    if gate != 'anonymous':
        assert server._BG_ROUTE_LOCKS['/queued'].acquire(timeout=1)
        server._BG_ROUTE_LOCKS['/queued'].release()


def test_http_drain_retries_queued_reads_and_finishes_running_read_and_write(monkeypatch):
    stopping = threading.Event()
    reading = threading.Event()
    writing = threading.Event()
    queued = threading.Event()
    finish = threading.Event()
    monkeypatch.setattr(server, '_ACCEPT_PAUSED', stopping)
    monkeypatch.setattr(server, '_INFLIGHT_REQUESTS', {})
    monkeypatch.setattr(server, '_BG_ROUTE_LOCKS', {})
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
