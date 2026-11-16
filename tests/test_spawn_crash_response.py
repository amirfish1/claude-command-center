"""Unexpected launch exceptions must return diagnostics, not an empty connection."""
import io
import json
from http.server import BaseHTTPRequestHandler

import pytest
import server


@pytest.mark.parametrize('started', [False, True])
def test_spawn_crash_returns_json_only_before_response_starts(monkeypatch, started):
    def crash(request):
        request.path = '/api/sessions/spawn'
        if started:
            request.send_response(200)
            request.end_headers()
            request.wfile.write(b'already sent')
        raise ImportError('private implementation detail')

    monkeypatch.setattr(BaseHTTPRequestHandler, 'handle_one_request', crash)
    request = object.__new__(server.CommandCenterHandler)
    request.command = 'POST'
    request.request_version = 'HTTP/1.1'
    request.requestline = 'POST /api/sessions/spawn HTTP/1.1'
    request.headers = {}
    request.wfile = io.BytesIO()
    request.close_connection = False
    with pytest.raises(ImportError):
        request.handle_one_request()
    response = request.wfile.getvalue()
    if started:
        assert response.count(b'HTTP/1.0') == 1
        assert response.endswith(b'already sent')
    else:
        assert b'500 Internal Server Error' in response
        payload = json.loads(response.split(b'\r\n\r\n', 1)[1])
        assert payload['code'] == 'spawn_handler_crash'
        assert 'ImportError' in payload['error']
        assert 'restart' in payload['error'].lower()
        assert 'private implementation detail' not in payload['error']
        assert payload['reference']


@pytest.mark.parametrize('route', ['/api/sessions/spawn', '/api/sessions/spawn/',
                                   '/api/sessions/spawn-codex/?source=test'])
def test_spawn_crash_is_readable_over_http(route):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    class CrashingLaunch(server.CommandCenterHandler):
        def do_POST(self):
            raise ImportError('private implementation detail')

    class TestServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # The exception is expected; the HTTP response is the assertion.

    with TestServer(('127.0.0.1', 0), CrashingLaunch) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        try:
            connection.request('POST', route, body='{}')
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == 500
            assert payload['code'] == 'spawn_handler_crash'
            assert 'check sessions before retrying' in payload['error']
        finally:
            connection.close()
            httpd.shutdown()
            thread.join(timeout=5)
