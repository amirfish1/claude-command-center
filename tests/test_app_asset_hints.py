"""Hints arrive before HTML and do not consume or reframe its response."""

import http.server
import io
import json
import socket
import threading
from types import SimpleNamespace

import pytest

from ccc_server.app_asset_hints import send
from ccc_server.http_read_keepalive import ReadKeepAliveMixin


HEADERS = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document",
           "X-Forwarded-Proto": "https"}


@pytest.fixture
def apps(tmp_path):
    folder = tmp_path / "example"
    folder.mkdir()
    (folder / "app.json").write_text('{}')
    (folder / "ccc-state.json").write_text(json.dumps({"agent": {"session_id": "test-session"}}))
    return tmp_path


def asset_url(name):
    return f"/static/{name}?v=current"


@pytest.mark.parametrize('change', [
    {"Sec-Fetch-Mode": "cors"}, {"Sec-Fetch-Dest": "iframe"},
    {"X-Forwarded-Proto": "http"}, {"Sec-Fetch-Mode": ""},
    {"Sec-Fetch-Dest": ""}, {"X-Forwarded-Proto": ""},
])
def test_non_navigation_or_direct_http_keeps_original_response(apps, change):
    handler = SimpleNamespace(request_version="HTTP/1.1", headers=HEADERS | change, wfile=io.BytesIO())
    assert not send(handler, "example", apps, asset_url)
    assert handler.wfile.getvalue() == b""


@pytest.mark.parametrize('app_id, session', [
    ("missing", "test-session"), ("../example", "test-session"),
    ("example", None), ("example", ""), ("example", "<invalid>"),
])
def test_only_known_app_with_valid_agent_is_prefetched(apps, app_id, session):
    (apps / 'example/ccc-state.json').write_text(json.dumps({"agent": {"session_id": session}}))
    handler = SimpleNamespace(request_version="HTTP/1.1", headers=HEADERS, wfile=io.BytesIO())
    assert not send(handler, app_id, apps, asset_url)
    assert not handler.wfile.getvalue()


def test_fixed_asset_urls_cannot_inject_headers_and_http10_is_unchanged(apps):
    handler = SimpleNamespace(request_version="HTTP/1.1", headers=HEADERS, wfile=io.BytesIO())
    assert not send(handler, "example", apps, lambda _: '/static/app.css?v=x\r\nInjected: true')
    handler.request_version = "HTTP/1.0"
    assert not send(handler, "example", apps, asset_url)
    assert not handler.wfile.getvalue()


def test_dashboard_route_keeps_canonical_final_html_after_hints(apps, monkeypatch):
    from tests.test_static_asset_caching import _get, _server
    server = _server()
    monkeypatch.setattr(server, 'USER_APPS_DIR', apps)
    monkeypatch.setattr(server, '_static_asset_url', asset_url)
    monkeypatch.setattr(server._conv_reader, 'forward_page',
                        lambda *_args: {'body': b'<p>canonical page</p>', 'encoding': None})
    status, _headers, body = _get(server, '/?ccc_app=example', HEADERS)
    assert status == 103
    assert body.startswith(b'HTTP/1.0 200 OK\r\n')
    assert body.endswith(b'<p>canonical page</p>')
    assert b'Content-Length: 21\r\n' in body


def test_real_connection_sees_hints_before_page_and_reuses_final_framing(apps):
    ready, release = threading.Event(), threading.Event()

    class Handler(ReadKeepAliveMixin, http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == '/?ccc_app=example':
                send(self, "example", apps, asset_url)
                ready.set()
                assert release.wait(3)
            self.send_response(200)
            self.send_header('Content-Length', '5')
            self.end_headers()
            self.wfile.write(b'page!')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=3) as connection:
            stream = connection.makefile('rb')
            request = ('GET /?ccc_app=example HTTP/1.1\r\nHost: localhost\r\n'
                       + ''.join(f'{key}: {value}\r\n' for key, value in HEADERS.items()) + '\r\n')
            connection.sendall(request.encode())
            assert ready.wait(3)
            assert stream.readline() == b'HTTP/1.1 103 Early Hints\r\n'
            assert stream.readline() == b'Link: </static/conversation-app.js?v=current>; rel=preload; as=script\r\n'
            assert stream.readline() == b'Link: </static/app.css?v=current>; rel=preload; as=style\r\n'
            assert stream.readline() == b'\r\n'
            assert not release.is_set()
            release.set()
            assert stream.readline() == b'HTTP/1.1 200 OK\r\n'
            while stream.readline() != b'\r\n':
                pass
            assert stream.read(5) == b'page!'
            connection.sendall(b'GET /static/next.js HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
            assert stream.readline() == b'HTTP/1.0 200 OK\r\n'
            while stream.readline() != b'\r\n':
                pass
            assert stream.read() == b'page!'
            stream.close()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
