"""Unit tests for ccc_server/free_settings.py (Settings > Free models).

A stub freellmapi router runs on a loopback port and records every call, so
the tests assert the proxy contract (login, Bearer reuse, 401 retry, path +
body forwarding, secret stripping) without a real router or node.
"""
import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ccc_server import free_settings as fs


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeHandler:
    """Stands in for CommandCenterHandler: captures send_json calls."""

    def __init__(self, path, body=None):
        self.path = path
        self.headers = {}
        raw = b""
        if body is not None:
            raw = json.dumps(body).encode()
            self.headers["Content-Length"] = str(len(raw))
        self.rfile = io.BytesIO(raw)
        self.sent = []

    def send_json(self, data, status=200):
        self.sent.append((status, data))


class RouterState:
    def __init__(self):
        self.logins = 0
        self.calls = []
        self.fail_auth_once = False
        self.keys = []
        self.map_ = {"default": "auto", "opus": "auto", "sonnet": "auto", "haiku": "auto"}


STATE = RouterState()


class FakeRouter(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _json(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authed(self):
        auth = self.headers.get("Authorization") or ""
        if auth not in ("Bearer tok-1", "Bearer admin-tok"):
            self._json(401, {"error": {"message": "unauthorized"}})
            return False
        if STATE.fail_auth_once:
            STATE.fail_auth_once = False
            self._json(401, {"error": {"message": "expired"}})
            return False
        return True

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/")
        body = self._body()
        if path == "/api/auth/login":
            STATE.logins += 1
            if body.get("email") == "a@b.c" and body.get("password") == "pw":
                self._json(200, {"token": "tok-1", "email": "a@b.c"})
            else:
                self._json(401, {"error": {"message": "bad login"}})
            return
        if not self._authed():
            return
        STATE.calls.append(("POST", path, body))
        if path == "/api/keys":
            self._json(201, {"id": 9, "platform": body.get("platform"),
                             "maskedKey": "sk-...-1234", "enabled": True})
            return
        self._json(404, {"error": {"message": "nope"}})

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if not self._authed():
            return
        STATE.calls.append(("GET", path, None))
        if path == "/api/keys":
            self._json(200, STATE.keys)
        elif path == "/api/keys/providers":
            self._json(200, {"providers": [{"platform": "kilo", "name": "Kilo", "keyless": True}],
                             "summary": {"total": 1, "configured": 0, "unconfigured": 1}})
        elif path == "/api/analytics/summary":
            self._json(200, {"totalRequests": 12, "successRate": 91.7,
                             "totalInputTokens": 5000, "totalOutputTokens": 9000,
                             "estimatedCostSavings": 1.23})
        elif path == "/api/analytics/by-platform":
            self._json(200, [{"platform": "kilo", "endpoint": "kilo", "requests": 12,
                              "successRate": 91.7, "avgLatencyMs": 800,
                              "totalInputTokens": 5000, "totalOutputTokens": 9000}])
        elif path == "/api/settings/anthropic-map":
            self._json(200, {"map": STATE.map_})
        elif path == "/api/leaky":
            self._json(200, {"maskedKey": "sk-...-1", "encrypted_key": "CIPHERTEXT",
                             "password": "hunter2", "token": "tok-1",
                             "nested": {"iv": "abc", "auth_tag": "def", "ok": 1}})
        else:
            self._json(404, {"error": {"message": "nope"}})

    def do_PATCH(self):
        path = self.path.split("?")[0].rstrip("/")
        if not self._authed():
            return
        body = self._body()
        STATE.calls.append(("PATCH", path, body))
        if path.startswith("/api/keys/platform/"):
            self._json(200, {"success": True, "enabled": body.get("enabled"), "updatedKeys": 1})
        elif path.startswith("/api/keys/"):
            self._json(200, {"success": True, "enabled": body.get("enabled")})
        else:
            self._json(404, {"error": {"message": "nope"}})

    def do_PUT(self):
        path = self.path.split("?")[0].rstrip("/")
        if not self._authed():
            return
        body = self._body()
        STATE.calls.append(("PUT", path, body))
        if path == "/api/settings/anthropic-map":
            STATE.map_.update(body)
            self._json(200, {"map": STATE.map_})
        else:
            self._json(404, {"error": {"message": "nope"}})

    def do_DELETE(self):
        path = self.path.split("?")[0].rstrip("/")
        if not self._authed():
            return
        STATE.calls.append(("DELETE", path, None))
        if path.startswith("/api/keys/"):
            self._json(200, {"success": True})
        else:
            self._json(404, {"error": {"message": "nope"}})


@pytest.fixture()
def router(tmp_path, monkeypatch):
    """Run the stub router; point the module at it via a temp state file."""
    STATE.__init__()
    fs._reset_cache()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeRouter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    state = tmp_path / "free-router.json"
    state.write_text(json.dumps({"port": port, "admin_email": "a@b.c",
                                 "admin_password": "pw"}))
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(state))
    monkeypatch.delenv("CCC_FREE_ROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("CCC_FREE_ROUTER_EMAIL", raising=False)
    monkeypatch.delenv("CCC_FREE_ROUTER_PASSWORD", raising=False)
    yield srv, port
    srv.shutdown()
    fs._reset_cache()


@pytest.fixture()
def no_router(tmp_path, monkeypatch):
    fs._reset_cache()
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(tmp_path / "missing.json"))
    monkeypatch.delenv("CCC_FREE_ROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("CCC_FREE_ROUTER_EMAIL", raising=False)
    monkeypatch.delenv("CCC_FREE_ROUTER_PASSWORD", raising=False)


def _get(path):
    h = FakeHandler(path)
    fs.handle(h, "GET")
    return h.sent[-1]


def _post(path, body):
    h = FakeHandler(path, body)
    fs.handle(h, "POST")
    return h.sent[-1]


# ---------------------------------------------------------------------------
# Config / ping
# ---------------------------------------------------------------------------

def test_ping_unconfigured(no_router):
    status, data = _get("/api/free-settings/ping")
    assert status == 200 and data["ok"] is True and data["configured"] is False


def test_ping_configured(router):
    status, data = _get("/api/free-settings/ping")
    assert status == 200 and data["configured"] is True
    assert data["base_url"].startswith("http://127.0.0.1:")


def test_router_conf_tolerates_field_spellings(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"port": 3017,
                             "admin": {"email": "x@y.z", "password": "q"}}))
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(p))
    conf = fs._router_conf()
    assert conf["email"] == "x@y.z" and conf["password"] == "q"
    assert conf["base_url"] == "http://127.0.0.1:3017"


def test_admin_token_skips_login(tmp_path, monkeypatch):
    """A stored admin_token authenticates directly — zero login calls."""
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"port": 3017, "admin_token": "admin-tok",
                             "admin_email": "a@b.c", "admin_password": "pw"}))
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(p))
    conf = fs._router_conf()
    assert conf["admin_token"] == "admin-tok"


def test_admin_token_used_without_login(router, tmp_path, monkeypatch):
    state = tmp_path / "free-router.json"
    port = router[1]
    state.write_text(json.dumps({"port": port, "admin_token": "admin-tok"}))
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(state))
    fs._reset_cache()
    status, data = _get("/api/free-settings/keys")
    assert status == 200 and data["ok"] is True
    assert STATE.logins == 0  # Bearer came from the file, not /api/auth/login


def test_admin_token_401_falls_back_to_login(router, tmp_path, monkeypatch):
    """Stale admin_token -> one 401, then email/password login, then retry."""
    state = tmp_path / "free-router.json"
    port = router[1]
    state.write_text(json.dumps({"port": port, "admin_token": "stale-tok",
                                 "admin_email": "a@b.c", "admin_password": "pw"}))
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(state))
    fs._reset_cache()
    status, data = _get("/api/free-settings/keys")
    assert status == 200 and data["ok"] is True
    assert STATE.logins == 1


def test_unknown_route_404(no_router):
    status, _ = _get("/api/free-settings/nope")
    assert status == 404
    status, _ = _post("/api/free-settings/nope", {})
    assert status == 404


# ---------------------------------------------------------------------------
# Proxy behaviour
# ---------------------------------------------------------------------------

def test_keys_list_proxied_and_logged_in(router):
    STATE.keys = [{"id": 1, "platform": "kilo", "maskedKey": "sk-...-1",
                   "enabled": True, "status": "valid"}]
    status, data = _get("/api/free-settings/keys")
    assert status == 200 and data["ok"] is True
    assert data["keys"][0]["maskedKey"] == "sk-...-1"
    assert STATE.logins == 1
    assert ("GET", "/api/keys", None) in STATE.calls


def test_token_cached_across_calls(router):
    _get("/api/free-settings/keys")
    _get("/api/free-settings/providers")
    assert STATE.logins == 1


def test_401_triggers_single_relogin_and_retry(router):
    STATE.fail_auth_once = True
    status, data = _get("/api/free-settings/keys")
    assert status == 200 and data["ok"] is True
    assert STATE.logins == 2  # initial login + one re-login after 401


def test_key_add_never_echoes_secret(router):
    status, data = _post("/api/free-settings/keys/add",
                         {"platform": "groq", "key": "gsk-supersecret-value"})
    assert status == 200 and data["ok"] is True
    # upstream received the real key…
    assert ("POST", "/api/keys", {"platform": "groq",
                                  "key": "gsk-supersecret-value"}) in STATE.calls
    # …but the client never sees it back
    assert "gsk-supersecret-value" not in json.dumps(data)
    assert data["key"]["maskedKey"] == "sk-...-1234"


def test_key_enable_disable(router):
    status, data = _post("/api/free-settings/keys/enable", {"id": 4, "enabled": False})
    assert status == 200 and data["ok"] is True
    assert ("PATCH", "/api/keys/4", {"enabled": False}) in STATE.calls


def test_key_remove(router):
    status, data = _post("/api/free-settings/keys/remove", {"id": 4})
    assert status == 200 and data["ok"] is True
    assert ("DELETE", "/api/keys/4", None) in STATE.calls


def test_platform_enable(router):
    status, data = _post("/api/free-settings/platforms/enable",
                         {"platform": "kilo", "enabled": True})
    assert status == 200 and data["ok"] is True
    assert ("PATCH", "/api/keys/platform/kilo", {"enabled": True}) in STATE.calls


def test_usage_merges_summary_and_platform(router):
    status, data = _get("/api/free-settings/usage?range=30d")
    assert status == 200 and data["ok"] is True
    assert data["range"] == "30d"
    assert data["summary"]["estimatedCostSavings"] == 1.23
    assert data["by_platform"][0]["platform"] == "kilo"


def test_usage_bad_range_falls_back(router):
    status, data = _get("/api/free-settings/usage?range=since-forever")
    assert status == 200 and data["range"] == "7d"


def test_strategy_get_and_post(router):
    status, data = _get("/api/free-settings/strategy")
    assert status == 200 and data["strategy"]["map"]["default"] == "auto"
    status, data = _post("/api/free-settings/strategy",
                         {"family": "default", "model": "kilo/auto"})
    assert status == 200
    assert ("PUT", "/api/settings/anthropic-map",
            {"default": "kilo/auto"}) in STATE.calls


# ---------------------------------------------------------------------------
# Validation + failure modes
# ---------------------------------------------------------------------------

def test_platform_slug_injection_rejected(router):
    status, data = _post("/api/free-settings/platforms/enable",
                         {"platform": "kilo/../../admin", "enabled": True})
    assert status == 400 and data["code"] == "bad_platform"


def test_strategy_family_and_model_validated(router):
    status, data = _post("/api/free-settings/strategy",
                         {"family": "nope", "model": "x"})
    assert status == 400 and data["code"] == "bad_family"
    status, data = _post("/api/free-settings/strategy",
                         {"family": "default", "model": 'bad "inject'})
    assert status == 400 and data["code"] == "bad_model"


def test_key_enable_requires_bool(router):
    status, data = _post("/api/free-settings/keys/enable", {"id": 4, "enabled": "yes"})
    assert status == 400 and data["code"] == "bad_enabled"


def test_unconfigured_returns_friendly_error(no_router):
    status, data = _get("/api/free-settings/keys")
    assert status == 503 and data["code"] == "router_not_configured"


def test_unreachable_router(tmp_path, monkeypatch):
    """State exists but nothing listens -> router_unreachable, not a crash."""
    fs._reset_cache()
    state = tmp_path / "free-router.json"
    state.write_text(json.dumps({"port": 9, "admin_email": "a@b.c",
                                 "admin_password": "pw"}))
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(state))
    monkeypatch.delenv("CCC_FREE_ROUTER_BASE_URL", raising=False)
    status, data = _get("/api/free-settings/keys")
    assert status == 503 and data["code"] == "router_unreachable"


def test_upstream_error_passthrough(router):
    # unknown upstream route -> the admin helper returns the 404 payload
    status, payload = fs._admin("GET", "/api/keys/providers-extra")
    assert status == 404
    assert isinstance(payload, dict)


def test_401_response_never_leaks_token(router):
    # When the router is unreachable-after-auth the error text must not
    # contain credentials or the session token.
    status, data = _post("/api/free-settings/keys/remove", {"id": 0})
    blob = json.dumps(data)
    assert "tok-1" not in blob and "pw" not in blob


def test_sanitize_strips_secret_fields():
    dirty = {"maskedKey": "sk-...-1", "encrypted_key": "CIPH", "iv": "i",
             "auth_tag": "a", "token": "t", "password": "p",
             "list": [{"proxy_url": "http://x:y@h", "ok": 1}]}
    clean = fs._sanitize(dirty)
    assert clean == {"maskedKey": "sk-...-1", "list": [{"ok": 1}]}


def test_login_failure_maps_to_auth_error(tmp_path, monkeypatch):
    fs._reset_cache()
    state = tmp_path / "free-router.json"
    state.write_text(json.dumps({"port": 9, "admin_email": "x", "admin_password": "y"}))
    monkeypatch.setenv("CCC_FREE_ROUTER_STATE", str(state))
    monkeypatch.delenv("CCC_FREE_ROUTER_BASE_URL", raising=False)
    # Port 9 refuses the connection before auth is even attempted.
    status, data = _get("/api/free-settings/keys")
    assert status == 503 and data["code"] in ("router_unreachable", "router_auth_failed")
