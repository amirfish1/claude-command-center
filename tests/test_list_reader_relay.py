"""CCC-701: fallback list reads holding the reader's token are relayed to it."""
import http.server
import io
import threading
import unittest

from ccc_server import list_reader


class _Reader(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    status = 200
    body = b'{"delta": true}'
    seen = []

    def do_GET(self):
        type(self).seen.append((self.path, dict(self.headers)))
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", '"d-abc"')
        self.send_header("Server-Timing", "app;dur=12")
        self.send_header("Access-Control-Allow-Origin", "http://x")
        if self.status == 304:
            self.end_headers()
            return
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class _Handler:
    """The parts of a dashboard handler relay() uses."""

    def __init__(self, path, headers):
        self.path = path
        self.headers = headers
        self.wfile = io.BytesIO()
        self.status = None
        self.sent = []

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent.append((name, value))

    def end_headers(self):
        pass


class RelayTest(unittest.TestCase):
    def setUp(self):
        _Reader.status, _Reader.body, _Reader.seen = 200, b'{"delta": true}', []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Reader)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def test_foreign_token(self):
        self.assertTrue(list_reader.foreign_token("aaaa.5.ctx.0", "bbbb"))
        self.assertFalse(list_reader.foreign_token("bbbb.5.ctx.0", "bbbb"))
        self.assertFalse(list_reader.foreign_token("", "bbbb"))

    def test_relays_reader_delta_with_its_headers(self):
        path = "/api/conversations/list?window=1d&stale_ok=1&since=aaaa.5.c.0"
        handler = _Handler(path, {"Accept-Encoding": "gzip", "If-None-Match": '"x"',
                                  "Cookie": "secret", "Origin": "http://x"})
        self.assertTrue(list_reader.relay(handler, self.port))
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.wfile.getvalue(), b'{"delta": true}')
        names = {name.lower(): value for name, value in handler.sent}
        self.assertEqual(names["etag"], '"d-abc"')
        self.assertEqual(names["server-timing"], "app;dur=12")
        self.assertEqual(names["content-length"], str(len(b'{"delta": true}')))
        self.assertEqual(names["x-ccc-list-relay"], "reader")
        self.assertNotIn("access-control-allow-origin", names)
        relayed_path, relayed_headers = _Reader.seen[0]
        self.assertEqual(relayed_path, path)
        self.assertEqual(relayed_headers.get("Accept-Encoding"), "gzip")
        self.assertEqual(relayed_headers.get("If-None-Match"), '"x"')
        # Same-origin dashboard response: no browser cookies or Origin go on.
        self.assertNotIn("Cookie", relayed_headers)
        self.assertNotIn("Origin", relayed_headers)

    def test_relays_reply_that_closes_the_connection(self):
        _Reader.protocol_version = "HTTP/1.0"
        try:
            handler = _Handler("/api/conversations/list?since=aaaa.5.c.0", {})
            self.assertTrue(list_reader.relay(handler, self.port))
            self.assertEqual(handler.wfile.getvalue(), b'{"delta": true}')
        finally:
            _Reader.protocol_version = "HTTP/1.1"

    def test_relays_not_modified_without_body(self):
        _Reader.status = 304
        handler = _Handler("/api/conversations/list?since=aaaa.5.c.0", {})
        self.assertTrue(list_reader.relay(handler, self.port))
        self.assertEqual(handler.status, 304)
        self.assertEqual(handler.wfile.getvalue(), b"")
        self.assertNotIn("content-length", {name.lower() for name, _ in handler.sent})

    def test_declined_read_falls_back_without_sending(self):
        _Reader.status = 503
        handler = _Handler("/api/conversations/list?since=aaaa.5.c.0", {})
        self.assertFalse(list_reader.relay(handler, self.port))
        self.assertIsNone(handler.status)
        self.assertEqual(handler.sent, [])

    def test_unreachable_reader_falls_back(self):
        self.server.shutdown()
        self.server.server_close()
        self.server = None
        handler = _Handler("/api/conversations/list?since=aaaa.5.c.0", {})
        self.assertFalse(list_reader.relay(handler, self.port))
        self.assertIsNone(handler.status)

    def test_slow_reader_falls_back_before_headers(self):
        gate = threading.Event()
        original = _Reader.do_GET

        def slow(handler):
            gate.wait(2)
            original(handler)

        _Reader.do_GET = slow
        try:
            handler = _Handler("/api/conversations/list?since=aaaa.5.c.0", {})
            self.assertFalse(list_reader.relay(handler, self.port, headers_s=0.2))
            self.assertIsNone(handler.status)
        finally:
            gate.set()
            _Reader.do_GET = original


if __name__ == "__main__":
    unittest.main()
