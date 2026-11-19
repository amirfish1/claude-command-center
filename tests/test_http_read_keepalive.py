"""Real HTTP clients exercise framing, access checks, reuse and idle release."""
import http.client
import http.server
import json
import socket
from contextlib import closing
import threading

import pytest

from ccc_server.http_read_keepalive import ReadKeepAliveMixin


@pytest.fixture
def service():
    class Handler(ReadKeepAliveMixin, http.server.BaseHTTPRequestHandler):
        read_idle_timeout = 0.15

        def log_message(self, *_):
            pass

        def do_GET(self):
            self.server.requests.append((self.path, threading.get_ident()))
            self.server.timeouts.append(self.connection.gettimeout())
            if self.path == '/view/unframed':
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'unframed')
                return
            status = 200 if self.headers.get('X-Test-Authorized') == 'test-access' else 403
            if self.path == '/static/not-modified':
                self.send_response(304)
                self.end_headers()
                return
            body = self.path.encode()
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.send_response(200)
            self.send_header('Content-Length', '6')
            self.end_headers()
            self.wfile.write(b'posted')

    class Server(http.server.ThreadingHTTPServer):
        def get_request(self):
            self.accepts += 1
            return super().get_request()

    server = Server(('127.0.0.1', 0), Handler)
    server.accepts = 0
    server.requests = []
    server.timeouts = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(conn, path, *, method='GET', headers=None, body=None):
    conn.request(method, path, headers=headers or {}, body=body)
    response = conn.getresponse()
    payload = response.read()
    return response, payload


def test_reuses_connection_and_handler_but_checks_each_request(service):
    with closing(http.client.HTTPConnection(*service.server_address, timeout=2)) as conn:
        for path in ['/static/one.js', '/?ccc_app=demo', '/view/demo',
                     '/?ccc_popout=conversation&embed=app&conv=example', '/app-static/demo/items.json',
                     '/api/apps/data?app=demo&key=items', '/api/apps/routines?app=demo',
                     '/api/conversations/example?tail=120']:
            response, body = request(conn, path, headers={'X-Test-Authorized':'test-access'})
            assert response.status == 200
            assert response.version == 11
            assert not response.will_close
            assert body == path.encode()
        denied, _ = request(conn, '/view/demo')
        assert denied.status == 403
        assert service.accepts == 1
        assert len({ident for _, ident in service.requests}) == 1


@pytest.mark.parametrize('method,path,headers,body', [
    ('GET', '/api/health', {}, None),
    ('GET', '/api/conversations/list', {}, None),
    ('GET', '/api/conversations/all', {}, None),
    ('GET', '/api/conversations/order', {}, None),
    ('GET', '/api/conversations/example/stream', {}, None),
    ('GET', '/api/apps/routines/run', {}, None),
    ('POST', '/view/demo', {}, 'input'),
    ('GET', '/view/demo', {'Connection':'close'}, None),
    ('GET', '/view/demo', {'Connection':'upgrade, Close'}, None),
    ('GET', '/view/demo', {'Content-Length':'1'}, 'x'),
    ('GET', '/view/demo', {'Expect':'100-continue'}, None),
    ('GET', '/view/demo', {'Transfer-Encoding':'chunked'}, '0\r\n\r\n'),
])
def test_next_request_retains_original_non_reuse_behavior(service, method, path, headers, body):
    with closing(http.client.HTTPConnection(*service.server_address, timeout=2)) as conn:
        response, _ = request(conn, '/static/one.js')
        assert not response.will_close
        response, _ = request(conn, path, method=method, headers=headers, body=body)
        assert response.version == 10
        assert response.will_close


def test_unframed_response_closes_and_304_keeps_framing(service):
    with closing(http.client.HTTPConnection(*service.server_address, timeout=2)) as conn:
        response, body = request(conn, '/static/not-modified')
        assert response.status == 304 and not response.will_close and body == b''
        response, body = request(conn, '/view/unframed')
        assert response.will_close and body == b'unframed'
        assert service.accepts == 1


def test_idle_connection_is_released_without_another_request(service):
    with closing(http.client.HTTPConnection(*service.server_address, timeout=2)) as conn:
        response, _ = request(conn, '/static/one.js')
        assert not response.will_close
        conn.sock.settimeout(2)
        assert conn.sock.recv(1) == b''


def test_idle_timeout_is_restored_for_the_next_response(service):
    with closing(http.client.HTTPConnection(*service.server_address, timeout=2)) as conn:
        request(conn, '/static/one.js')
        request(conn, '/view/demo')
        assert service.timeouts == [None, None]


def test_http10_client_keeps_original_connection_behavior(service):
    with closing(http.client.HTTPConnection(*service.server_address, timeout=2)) as conn:
        conn._http_vsn = 10
        conn._http_vsn_str = 'HTTP/1.0'
        response, _ = request(conn, '/view/demo', headers={'Connection':'keep-alive'})
        assert response.version == 10 and response.will_close


@pytest.mark.parametrize('headers', [
    b'Content-Length: 0\r\nContent-Length: 1\r\n',
    b'Content-Length: 0\r\nContent-Length: 0\r\n',
    b'Transfer-Encoding:\r\nTransfer-Encoding: chunked\r\n',
    b'Connection: keep-alive\r\nConnection: close\r\n',
])
def test_duplicate_framing_and_connection_headers_cannot_enable_reuse(service, headers):
    with socket.create_connection(service.server_address, timeout=2) as conn:
        conn.sendall(b'GET /view/demo HTTP/1.1\r\nHost: localhost\r\n' + headers + b'\r\n')
        response = http.client.HTTPResponse(conn)
        response.begin()
        assert response.read() == b'/view/demo'
        assert response.version == 10 and response.will_close


def test_app_reads_reuse_real_handler_and_recheck_access_and_fresh_data(tmp_path, monkeypatch):
    import server

    app = tmp_path / 'demo'
    (app / 'data').mkdir(parents=True)
    (app / 'app.json').write_text(json.dumps({'label': 'Demo', 'routines': []}))
    value_file = app / 'data' / 'items.json'
    value_file.write_text(json.dumps({'value': 1, 'updated': 1}))
    monkeypatch.setattr(server, 'USER_APPS_DIR', tmp_path)
    monkeypatch.setenv('CCC_PROXY_SECRET', 'test-proxy-secret')

    class Handler(server.CommandCenterHandler):
        def log_message(self, *_):
            pass

    class Server(server.ThreadedHTTPServer):
        accepts = 0

        def get_request(self):
            self.accepts += 1
            return super().get_request()

    httpd = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    headers = {'X-CCC-Proxy-Secret': 'test-proxy-secret'}
    try:
        with closing(http.client.HTTPConnection(*httpd.server_address, timeout=3)) as conn:
            for value in (1, 2):
                value_file.write_text(json.dumps({'value': value, 'updated': value}))
                response, body = request(conn, '/api/apps/data?app=demo&key=items', headers=headers)
                assert response.status == 200 and not response.will_close
                assert json.loads(body)['value'] == value
                response, body = request(conn, '/api/apps/routines?app=demo', headers=headers)
                assert response.status == 200 and not response.will_close
                assert json.loads(body)['ok'] is True
            denied, body = request(conn, '/api/apps/data?app=demo&key=items')
            assert denied.status == 401 and json.loads(body)['error'] == 'unauthorized'
            response, body = request(conn, '/api/apps/data?app=demo&key=../outside', headers=headers)
            assert response.status == 400 and json.loads(body)['error'] == 'bad key'
            assert httpd.accepts == 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
