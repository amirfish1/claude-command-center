"""Free-provider registry and key submission: ccc_server/free_providers.py.

Two layers of coverage:

* mocked ``_frp_authed_request`` scripts each router verdict (healthy,
  invalid, transport error) so every submit_key branch is exercised, and
* a real loopback ``FakeRouter`` (stdlib http.server) proves the urllib
  plumbing: login -> POST /api/keys -> POST /api/health/check/<id> -> and
  DELETE cleanup on rejection.

All keys in fixtures are obvious fakes. The provider health probe is the
router's own, so no test here touches a real provider.
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from ccc_server import free_providers as fp

# Clearly fake key material (AGENTS.md: obvious fakes only).
GROQ_KEY = "gsk_" + "x" * 48
GOOGLE_KEY = "AIza" + "y" * 36


class _Base(unittest.TestCase):
    def setUp(self):
        # Token cache, state cache + env are process-global; reset between tests.
        fp._FRP_TOKEN_CACHE.update({"base": None, "token": None, "at": 0.0})
        fp._FRP_STATE_CACHE.update({"at": 0.0, "data": {}})
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for var in ("CCC_FREE_ROUTER_URL", "CCC_FREE_ROUTER_STATE",
                    "CCC_FREE_ROUTER_TOKEN"):
            os.environ.pop(var, None)
        self.addCleanup(self._env.stop)


class TestRegistry(_Base):
    CONTRACT = ("platform", "name", "signup_url", "key_hint", "key_regex",
                "keyless", "free_no_card", "tos_note", "coding_score")

    def test_every_row_carries_the_contract_fields(self):
        self.assertGreaterEqual(len(fp.FREE_PROVIDERS), 8)
        for p in fp.FREE_PROVIDERS:
            for field in self.CONTRACT:
                self.assertIn(field, p, f"{p.get('platform')} missing {field}")

    def test_signup_urls_are_https(self):
        for p in fp.FREE_PROVIDERS:
            self.assertTrue(p["signup_url"].startswith("https://"), p["platform"])

    def test_kilo_is_the_only_keyless_row_and_needs_consent(self):
        keyless = [p for p in fp.FREE_PROVIDERS if p["keyless"]]
        self.assertEqual([p["platform"] for p in keyless], ["kilo"])
        self.assertTrue(keyless[0]["needs_consent"])
        self.assertIn("logs prompts", keyless[0]["tos_note"])

    def test_regexes_accept_real_shapes_reject_garbage(self):
        import re
        good = {
            "google": GOOGLE_KEY,
            "groq": GROQ_KEY,
            "cerebras": "csk-" + "a" * 44,
            "openrouter": "sk-or-v1-" + "b" * 64,
            "nvidia": "nvapi-" + "c" * 32,
            "mistral": "D" * 32,
            "github": "ghp_" + "e" * 36,
            "huggingface": "hf_" + "f" * 34,
        }
        for platform, key in good.items():
            rx = fp._FRP_INDEX[platform]["key_regex"]
            self.assertRegex(key, re.compile(rx), platform)
        for platform in good:
            rx = fp._FRP_INDEX[platform]["key_regex"]
            self.assertNotRegex("not a key!!", re.compile(rx), platform)

    def test_catalog_returns_bare_list_with_state_defaults(self):
        with mock.patch.object(fp, "_frp_key_state", return_value={}):
            rows = fp.free_provider_catalog()
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows), len(fp.FREE_PROVIDERS))
        for row in rows:
            self.assertFalse(row["has_key"])
            self.assertIsNone(row["key_status"])

    def test_catalog_merges_live_key_state(self):
        live = {"groq": {"configured": True, "status": "healthy",
                         "enabled": True, "masked_key": "gsk_...xyz"},
                "nope": {"configured": True, "status": "healthy",
                         "enabled": True, "masked_key": None}}
        with mock.patch.object(fp, "_frp_key_state", return_value=live):
            rows = {r["platform"]: r for r in fp.free_provider_catalog()}
        self.assertTrue(rows["groq"]["has_key"])
        self.assertEqual(rows["groq"]["key_status"], "healthy")
        self.assertEqual(rows["groq"]["masked_key"], "gsk_...xyz")
        self.assertFalse(rows["google"]["has_key"])
        self.assertNotIn("nope", rows)


class TestRouterDiscovery(_Base):
    def test_env_url_wins(self):
        os.environ["CCC_FREE_ROUTER_URL"] = "http://127.0.0.1:4321/"
        self.assertEqual(fp.free_router_base_url(), "http://127.0.0.1:4321")

    def test_state_file_port(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "free-router.json"
            state.write_text(json.dumps({"port": 4567}))
            os.environ["CCC_FREE_ROUTER_STATE"] = str(state)
            self.assertEqual(fp.free_router_base_url(), "http://127.0.0.1:4567")

    def test_state_file_base_url_and_admin_nested(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "free-router.json"
            state.write_text(json.dumps({
                "base_url": "http://127.0.0.1:3017",
                "admin": {"email": "a@b.c", "password": "pw"},
            }))
            os.environ["CCC_FREE_ROUTER_STATE"] = str(state)
            self.assertEqual(fp.free_router_base_url(), "http://127.0.0.1:3017")
            self.assertEqual(fp._frp_admin_credentials(), ("a@b.c", "pw"))

    def test_default_port_when_nothing_configured(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCC_FREE_ROUTER_STATE"] = str(Path(td) / "absent.json")
            self.assertEqual(fp.free_router_base_url(), "http://127.0.0.1:3017")
            self.assertEqual(fp._frp_admin_credentials(), ("", ""))

    def test_unreachable_router_reports_false(self):
        # Port 1 is never listening; reachable() must swallow the refusal.
        os.environ["CCC_FREE_ROUTER_URL"] = "http://127.0.0.1:1"
        self.assertFalse(fp.free_router_reachable())


class TestSubmitKeyBranches(_Base):
    """submit_free_key with _frp_authed_request scripted per path."""

    def _script(self, responses):
        """responses: {(method, path-prefix): (status, payload)} -> recorder."""
        calls = []

        def fake(method, path, body=None, timeout=10, secret=None):
            calls.append({"method": method, "path": path, "body": body})
            for (m, prefix), out in responses.items():
                if m == method and path.startswith(prefix):
                    return out
            return (404, {"error": {"message": "unscripted " + path}})

        patcher = mock.patch.object(fp, "_frp_authed_request", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_unknown_platform(self):
        res = fp.submit_free_key("nope", "x")
        self.assertEqual(res["code"], "unknown_platform")
        self.assertFalse(res["ok"])

    def test_keyed_provider_requires_a_key(self):
        res = fp.submit_free_key("groq", "")
        self.assertEqual(res["code"], "key_required")
        self.assertFalse(res["ok"])

    def test_bad_format_never_reaches_the_router(self):
        calls = self._script({})
        res = fp.submit_free_key("groq", "not a key")
        self.assertEqual(res["code"], "key_format")
        self.assertFalse(res["ok"])
        self.assertEqual(calls, [])

    def test_keyless_needs_consent(self):
        calls = self._script({})
        res = fp.submit_free_key("kilo", None, consent=False)
        self.assertEqual(res["code"], "consent_required")
        self.assertFalse(res["ok"])
        self.assertEqual(calls, [])

    def test_happy_path_validated(self):
        calls = self._script({
            ("POST", "/api/keys"): (201, {"id": 7, "maskedKey": "gsk_...xyz"}),
            ("POST", "/api/health/check/"): (200, {"keyId": 7, "status": "healthy"}),
        })
        res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertTrue(res["ok"])
        self.assertTrue(res["validated"])
        self.assertEqual(res["masked_key"], "gsk_...xyz")
        # Key forwarded exactly once: inside the POST /api/keys body only.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["body"]["key"], GROQ_KEY)
        self.assertNotIn("key", {k: v for k, v in calls[1].items() if k == "body"})

    def test_keyless_enable_probes_and_validates(self):
        calls = self._script({
            ("POST", "/api/keys"): (200, {"id": 3, "maskedKey": "****"}),
            ("POST", "/api/health/check/"): (200, {"keyId": 3, "status": "healthy"}),
        })
        res = fp.submit_free_key("kilo", None, consent=True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["validated"])
        self.assertTrue(res["keyless"])
        # The sentinel POST must not carry a key field at all.
        self.assertNotIn("key", calls[0]["body"])

    def test_provider_rejection_deletes_the_row(self):
        calls = self._script({
            ("POST", "/api/keys"): (201, {"id": 9, "maskedKey": "gsk_...xyz"}),
            ("POST", "/api/health/check/"): (200, {"keyId": 9, "status": "invalid"}),
            ("DELETE", "/api/keys/"): (200, {"success": True}),
        })
        res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertFalse(res["ok"])
        self.assertFalse(res["validated"])
        self.assertEqual(res["code"], "rejected")
        self.assertEqual(calls[-1]["method"], "DELETE")
        self.assertEqual(calls[-1]["path"], "/api/keys/9")

    def test_keyless_rejection_keeps_the_sentinel(self):
        calls = self._script({
            ("POST", "/api/keys"): (200, {"id": 3, "maskedKey": "****"}),
            ("POST", "/api/health/check/"): (200, {"keyId": 3, "status": "invalid"}),
        })
        res = fp.submit_free_key("kilo", None, consent=True)
        self.assertTrue(res["ok"])
        self.assertFalse(res["validated"])
        self.assertEqual(res["code"], "unconfirmed")
        self.assertFalse(any(c["method"] == "DELETE" for c in calls))

    def test_probe_error_is_saved_unconfirmed(self):
        self._script({
            ("POST", "/api/keys"): (201, {"id": 4, "maskedKey": "AIza...y"}),
            ("POST", "/api/health/check/"): (200, {"keyId": 4, "status": "error"}),
        })
        res = fp.submit_free_key("google", GOOGLE_KEY)
        self.assertTrue(res["ok"])
        self.assertFalse(res["validated"])
        self.assertEqual(res["code"], "unconfirmed")

    def test_router_unavailable_is_clean(self):
        with mock.patch.object(fp, "_frp_authed_request",
                               side_effect=fp.FreeRouterUnavailable("refused")):
            res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "router_unavailable")

    def test_router_4xx_surfaces_scrubbed_message(self):
        # A provider/router error that echoes the submitted key must come
        # back scrubbed -- the secret never appears in our response.
        echoed = {"error": {"message": "rejected key " + GROQ_KEY}}
        self._script({("POST", "/api/keys"): (400, echoed)})
        res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "router_rejected")
        self.assertNotIn(GROQ_KEY, json.dumps(res))

    def test_result_never_contains_the_raw_key(self):
        self._script({
            ("POST", "/api/keys"): (201, {"id": 8, "maskedKey": "gsk_...xyz"}),
            ("POST", "/api/health/check/"): (200, {"keyId": 8, "status": "healthy"}),
        })
        res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertNotIn(GROQ_KEY, json.dumps(res))


# ---------------------------------------------------------------------------
# Loopback fake router -- real HTTP through urllib, no mocks.
# ---------------------------------------------------------------------------

class _FakeRouterHandler(BaseHTTPRequestHandler):
    login_hits = []
    posts = []
    deletes = []
    checks = []
    lists = []
    verdict = "healthy"
    authed = True  # when False, every authed call answers 401

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _check_auth(self):
        if not self.authed:
            self._json(401, {"error": {"message": "nope"}})
            return False
        return (self.headers.get("Authorization") or "") == "Bearer test-token" or self._json(401, {})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/auth/login":
            body = self._body()
            type(self).login_hits.append(body)
            if body.get("email") == "ops@local.test" and body.get("password") == "pw":
                return self._json(200, {"token": "test-token", "email": "ops@local.test"})
            return self._json(401, {"error": {"message": "Invalid email or password"}})
        if not self._check_auth():
            return
        if path == "/api/keys":
            body = self._body()
            type(self).posts.append(body)
            return self._json(201, {"id": 42, "platform": body.get("platform"),
                                    "maskedKey": "....tail", "status": "unknown",
                                    "enabled": True})
        if path.startswith("/api/health/check/"):
            type(self).checks.append(path.rsplit("/", 1)[-1])
            return self._json(200, {"keyId": 42, "status": type(self).verdict})
        return self._json(404, {"error": {"message": "not found"}})

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/auth/status":
            return self._json(200, {"needsSetup": False, "authenticated": True})
        if not self._check_auth():
            return
        if path == "/api/keys":
            type(self).lists.append(path)
            return self._json(200, {"keys": [
                {"id": 42, "platform": "groq", "maskedKey": "gsk_...tail",
                 "status": "healthy", "enabled": True},
            ]})
        return self._json(404, {})

    def do_DELETE(self):
        if not self._check_auth():
            return
        path = urllib.parse.urlparse(self.path).path
        type(self).deletes.append(path)
        return self._json(200, {"success": True})

    def log_message(self, *a):  # keep test output quiet
        pass


class TestAgainstFakeRouter(_Base):
    def setUp(self):
        super().setUp()
        for attr in ("login_hits", "posts", "deletes", "checks", "lists"):
            setattr(_FakeRouterHandler, attr, [])
        _FakeRouterHandler.verdict = "healthy"
        _FakeRouterHandler.authed = True
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRouterHandler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        self.addCleanup(self._srv.shutdown)
        self.addCleanup(self._srv.server_close)
        os.environ["CCC_FREE_ROUTER_URL"] = f"http://127.0.0.1:{self._srv.server_port}"

    def test_submit_with_bearer_env_token(self):
        os.environ["CCC_FREE_ROUTER_TOKEN"] = "test-token"
        res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertTrue(res["validated"], res)
        self.assertEqual(_FakeRouterHandler.posts[0]["platform"], "groq")
        self.assertEqual(_FakeRouterHandler.posts[0]["key"], GROQ_KEY)
        self.assertEqual(_FakeRouterHandler.checks, ["42"])

    def test_submit_with_state_file_login(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "free-router.json"
            state.write_text(json.dumps({
                "admin_email": "ops@local.test", "admin_password": "pw"}))
            os.environ["CCC_FREE_ROUTER_STATE"] = str(state)
            res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertTrue(res["validated"], res)
        self.assertEqual(len(_FakeRouterHandler.login_hits), 1)

    def test_catalog_reads_live_rows_over_http(self):
        os.environ["CCC_FREE_ROUTER_TOKEN"] = "test-token"
        rows = {r["platform"]: r for r in fp.free_provider_catalog()}
        self.assertTrue(rows["groq"]["has_key"])
        self.assertEqual(rows["groq"]["key_status"], "healthy")
        self.assertFalse(rows["kilo"]["has_key"])

    def test_rejected_key_deleted_over_http(self):
        os.environ["CCC_FREE_ROUTER_TOKEN"] = "test-token"
        _FakeRouterHandler.verdict = "invalid"
        res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "rejected")
        self.assertEqual(_FakeRouterHandler.deletes, ["/api/keys/42"])

    def test_unauthorized_then_relogin_is_not_needed_with_env_token(self):
        os.environ["CCC_FREE_ROUTER_TOKEN"] = "wrong-token"
        _FakeRouterHandler.authed = True
        res = fp.submit_free_key("groq", GROQ_KEY)
        self.assertFalse(res["ok"])
        self.assertEqual(res["code"], "router_rejected")


if __name__ == "__main__":
    unittest.main()
