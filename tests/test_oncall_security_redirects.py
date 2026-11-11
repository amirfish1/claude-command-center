"""Real loopback HTTP attack: credentials must not follow redirects."""
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ccc_server.oncall import channels, sources
import urllib.request
import pytest

@contextmanager
def endpoint(handler):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_channel_redirect_does_not_leak_authorization():
    seen = []
    class Sink(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            seen.append(self.headers.get('Authorization'))
            self.send_response(200); self.end_headers()
    with endpoint(Sink) as destination:
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', destination)
                self.end_headers()
        with endpoint(Redirect) as origin:
            channels.http_request('GET', origin, headers={'Authorization': 'Bearer test-secret'})
    assert not seen


def test_source_downgrade_drops_authorization():
    req = urllib.request.Request('https://example.test/a', headers={'Authorization': 'Bearer test-secret'})
    redirected = sources._SafeRedirect().redirect_request(req, None, 302, 'Found', {}, 'http://example.test/b')
    assert not redirected.has_header('Authorization')
