# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Settings > Free models panel backend.

A thin loopback proxy between the dashboard and the CCC-managed freellmapi
router. The router's admin API (/api/keys, /api/analytics, /api/settings)
is guarded by a session token, so this module logs in with the admin
credentials the installer wrote to ``~/.ccc/free-router.json`` and forwards
exactly the management calls the panel needs:

    GET  /api/free-settings/ping               -> {"ok","configured","base_url"}
    GET  /api/free-settings/providers          -> router's provider catalog
    GET  /api/free-settings/keys               -> masked key list
    POST /api/free-settings/keys/add           {platform, key?, label?}
    POST /api/free-settings/keys/enable        {id, enabled}
    POST /api/free-settings/keys/remove        {id}
    POST /api/free-settings/platforms/enable   {platform, enabled}
    GET  /api/free-settings/usage?range=       -> {summary, by_platform}
    GET  /api/free-settings/strategy           -> {map} (anthropic model map)
    POST /api/free-settings/strategy           {family, model}

Read-only sibling contracts (/api/free-router/status, .../providers,
.../models) are fetched by the browser directly; a 404 there means "that
lane is not merged yet" and the panel degrades gracefully. Everything under
/api/free-settings/* is owned here so merges stay conflict-free.

Security: responses never echo key material, admin credentials, or the
session token back to the caller. Anything the router returns is filtered
through _sanitize() before it leaves this process. Router state lives in
~/.ccc/free-router.json (0600, written by the installer); tests point
CCC_FREE_ROUTER_STATE / CCC_FREE_ROUTER_BASE_URL at fakes instead.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Fields that may carry secret material and are always stripped from any
# payload we relay to the browser. The router only ever returns masked keys,
# but filtering costs nothing and keeps that guarantee local.
_SECRET_FIELDS = {
    "key", "api_key", "apikey", "token", "password", "secret",
    "encrypted_key", "iv", "auth_tag", "proxy_encrypted", "proxy_iv",
    "proxy_auth_tag", "proxy_url", "proxyUrl",
}

_TIMEOUT_S = 10
_MAX_BODY = 64 * 1024
_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_FAMILIES = ("default", "opus", "sonnet", "haiku")
_RANGES = ("24h", "7d", "30d", "90d")

# Session-token cache: {base_url: (token, monotonic_deadline)}. Tokens are
# re-used until a 401 says otherwise; the deadline just bounds staleness.
_token_lock = threading.Lock()
_token_cache: dict[str, tuple[str, float]] = {}
_TOKEN_TTL_S = 600.0
# base_urls whose stored admin_token already 401'd — don't keep retrying it.
_bad_admin_tokens: set[str] = set()


class FreeSettingsError(Exception):
    """User-facing failure; `code` is a stable string for the UI."""

    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Router state + admin session
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    override = os.environ.get("CCC_FREE_ROUTER_STATE")
    if override:
        return Path(override)
    return Path.home() / ".ccc" / "free-router.json"


def _pick(d: dict, *names):
    """First non-empty string among possibly-nested key spellings."""
    for name in names:
        cur = d
        ok = True
        for part in name.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and isinstance(cur, str) and cur.strip():
            return cur.strip()
    return None


def _router_conf() -> dict | None:
    """{base_url, admin_token, email, password} or None when not set up.

    Tolerant about field names: the installer owns the file shape and both
    sides evolve, so we accept a few plausible spellings rather than
    hard-fail on a rename. `admin_token` (a persistent session token the
    installer may store) is preferred; email+password is the login fallback.
    """
    base = (os.environ.get("CCC_FREE_ROUTER_BASE_URL") or "").strip()
    email = (os.environ.get("CCC_FREE_ROUTER_EMAIL") or "").strip()
    password = os.environ.get("CCC_FREE_ROUTER_PASSWORD") or ""
    admin_token = (os.environ.get("CCC_FREE_ROUTER_ADMIN_TOKEN") or "").strip()

    if not base or not (admin_token or (email and password)):
        try:
            raw = _state_path().read_text(encoding="utf-8")
            state = json.loads(raw)
        except (OSError, ValueError):
            state = None
        if not isinstance(state, dict):
            if not (base and (admin_token or (email and password))):
                return None
            state = {}
        if not base:
            base = str(state.get("base_url") or "").strip()
            if not base:
                port = state.get("port") or 3017
                try:
                    port = int(port)
                except (TypeError, ValueError):
                    port = 3017
                base = f"http://127.0.0.1:{port}"
        if not admin_token:
            admin_token = _pick(state, "admin_token", "token",
                                "admin.token", "session_token") or ""
        if not email:
            email = _pick(state, "admin_email", "email", "admin.email",
                          "admin.user", "admin.username") or ""
        if not password:
            password = _pick(state, "admin_password", "password",
                             "admin.password", "admin.pass") or ""

    base = base.rstrip("/")
    if not base or not (admin_token or (email and password)):
        return None
    return {"base_url": base, "admin_token": admin_token,
            "email": email, "password": password}


def _conf_or_raise() -> dict:
    conf = _router_conf()
    if conf is None:
        raise FreeSettingsError(
            "router_not_configured",
            "The free-model router is not set up on this machine yet.")
    return conf


def _http(conf: dict, method: str, path: str, body, token: str | None):
    url = conf["base_url"] + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read()
            return resp.status, _json_or_text(raw)
    except urllib.error.HTTPError as e:
        try:
            payload = _json_or_text(e.read())
        except Exception:
            payload = None
        return e.code, payload
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise FreeSettingsError(
            "router_unreachable",
            f"The free-model router is not answering on {conf['base_url']}. "
            "Start it, then try again.") from e


def _json_or_text(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")[:500]


def _login(conf: dict) -> str:
    if not (conf.get("email") and conf.get("password")):
        raise FreeSettingsError(
            "router_auth_failed",
            "The saved router credentials are incomplete. Re-run setup "
            "from Settings, or reinstall the free engine.")
    status, payload = _http(conf, "POST", "/api/auth/login",
                            {"email": conf["email"], "password": conf["password"]},
                            None)
    token = payload.get("token") if isinstance(payload, dict) else None
    if status != 200 or not token:
        raise FreeSettingsError(
            "router_auth_failed",
            "The router rejected the saved admin login. Re-run its setup "
            "from Settings, or reinstall the free engine.")
    with _token_lock:
        _token_cache[conf["base_url"]] = (token, time.monotonic() + _TOKEN_TTL_S)
    return token


def _token(conf: dict) -> str:
    with _token_lock:
        cached = _token_cache.get(conf["base_url"])
        if cached and cached[1] > time.monotonic():
            return cached[0]
        admin_bad = conf["base_url"] in _bad_admin_tokens
    # A stored admin_token skips the login round-trip entirely.
    if conf.get("admin_token") and not admin_bad:
        return conf["admin_token"]
    return _login(conf)


def _drop_token(conf: dict, token: str) -> None:
    with _token_lock:
        _token_cache.pop(conf["base_url"], None)
        if token and token == conf.get("admin_token"):
            _bad_admin_tokens.add(conf["base_url"])


def _admin(method: str, path: str, body=None):
    """Authenticated router admin call; returns (status, payload)."""
    conf = _conf_or_raise()
    token = _token(conf)
    status, payload = _http(conf, method, path, body, token)
    if status == 401:
        _drop_token(conf, token)
        token = _token(conf)
        status, payload = _http(conf, method, path, body, token)
    return status, payload


# ---------------------------------------------------------------------------
# Sanitizing + envelope helpers
# ---------------------------------------------------------------------------

def _sanitize(value):
    """Deep-copy a payload minus anything that smells like a secret."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in _SECRET_FIELDS:
                continue
            out[k] = _sanitize(v)
        return out
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def _err_message(payload, fallback: str) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return err["message"][:300]
        if isinstance(err, str):
            return err[:300]
        if isinstance(payload.get("message"), str):
            return payload["message"][:300]
    return fallback


def _relay(handler, status: int, payload, wrap_key: str):
    """Send a proxied result. 2xx -> {ok:True,<wrap_key>:payload}; else -> ok:False."""
    if 200 <= status < 300:
        handler.send_json({"ok": True, wrap_key: _sanitize(payload)})
        return
    handler.send_json({
        "ok": False,
        "code": "upstream_error",
        "error": _err_message(payload, f"The router answered {status}."),
        "upstream_status": status,
    }, status if status in (400, 401, 403, 404, 409, 422) else 502)


def _fail(handler, e: FreeSettingsError):
    handler.send_json({"ok": False, "code": e.code, "error": str(e)}, e.status)


def _read_body(handler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
    except (TypeError, ValueError):
        length = 0
    if not 0 < length <= _MAX_BODY:
        return {}
    try:
        data = json.loads(handler.rfile.read(length) or b"{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _reset_cache():
    """Test hook: drop cached admin tokens and 401 marks."""
    with _token_lock:
        _token_cache.clear()
        _bad_admin_tokens.clear()


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _handle_ping(handler, _qs):
    conf = _router_conf()
    handler.send_json({
        "ok": True,
        "configured": conf is not None,
        "base_url": conf["base_url"] if conf else None,
    })


def _handle_keys_list(handler, _qs):
    try:
        status, payload = _admin("GET", "/api/keys")
    except FreeSettingsError as e:
        return _fail(handler, e)
    _relay(handler, status, payload if isinstance(payload, list) else [], "keys")


def _handle_providers(handler, _qs):
    try:
        status, payload = _admin("GET", "/api/keys/providers")
    except FreeSettingsError as e:
        return _fail(handler, e)
    _relay(handler, status, payload, "providers")


def _handle_usage(handler, qs):
    rng = (qs.get("range") or ["7d"])[0]
    if rng not in _RANGES:
        rng = "7d"
    try:
        s_status, summary = _admin("GET", f"/api/analytics/summary?range={rng}")
        p_status, by_platform = _admin("GET", f"/api/analytics/by-platform?range={rng}")
    except FreeSettingsError as e:
        return _fail(handler, e)
    if not (200 <= s_status < 300):
        return _relay(handler, s_status, summary, "summary")
    handler.send_json({
        "ok": True,
        "range": rng,
        "summary": _sanitize(summary if isinstance(summary, dict) else {}),
        "by_platform": _sanitize(by_platform if isinstance(by_platform, list) else []),
        "by_platform_status": p_status,
    })


def _handle_strategy_get(handler, _qs):
    try:
        status, payload = _admin("GET", "/api/settings/anthropic-map")
    except FreeSettingsError as e:
        return _fail(handler, e)
    _relay(handler, status, payload, "strategy")


def _handle_strategy_post(handler, data):
    family = str(data.get("family") or "default").strip()
    model = str(data.get("model") or "").strip()
    if family not in _FAMILIES:
        return handler.send_json(
            {"ok": False, "code": "bad_family",
             "error": f"family must be one of {', '.join(_FAMILIES)}"}, 400)
    if not model or len(model) > 200 or any(c in model for c in " \t\n\r\"'"):
        return handler.send_json(
            {"ok": False, "code": "bad_model",
             "error": "model must be a short id like 'auto' or a catalog model id"}, 400)
    try:
        status, payload = _admin("PUT", "/api/settings/anthropic-map",
                                 {family: model})
    except FreeSettingsError as e:
        return _fail(handler, e)
    _relay(handler, status, payload, "strategy")


def _handle_key_add(handler, data):
    platform = str(data.get("platform") or "").strip()
    if not _PLATFORM_RE.match(platform):
        return handler.send_json(
            {"ok": False, "code": "bad_platform",
             "error": "Unknown provider."}, 400)
    body: dict = {"platform": platform}
    key = data.get("key")
    if isinstance(key, str) and key.strip():
        if len(key) > 4096:
            return handler.send_json(
                {"ok": False, "code": "bad_key", "error": "That key looks too long."}, 400)
        body["key"] = key.strip()
    label = data.get("label")
    if isinstance(label, str) and label.strip():
        body["label"] = label.strip()[:120]
    try:
        status, payload = _admin("POST", "/api/keys", body)
    except FreeSettingsError as e:
        return _fail(handler, e)
    _relay(handler, status, payload, "key")


def _handle_key_enable(handler, data):
    try:
        kid = int(data.get("id"))
    except (TypeError, ValueError):
        return handler.send_json(
            {"ok": False, "code": "bad_id", "error": "Missing key id."}, 400)
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return handler.send_json(
            {"ok": False, "code": "bad_enabled", "error": "enabled must be true or false."}, 400)
    try:
        status, payload = _admin("PATCH", f"/api/keys/{kid}", {"enabled": enabled})
    except FreeSettingsError as e:
        return _fail(handler, e)
    _relay(handler, status, payload, "key")


def _handle_key_remove(handler, data):
    try:
        kid = int(data.get("id"))
    except (TypeError, ValueError):
        return handler.send_json(
            {"ok": False, "code": "bad_id", "error": "Missing key id."}, 400)
    try:
        status, payload = _admin("DELETE", f"/api/keys/{kid}")
    except FreeSettingsError as e:
        return _fail(handler, e)
    if status == 204 or (isinstance(payload, (str, bytes)) and status < 300):
        return handler.send_json({"ok": True})
    _relay(handler, status, payload, "key")


def _handle_platform_enable(handler, data):
    platform = str(data.get("platform") or "").strip()
    if not _PLATFORM_RE.match(platform):
        return handler.send_json(
            {"ok": False, "code": "bad_platform", "error": "Unknown provider."}, 400)
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return handler.send_json(
            {"ok": False, "code": "bad_enabled", "error": "enabled must be true or false."}, 400)
    try:
        status, payload = _admin("PATCH", f"/api/keys/platform/{platform}",
                                 {"enabled": enabled})
    except FreeSettingsError as e:
        return _fail(handler, e)
    _relay(handler, status, payload, "result")


_GET_ROUTES = {
    "/api/free-settings/ping": _handle_ping,
    "/api/free-settings/keys": _handle_keys_list,
    "/api/free-settings/providers": _handle_providers,
    "/api/free-settings/usage": _handle_usage,
    "/api/free-settings/strategy": _handle_strategy_get,
}

_POST_ROUTES = {
    "/api/free-settings/keys/add": _handle_key_add,
    "/api/free-settings/keys/enable": _handle_key_enable,
    "/api/free-settings/keys/remove": _handle_key_remove,
    "/api/free-settings/platforms/enable": _handle_platform_enable,
    "/api/free-settings/strategy": _handle_strategy_post,
}


def handle(handler, method: str):
    """Dispatch one /api/free-settings/* request. Called from server.py."""
    parsed = urllib.parse.urlparse(handler.path)
    path = parsed.path.rstrip("/") or "/"
    if method == "GET":
        fn = _GET_ROUTES.get(path)
        if fn is None:
            handler.send_json({"ok": False, "error": "unknown free-settings route"}, 404)
            return
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            fn(handler, qs)
        except FreeSettingsError as e:
            _fail(handler, e)
        except Exception as e:  # never 500 the settings modal
            handler.send_json({"ok": False, "code": "internal",
                               "error": str(e)[:200]}, 500)
        return
    fn = _POST_ROUTES.get(path)
    if fn is None:
        handler.send_json({"ok": False, "error": "unknown free-settings route"}, 404)
        return
    try:
        fn(handler, _read_body(handler))
    except FreeSettingsError as e:
        _fail(handler, e)
    except Exception as e:
        handler.send_json({"ok": False, "code": "internal",
                           "error": str(e)[:200]}, 500)
