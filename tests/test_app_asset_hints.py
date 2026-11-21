"""Preloads cannot overtake document headers or leak across requests."""

import http.server
import io
import json
import socket
import threading
from types import SimpleNamespace

import pytest

from ccc_server.app_asset_hints import AppAssetHintsMixin, send
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


def test_hints_match_current_canonical_bundles_and_validate_all_before_writing(apps):
    from pathlib import Path
    from ccc_server.app_embed import conversation_page
    from ccc_server.app_asset_hints import _ASSETS
    from ccc_server.conversation_assets import ConversationScript
    static = Path(__file__).resolve().parents[1] / 'static'
    html = conversation_page((static / 'index.html').read_text())
    handler = SimpleNamespace(request_version="HTTP/1.1", headers=HEADERS, wfile=io.BytesIO())

    def current_url(name):
        version = ConversationScript(static, name).metadata()[0] if name.startswith('conversation-') else 'current'
        return f'/static/{name}?v={version}'

    assert send(handler, 'example', apps, current_url)
    assert handler.wfile.getvalue() == b""
    final = handler._ccc_final_asset_hints
    for name, kind in _ASSETS:
        link = f'<{current_url(name)}>; rel=preload; as={kind}'
        assert link in final
        assert f'/{name}"' in html
    # A bad last URL must not leave a partial informational response behind.
    handler.wfile = io.BytesIO()
    assert not send(handler, 'example', apps, lambda name:
                    current_url(name) if name != _ASSETS[-1][0] else '/static/other.css?v=current')
    assert not handler.wfile.getvalue()


def test_dashboard_route_keeps_canonical_final_html_after_hints(apps, monkeypatch):
    from tests.test_static_asset_caching import _get, _server
    server = _server()
    monkeypatch.setattr(server, 'USER_APPS_DIR', apps)

    def prepared_page(*_args, asset_hints):
        assert asset_hints
        return {'body': b'<p>canonical page</p>', 'encoding': None,
                'asset_hints': ('</static/conversation-app.js?v=current>; rel=preload; as=script',)}
    monkeypatch.setattr(server._conv_reader, 'forward_page', prepared_page)
    monkeypatch.setattr(server, '_static_asset_url', lambda _: pytest.fail('prepared hints were rebuilt'))
    status, _headers, body = _get(server, '/?ccc_app=example', HEADERS)
    assert status == 200
    assert body == b'<p>canonical page</p>'
    assert _headers["content-length"] == "21"
    assert _headers["link"] == '</static/conversation-app.js?v=current>; rel=preload; as=script'


def test_reader_preparation_and_local_fallback_emit_the_same_links(apps, monkeypatch):
    from tests.test_app_page_reader import wire_page
    from tests.test_static_asset_caching import _get, _server

    server = _server()
    monkeypatch.setattr(server, 'USER_APPS_DIR', apps)
    monkeypatch.setattr(server, '_static_asset_url', asset_url)
    path, query = '/', 'ccc_app=example'
    head, body = wire_page(server, path, query, 'identity', asset_hints=True)
    from ccc_server.app_asset_hints import _ASSETS
    assert head['asset_hints'] == [f'<{asset_url(name)}>; rel=preload; as={kind}'
                                   for name, kind in _ASSETS]
    with monkeypatch.context() as context:
        context.setattr(server._conv_reader, 'forward_page', lambda *_args, **_options: None)
        status, headers, fallback = _get(server, '/?' + query, HEADERS)
    assert status == 200 and fallback == body
    assert headers['link'] == head['asset_hints'][-1]
    ordinary, _ = wire_page(server, path, query, 'identity')
    assert 'asset_hints' not in ordinary


def test_real_connection_sees_hints_before_page_and_reuses_final_framing(apps):
    ready, release = threading.Event(), threading.Event()

    class Handler(AppAssetHintsMixin, ReadKeepAliveMixin, http.server.BaseHTTPRequestHandler):
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
            assert not release.is_set()
            release.set()
            assert stream.readline() == b'HTTP/1.1 200 OK\r\n'
            final = []
            while (line := stream.readline()) != b'\r\n':
                final.append(line)
            assert b'Link: </static/conversation-app.js?v=current>; rel=preload; as=script\r\n' in final
            assert b'Link: </static/conversation-base.css?v=current>; rel=preload; as=style\r\n' in final
            from ccc_server.app_asset_hints import _ASSETS
            assert sum(line.startswith(b'Link:') for line in final) == len(_ASSETS)
            for name, kind in _ASSETS:
                assert f'Link: <{asset_url(name)}>; rel=preload; as={kind}\r\n'.encode() in final
            assert stream.read(5) == b'page!'
            connection.sendall(b'GET /static/next.js HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
            assert stream.readline() == b'HTTP/1.0 200 OK\r\n'
            while (line := stream.readline()) != b'\r\n':
                assert not line.startswith(b'Link:')
            assert stream.read() == b'page!'
            stream.close()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
