"""Real sockets retain concurrent streams and drain behavior with spare workers."""
from contextlib import closing
import http.client
import http.server
import threading

import pytest

from ccc_server.http_admission import ConnectionAdmissionMixin
from ccc_server.http_read_keepalive import ReadKeepAliveMixin


@pytest.fixture
def service():
    class Handler(ReadKeepAliveMixin, http.server.BaseHTTPRequestHandler):
        read_idle_timeout = 0.1

        def log_message(self, *_):
            pass

        def do_GET(self):
            self.server.visits.append((self.path, threading.get_ident()))
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            if self.path == '/hold':
                self.server.release.wait(timeout=10)
            self.wfile.write(b'ok')

    class Server(ConnectionAdmissionMixin, http.server.ThreadingHTTPServer):
        pass

    server = Server(('127.0.0.1', 0), Handler)
    server.release = threading.Event()
    server.visits = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        workers = server._connection_workers
        with workers.condition:
            assert workers.condition.wait_for(lambda: workers.live == 0, timeout=3)
        assert not workers.manager.is_alive()


def read(server, path='/read'):
    with closing(http.client.HTTPConnection(*server.server_address, timeout=3)) as conn:
        conn.request('GET', path)
        response = conn.getresponse()
        assert response.status == 200
        assert response.read() == b'ok'


def test_short_connections_use_ready_workers_without_creating_threads(service, monkeypatch):
    def no_start(*_):
        raise AssertionError('a short read created a thread')
    monkeypatch.setattr(threading.Thread, 'start', no_start)
    for _ in range(20):
        read(service)
    assert len(service.visits) == 20
    assert not service._connection_workers.failed


def test_streams_exceed_spare_count_without_blocking_new_requests(service):
    connections = []
    try:
        for _ in range(service.connection_spares + 4):
            conn = http.client.HTTPConnection(*service.server_address, timeout=3)
            conn.request('GET', '/hold')
            response = conn.getresponse()
            assert response.status == 200
            connections.append((conn, response))
        # A fixed-size executor would be full of streams and time out here.
        read(service)
    finally:
        service.release.set()
        for conn, response in connections:
            assert response.read() == b'ok'
            conn.close()


def test_accepting_does_not_wait_for_spare_thread_start(service, monkeypatch):
    entered, resume = threading.Event(), threading.Event()
    original = threading.Thread.start

    def delayed_start(thread):
        if thread.name == 'process_request_thread':
            entered.set()
            assert resume.wait(timeout=5)
        return original(thread)

    monkeypatch.setattr(threading.Thread, 'start', delayed_start)
    connections = []
    try:
        # Refill starts after half the initial workers enter held connections.
        for _ in range(5):
            conn = http.client.HTTPConnection(*service.server_address, timeout=3)
            conn.request('GET', '/hold')
            response = conn.getresponse()
            assert response.status == 200
            connections.append((conn, response))
        assert entered.wait(timeout=3)
        read(service)
        assert not resume.is_set()
    finally:
        resume.set()
        service.release.set()
        for conn, response in connections:
            response.read()
            conn.close()


def test_connection_reuse_keeps_original_protocol_reset(service):
    with closing(http.client.HTTPConnection(*service.server_address, timeout=3)) as conn:
        for _ in range(2):
            conn.request('GET', '/static/example.js')
            response = conn.getresponse()
            assert response.version == 11 and not response.will_close
            assert response.read() == b'ok'
        conn.request('GET', '/api/example')
        response = conn.getresponse()
        assert response.version == 10 and response.will_close
        assert response.read() == b'ok'
        assert len({ident for _, ident in service.visits}) == 1


def test_server_close_does_not_join_an_active_stream(service):
    conn = http.client.HTTPConnection(*service.server_address, timeout=3)
    try:
        conn.request('GET', '/hold')
        response = conn.getresponse()
        assert response.status == 200
        service.shutdown()
        service.server_close()
        assert not service.release.is_set()
        service.release.set()
        assert response.read() == b'ok'
    finally:
        service.release.set()
        conn.close()


def test_spare_creation_failure_retires_pool_and_falls_back(service, monkeypatch):
    original = threading.Thread.start

    def cannot_start(thread):
        if thread.name == 'process_request_thread':
            raise RuntimeError('test thread resource exhaustion')
        return original(thread)

    monkeypatch.setattr(threading.Thread, 'start', cannot_start)
    connections = []
    try:
        for _ in range(5):
            conn = http.client.HTTPConnection(*service.server_address, timeout=3)
            conn.request('GET', '/hold')
            response = conn.getresponse()
            connections.append((conn, response))
        workers = service._connection_workers
        with workers.condition:
            assert workers.condition.wait_for(lambda: workers.closed, timeout=3)
        assert workers.failed
        monkeypatch.setattr(threading.Thread, 'start', original)
        read(service)
    finally:
        service.release.set()
        for conn, response in connections:
            response.read()
            conn.close()


@pytest.mark.parametrize('fail_manager', [False, True])
def test_initial_worker_failure_closes_started_spares(monkeypatch, fail_manager):
    original = threading.Thread.start
    started = []

    def failing_start(thread):
        if ((fail_manager and thread.name == 'http-admission') or
                (not fail_manager and thread.name == 'process_request_thread'
                 and len(started) == 3)):
            raise RuntimeError('test startup resource exhaustion')
        result = original(thread)
        if thread.name == 'process_request_thread':
            started.append(thread)
        return result

    monkeypatch.setattr(threading.Thread, 'start', failing_start)
    class Server(ConnectionAdmissionMixin, http.server.ThreadingHTTPServer):
        pass
    with pytest.raises(RuntimeError, match='test startup resource exhaustion'):
        Server(('127.0.0.1', 0), http.server.BaseHTTPRequestHandler)
    assert started
    for thread in started:
        thread.join(timeout=3)
        assert not thread.is_alive()
