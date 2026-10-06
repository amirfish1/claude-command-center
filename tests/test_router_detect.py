"""Existing-router detection (L20): ccc_server/router_detect.py.

Detection probes run against throwaway loopback HTTP stubs on ephemeral
ports — the real product ports are overridden per test, so nothing here
touches a user's actual routers. The preferred-router file is redirected to
a tempdir; BYOK probing is patched so no Keychain/state dir is touched.
"""

import json
import http.server
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import server  # noqa: F401 -- registers "server" for ccc_server.core lookups
from ccc_server import router_detect


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Routes keyed by exact path; values are (status, body_text)."""
    routes = {}

    def do_GET(self):
        body = self.routes.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"{}")
            return
        status, text = body
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(text.encode())

    def log_message(self, *args):
        pass


class _StubServer:
    def __init__(self, routes):
        handler = type("H", (_StubHandler,), {"routes": routes})
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RouterDetectBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.choice_file = Path(self._tmp.name) / "free-router-choice.json"
        # Fake empty home so the installed-not-running checks never see the
        # real ~/.ollama / ~/.config/free-claude-code.
        self.fake_home = Path(self._tmp.name) / "home"
        self.fake_home.mkdir()
        # Point every probe port at a closed ephemeral port by default —
        # subclasses open stubs and override specific constants.
        self._closed = 1  # port 1 is never open
        self._patches = [
            mock.patch.object(router_detect, "_choice_path", return_value=self.choice_file),
            mock.patch.object(Path, "home", return_value=self.fake_home),
            mock.patch.object(router_detect, "FREELLMAPI_USER_PORT", self._closed),
            mock.patch.object(router_detect, "FREELLMAPI_CCC_PORT", self._closed),
            mock.patch.object(router_detect, "NINEROUTER_PORT", self._closed),
            mock.patch.object(router_detect, "FREECLAUDECODE_PORT", self._closed),
            mock.patch.object(router_detect, "OLLAMA_PORT", self._closed),
            mock.patch.object(router_detect, "LMSTUDIO_PORT", self._closed),
            mock.patch.object(router_detect, "_detect_byok_openrouter", lambda: []),
            mock.patch.object(router_detect, "_detect_env_routes", lambda: []),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        # Fresh cache per test.
        router_detect._scan_cache["ts"] = 0.0
        router_detect._scan_cache["data"] = None
        self.addCleanup(lambda: router_detect._scan_cache.update({"ts": 0.0, "data": None}))


class TestEmptyScan(RouterDetectBase):
    def test_nothing_detected(self):
        data = router_detect.detect_routers(fresh=True)
        self.assertTrue(data["ok"])
        self.assertEqual(data["routers"], [])
        self.assertIsNone(data["preferred"])
        self.assertIn("scan_ms", data)

    def test_cache_second_call_is_cached(self):
        first = router_detect.detect_routers(fresh=True)
        second = router_detect.detect_routers()
        self.assertTrue(second.get("cached"))
        self.assertEqual(first["routers"], second["routers"])


class TestFreellmapi(RouterDetectBase):
    def test_user_freellmapi_detected(self):
        stub = _StubServer({
            "/v1/openapi.json": (200, json.dumps({
                "info": {"title": "FreeLLMAPI", "version": "0.4.1"},
            })),
            "/api/ping": (200, '{"status":"ok"}'),
        })
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "FREELLMAPI_USER_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        ids = {r["id"] for r in data["routers"]}
        self.assertIn("freellmapi", ids)
        rec = next(r for r in data["routers"] if r["id"] == "freellmapi")
        self.assertEqual(rec["port"], stub.port)
        self.assertEqual(rec["version"], "0.4.1")
        self.assertTrue(rec["openai_base_url"].endswith("/v1"))
        self.assertFalse(rec["managed_by_ccc"])

    def test_ccc_managed_freellmapi_flagged(self):
        stub = _StubServer({
            "/v1/openapi.json": (200, json.dumps({"info": {"title": "FreeLLMAPI"}})),
        })
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "FREELLMAPI_CCC_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        rec = next(r for r in data["routers"] if r["id"] == "freellmapi-ccc")
        self.assertTrue(rec["managed_by_ccc"])

    def test_ping_fallback_without_openapi(self):
        stub = _StubServer({"/api/ping": (200, '{"status":"ok"}')})
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "FREELLMAPI_USER_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        self.assertIn("freellmapi", {r["id"] for r in data["routers"]})

    def test_unrelated_service_on_3001_not_claimed(self):
        # Something else entirely on the freellmapi port must not be reported.
        stub = _StubServer({"/": (200, "<html>some dev server</html>")})
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "FREELLMAPI_USER_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        self.assertNotIn("freellmapi", {r["id"] for r in data["routers"]})


class TestPort20128(RouterDetectBase):
    def test_omniroute_fingerprint(self):
        stub = _StubServer({
            "/": (200, "<html><title>OmniRoute Dashboard</title></html>"),
            "/v1/models": (200, '{"object":"list","data":[{"id":"auto"},{"id":"oc/kimi"}]}'),
        })
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "NINEROUTER_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        rec = next((r for r in data["routers"] if r["id"] == "omniroute"), None)
        self.assertIsNotNone(rec)
        self.assertTrue(rec["keyless"])
        self.assertEqual(rec["models_count"], 2)

    def test_9router_fingerprint(self):
        stub = _StubServer({
            "/": (200, "<html><body>9Router — never stop coding</body></html>"),
            "/v1/models": (401, '{"error":"unauthorized"}'),
        })
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "NINEROUTER_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        rec = next((r for r in data["routers"] if r["id"] == "ninerouter"), None)
        self.assertIsNotNone(rec)
        self.assertFalse(rec["keyless"])  # 401 on /v1/models => needs a key

    def test_unknown_service_not_reported(self):
        stub = _StubServer({"/": (200, "<html><title>Grafana</title></html>")})
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "NINEROUTER_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        self.assertEqual(data["routers"], [])


class TestOllama(RouterDetectBase):
    def test_running_ollama_with_models(self):
        stub = _StubServer({
            "/api/version": (200, '{"version":"0.6.2"}'),
            "/api/tags": (200, '{"models":[{"name":"qwen3:8b"},{"name":"deepseek-r1:7b"}]}'),
        })
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "OLLAMA_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        rec = next((r for r in data["routers"] if r["id"] == "ollama"), None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["version"], "0.6.2")
        self.assertEqual(rec["models_count"], 2)
        self.assertTrue(rec["keyless"])

    def test_installed_not_running(self):
        fake_home = Path(self._tmp.name) / "home"
        (fake_home / ".ollama").mkdir(parents=True)
        with mock.patch.object(Path, "home", return_value=fake_home):
            data = router_detect.detect_routers(fresh=True)
        rec = next((r for r in data["routers"] if r["id"] == "ollama"), None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "installed")


class TestFreeClaudeCode(RouterDetectBase):
    def test_running_proxy_detected(self):
        stub = _StubServer({
            "/openapi.json": (200, json.dumps({"info": {"title": "free-claude-code proxy"}})),
        })
        self.addCleanup(stub.close)
        with mock.patch.object(router_detect, "FREECLAUDECODE_PORT", stub.port):
            data = router_detect.detect_routers(fresh=True)
        rec = next((r for r in data["routers"] if r["id"] == "freeclaudecode"), None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "running")
        self.assertEqual(rec["anthropic_base_url"], f"http://127.0.0.1:{stub.port}")

    def test_installed_not_running_via_config_dir(self):
        fake_home = Path(self._tmp.name) / "home"
        cfg = fake_home / ".config" / "free-claude-code"
        cfg.mkdir(parents=True)
        (cfg / ".env").write_text("PORT=8082\n")
        with mock.patch.object(Path, "home", return_value=fake_home):
            data = router_detect.detect_routers(fresh=True)
        rec = next((r for r in data["routers"] if r["id"] == "freeclaudecode"), None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["status"], "installed")


class TestByokOpenRouter(unittest.TestCase):
    """BYOK detection patches _detect_byok_openrouter out in the base class —
    exercise the real function here with the byok module patched."""

    def test_openrouter_profile_detected(self):
        with mock.patch.object(
            router_detect._core, "byok_list_profiles",
            return_value=[{"name": "work", "providers": ["openrouter"]}],
            create=True,
        ):
            rows = router_detect._detect_byok_openrouter()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "openrouter-key")
        self.assertEqual(rows[0]["kind"], "key")

    def test_no_key_no_detection(self):
        with mock.patch.object(
            router_detect._core, "byok_list_profiles", return_value=[], create=True,
        ), mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("OPENROUTER_API_KEY", None)
            rows = router_detect._detect_byok_openrouter()
        self.assertEqual(rows, [])


class TestPreference(RouterDetectBase):
    def test_set_and_read_preferred(self):
        self.assertIsNone(router_detect.preferred_router_id())
        router_detect.set_preferred_router("ollama")
        self.assertEqual(router_detect.preferred_router_id(), "ollama")

    def test_key_stored_0600_and_never_returned(self):
        router_detect.set_preferred_router("ninerouter", key="jr-secret-1")
        data = json.loads(self.choice_file.read_text())
        self.assertEqual(data["key"], "jr-secret-1")
        mode = self.choice_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        payload = router_detect.detect_routers(fresh=True)
        self.assertNotIn("jr-secret-1", json.dumps(payload))  # secret never leaks

    def test_clear_preference(self):
        router_detect.set_preferred_router("ollama")
        router_detect.set_preferred_router(None)
        self.assertIsNone(router_detect.preferred_router_id())
        self.assertFalse(self.choice_file.exists())

    def test_stale_preference_dropped_when_router_gone(self):
        router_detect.set_preferred_router("ghost")
        data = router_detect.detect_routers(fresh=True)
        self.assertIsNone(data["preferred"])


class TestScanPerf(RouterDetectBase):
    def test_scan_is_parallel_and_bounded(self):
        """6+ probes must not serialize: total scan < 4x one probe timeout."""
        import time
        t0 = time.monotonic()
        router_detect.detect_routers(fresh=True)
        elapsed = time.monotonic() - t0
        # Each probe has a 0.8s timeout; serial worst case would be >4s.
        self.assertLess(elapsed, 3.0)


if __name__ == "__main__":
    unittest.main()
