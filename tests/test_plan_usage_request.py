"""Request envelope and failure handling for the Claude plan-usage reader.

No real token or network: the opener is replaced, and the redirect test
runs against a loopback server with a fake token.
"""
import importlib
import io
import json
import pathlib
import sys
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

FAKE_TOKEN = "sk-ant-test-XXXX-oauth-token"
ORG_ID = "11111111-2222-4333-8444-555555555555"


class _FakeResponse(io.BytesIO):
    def __init__(self, body, content_type="application/json", url=None, status=200):
        super().__init__(body)
        self.headers = Message()
        if content_type:
            self.headers["Content-Type"] = content_type
        self._url = url or f"https://claude.ai/api/organizations/{ORG_ID}/usage"
        self.status = status

    def geturl(self):
        return self._url


def _http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(
        f"https://claude.ai/api/organizations/{ORG_ID}/usage", code, "err", headers, io.BytesIO(b""),
    )


class PlanUsageRequestTests(unittest.TestCase):
    def setUp(self):
        importlib.import_module("server")
        self.mod = importlib.import_module("ccc_server.recall_usage")
        self.mod._plan_usage_denied_until = 0.0
        self.mod._plan_usage_denied_error = None
        self.patches = [
            mock.patch.object(self.mod, "_get_claude_credentials", return_value={"oauth_token": FAKE_TOKEN}),
            mock.patch.object(self.mod, "_get_claude_org_id", return_value=ORG_ID),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.mod._plan_usage_denied_until = 0.0
        self.mod._plan_usage_denied_error = None

    def _fetch_with(self, side_effect):
        opener = mock.Mock(side_effect=side_effect)
        with mock.patch.object(self.mod, "_plan_usage_open", opener):
            return self.mod._fetch_plan_usage(), opener

    def test_request_envelope_is_truthful_ccc_identity(self):
        res, opener = self._fetch_with(lambda req, timeout: _FakeResponse(b'{"five_hour": {}}'))
        self.assertTrue(res["ok"])
        req = opener.call_args.args[0]
        self.assertEqual(req.full_url, f"https://claude.ai/api/organizations/{ORG_ID}/usage")
        ua = req.get_header("User-agent")
        self.assertTrue(ua.startswith("claude-command-center/"), ua)
        for browser_marker in ("Mozilla", "AppleWebKit", "Chrome", "Safari", "Gecko"):
            self.assertNotIn(browser_marker, ua)
        self.assertEqual(req.get_header("Authorization"), f"Bearer {FAKE_TOKEN}")
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertEqual(opener.call_args.args[1], 5)

    def test_session_key_goes_only_in_cookie(self):
        with mock.patch.object(self.mod, "_get_claude_credentials", return_value={"session_key": "sk-ant-test-XXXX-sess"}):
            _res, opener = self._fetch_with(lambda req, timeout: _FakeResponse(b"{}"))
        req = opener.call_args.args[0]
        self.assertEqual(req.get_header("Cookie"), "sessionKey=sk-ant-test-XXXX-sess")
        self.assertIsNone(req.get_header("Authorization"))

    def test_invalid_org_id_never_builds_a_request(self):
        with mock.patch.object(self.mod, "_get_claude_org_id", return_value="../evil.example/x"):
            res, opener = self._fetch_with(lambda req, timeout: _FakeResponse(b"{}"))
        self.assertFalse(res["ok"])
        opener.assert_not_called()

    def test_403_is_unavailable_and_backs_off_without_retrying(self):
        res, opener = self._fetch_with(_http_error(403))
        self.assertEqual((res["ok"], res["unavailable"], res["reason"], res["status"]),
                         (False, True, "denied", 403))
        # A second read inside the hold window makes no request at all.
        res2, opener2 = self._fetch_with(lambda req, timeout: _FakeResponse(b"{}"))
        self.assertEqual(res2["reason"], "denied")
        self.assertIn("backoff_until", res2)
        opener2.assert_not_called()

    def test_429_honours_retry_after_bounded(self):
        with mock.patch.object(self.mod.time, "time", return_value=1000.0):
            res, _ = self._fetch_with(_http_error(429, retry_after=7200))
        self.assertEqual(res["reason"], "rate_limited")
        self.assertEqual(self.mod._plan_usage_denied_until, 1000.0 + 7200)
        with mock.patch.object(self.mod.time, "time", return_value=1000.0):
            self.mod._plan_usage_denied_error = None
            self._fetch_with(_http_error(429, retry_after=10 ** 9))
        self.assertEqual(self.mod._plan_usage_denied_until,
                         1000.0 + self.mod._PLAN_USAGE_MAX_RETRY_AFTER_SECS)

    def test_401_retries_on_normal_poll_cadence(self):
        with mock.patch.object(self.mod.time, "time", return_value=1000.0):
            res, _ = self._fetch_with(_http_error(401))
        self.assertEqual(res["reason"], "unauthorized")
        self.assertEqual(self.mod._plan_usage_denied_until, 1000.0 + self.mod._PLAN_USAGE_POLL_SECS)

    def test_html_challenge_page_is_unavailable_not_parsed(self):
        res, _ = self._fetch_with(
            lambda req, timeout: _FakeResponse(b"<html>Just a moment...</html>", content_type="text/html"))
        self.assertEqual((res["ok"], res["reason"]), (False, "challenge"))

    def test_response_from_other_host_is_refused(self):
        res, _ = self._fetch_with(
            lambda req, timeout: _FakeResponse(b"{}", url="https://evil.example/usage"))
        self.assertEqual((res["ok"], res["reason"]), (False, "redirected"))

    def test_transport_error_redacts_token(self):
        res, _ = self._fetch_with(OSError(f"boom Authorization: Bearer {FAKE_TOKEN}"))
        self.assertFalse(res["ok"])
        self.assertNotIn(FAKE_TOKEN, res["error"])
        self.assertIn("[redacted]", res["error"])

    def test_failed_poll_does_not_overwrite_cached_reading(self):
        self.mod._plan_usage_cache = {"ok": True, "usage": {"five_hour": {"utilization": 12}}}
        self.mod._plan_usage_cache_time = 123.0
        with mock.patch.object(self.mod, "_plan_usage_open", side_effect=_http_error(403)), \
                mock.patch.object(self.mod._core, "_read_codex_usage", return_value=None), \
                mock.patch.object(self.mod._core, "_read_kimi_usage", return_value=None):
            self.assertFalse(self.mod._poll_plan_usage_once())
        self.assertEqual(self.mod._plan_usage_cache["usage"]["five_hour"]["utilization"], 12)
        self.assertEqual(self.mod._plan_usage_cache_time, 123.0)


class _RedirectHandler(BaseHTTPRequestHandler):
    hits = []

    def do_GET(self):
        type(self).hits.append((self.path, self.headers.get("Authorization")))
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/target")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


class PlanUsageRedirectTests(unittest.TestCase):
    def test_opener_never_follows_redirects_with_credentials(self):
        mod = importlib.import_module("ccc_server.recall_usage")
        _RedirectHandler.hits = []
        srv = HTTPServer(("127.0.0.1", 0), _RedirectHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/start")
            req.add_header("Authorization", f"Bearer {FAKE_TOKEN}")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                mod._plan_usage_open(req, 5)
            ctx.exception.close()
            self.assertEqual(ctx.exception.code, 302)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual([p for p, _ in _RedirectHandler.hits], ["/start"])


if __name__ == "__main__":
    unittest.main()
