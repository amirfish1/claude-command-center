# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Free-LLM provider registry and guided key setup for the managed router.

The registry below is the novice-facing catalog used by the free-key wizard
(``static/free-key-wizard.js``): which providers have a real free tier, where
to sign up, what the key looks like, and how good their free models are at
coding. The submit path talks to the CCC-managed freellmapi instance
(L01's ``ccc_server/free_router.py`` owns its lifecycle): it logs in with the
admin account from ``~/.ccc/free-router.json``, stores the key through the
router's own ``POST /api/keys``, then proves it works via
``POST /api/health/check/<id>``.

Secrets discipline: a submitted key is never logged, never written to CCC
state, and never echoed back. Router responses are scrubbed before they are
surfaced so a provider that reflects input cannot leak it into an error.
When a provider rejects a key outright the just-created router row is
deleted, so retries never pile up dead keys.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
# Field contract (GET /api/free-router/providers):
#   platform      router platform id (freellmapi's Platform union)
#   name          display name
#   signup_url    where a novice gets a free key (canonical provider page)
#   key_hint      what a real key looks like, e.g. "sk-or-v1-..."
#   key_regex     loose JS/Python-safe pattern; catches bad pastes locally,
#                 deliberately permissive about suffix so provider format
#                 drift never hard-blocks a valid key
#   keyless       free tier works with no key at all (anonymous)
#   free_no_card  the free tier never asks for a credit card
#   tos_note      one plain line a novice must see before enabling
#   coding_score  0-100 editorial rank: how good this provider's free roster
#                 is for coding-agent work today. Sort key only, refreshed by
#                 hand; not a benchmark claim.
# Extra fields (additive, UI-facing):
#   tagline       one warm line for the card
#   needs_consent the wizard must show tos_note and take an explicit click
#                 before enabling (kilo logs prompts for training)

def _frp_row(platform, name, signup_url, key_hint, key_regex, keyless, free_no_card,
       tos_note, coding_score, tagline, needs_consent=False):
    return {
        "platform": platform,
        "name": name,
        "signup_url": signup_url,
        "key_hint": key_hint,
        "key_regex": key_regex,
        "keyless": keyless,
        "free_no_card": free_no_card,
        "tos_note": tos_note,
        "coding_score": coding_score,
        "tagline": tagline,
        "needs_consent": needs_consent,
    }


# Signup URLs match the upstream freellmapi Keys page
# (client/src/components/keys/shared.tsx) so the wizard never invents links.
FREE_PROVIDERS = (
    _frp_row(
        "kilo", "Kilo Gateway", "https://app.kilo.ai",
        "no key needed", None, True, True,
        "No signup needed. Kilo logs prompts to train their models.",
        55,
        "Zero setup. Free models in one click.",
        needs_consent=True,
    ),
    _frp_row(
        "google", "Google AI Studio", "https://aistudio.google.com/apikey",
        "AIza...", r"^AIza[0-9A-Za-z_-]{30,}$", False, True,
        "Free with a Google account. Generous daily limits.",
        92,
        "Strongest free coding models today.",
    ),
    _frp_row(
        "cerebras", "Cerebras", "https://cloud.cerebras.ai",
        "csk-...", r"^csk-[0-9A-Za-z_-]{30,}$", False, True,
        "Free tier, no card. Extremely fast.",
        88,
        "Huge coding models at wild speed.",
    ),
    _frp_row(
        "groq", "Groq", "https://console.groq.com/keys",
        "gsk_...", r"^gsk_[0-9A-Za-z]{30,}$", False, True,
        "Free with an email signup. Very fast replies.",
        84,
        "Snappy free models, great for quick edits.",
    ),
    _frp_row(
        "openrouter", "OpenRouter", "https://openrouter.ai/keys",
        "sk-or-v1-...", r"^sk-or-[0-9A-Za-z_-]{20,}$", False, True,
        "Free models are the ones marked \":free\". Sign in with Google or GitHub.",
        80,
        "One key unlocks a big free catalog.",
    ),
    _frp_row(
        "github", "GitHub Models", "https://github.com/settings/tokens",
        "ghp_... or github_pat_...", r"^(ghp_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{20,})$",
        False, True,
        "Free with any GitHub account. Uses a personal access token.",
        76,
        "Free models from the account you already have.",
    ),
    _frp_row(
        "mistral", "Mistral", "https://console.mistral.ai/api-keys/",
        "32 letters and numbers", r"^[0-9A-Za-z]{32,}$", False, True,
        "Free tier. Signup asks for a phone number.",
        74,
        "Free European models, solid at code.",
    ),
    _frp_row(
        "nvidia", "NVIDIA NIM", "https://build.nvidia.com/settings/api-keys",
        "nvapi-...", r"^nvapi-[0-9A-Za-z_-]{20,}$", False, True,
        "Free starter credits, no card needed.",
        72,
        "Big open models on NVIDIA's free cloud.",
    ),
    _frp_row(
        "huggingface", "Hugging Face", "https://huggingface.co/settings/tokens",
        "hf_...", r"^hf_[0-9A-Za-z]{20,}$", False, True,
        "Free with a Hugging Face account. Small monthly free credit.",
        65,
        "A friendly hub with a small free tier.",
    ),
)

_FRP_INDEX = {p["platform"]: p for p in FREE_PROVIDERS}
# Compiled once; registry values are constants.
_FRP_REGEX = {
    p["platform"]: re.compile(p["key_regex"])
    for p in FREE_PROVIDERS if p["key_regex"]
}

# ---------------------------------------------------------------------------
# Router discovery + client
# ---------------------------------------------------------------------------

_FRP_DEFAULT_PORT = 3017  # PLAN: CCC-managed freellmapi binds 127.0.0.1:3017
_FRP_TOKEN_CACHE = {"base": None, "token": None, "at": 0.0}
_FRP_TOKEN_LOCK = threading.Lock()
_FRP_STATE_CACHE = {"at": 0.0, "data": {}}
_FRP_STATE_TTL_S = 5.0  # a polling view must not hammer the router's /api/keys


def _frp_state_file():
    """Path to the router's state file (``~/.ccc/free-router.json``).

    L01 writes it (0600) with the admin credentials and port. The env
    override keeps tests and dev boxes pointed at a fixture instead of the
    real file."""
    override = (os.environ.get("CCC_FREE_ROUTER_STATE") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".ccc" / "free-router.json"


def _frp_read_state():
    """Dict from the router state file, or {} when absent/unreadable."""
    try:
        data = json.loads(_frp_state_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def free_router_base_url():
    """Base URL of the managed router, e.g. ``http://127.0.0.1:3017``.

    Resolution order: explicit env override (tests/dev) -> ``base_url`` or
    ``port`` from the state file -> the well-known default port. Always
    loopback; the router must never be reached off this machine."""
    override = (os.environ.get("CCC_FREE_ROUTER_URL") or "").strip().rstrip("/")
    if override:
        return override
    state = _frp_read_state()
    base = str(state.get("base_url") or "").strip().rstrip("/")
    if base:
        return base
    port = state.get("port") or _FRP_DEFAULT_PORT
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = _FRP_DEFAULT_PORT
    return f"http://127.0.0.1:{port}"


def _frp_admin_credentials():
    """(email, password) for the router admin account from the state file.

    Field names are read liberally so small schema drift in L01's writer
    doesn't break the wizard."""
    state = _frp_read_state()
    admin = state.get("admin") if isinstance(state.get("admin"), dict) else {}
    email = (
        state.get("admin_email") or state.get("email")
        or admin.get("email") or ""
    )
    password = (
        state.get("admin_password") or state.get("password")
        or admin.get("password") or ""
    )
    return str(email).strip(), str(password)


def _frp_scrub(text, secret=None):
    """Cap an error string and strip any echoed key material from it."""
    msg = str(text or "")
    if secret:
        msg = msg.replace(secret, "***")
    return msg[:300]


class FreeRouterUnavailable(Exception):
    """The managed router is not reachable or has no usable admin account."""


class FreeRouterError(Exception):
    """The router answered with an error (already secret-scrubbed)."""


def _frp_request(method, path, body=None, token=None, timeout=10, secret=None):
    """One JSON call against the managed router. Returns (status_code, dict).

    Raises FreeRouterUnavailable when the router cannot be reached at all, so
    callers can show "start your free router" instead of a stack trace."""
    url = free_router_base_url() + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(256 * 1024)
            return resp.status, _frp_json(raw)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(256 * 1024)
        except OSError:
            raw = b""
        return e.code, _frp_json(raw)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise FreeRouterUnavailable(_frp_scrub(getattr(e, "reason", e), secret))


def _frp_json(raw):
    try:
        data = json.loads(raw or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, (dict, list)) else {}


def _frp_error_message(payload, fallback="Something went wrong"):
    """Pull freellmapi's ``{error:{message}}`` shape out of a response."""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
        if payload.get("message"):
            return str(payload["message"])
    return fallback


def _frp_login(force=False):
    """Admin session token for the router, cached for the process.

    ``CCC_FREE_ROUTER_TOKEN`` bypasses login entirely (tests against a fake
    router that has no auth). A 401 on a cached token triggers one forced
    re-login by the caller's retry."""
    env_token = (os.environ.get("CCC_FREE_ROUTER_TOKEN") or "").strip()
    if env_token:
        return env_token
    base = free_router_base_url()
    with _FRP_TOKEN_LOCK:
        if not force and _FRP_TOKEN_CACHE["base"] == base and _FRP_TOKEN_CACHE["token"]:
            return _FRP_TOKEN_CACHE["token"]
        # L01 persists a session token next to the admin credentials; using
        # it skips a login roundtrip. A stale one falls through to login via
        # the caller's 401 retry.
        state = _frp_read_state()
        saved = str(state.get("admin_token") or state.get("token") or "").strip()
        if saved and not force:
            _FRP_TOKEN_CACHE.update({"base": base, "token": saved, "at": time.time()})
            return saved
        email, password = _frp_admin_credentials()
        if not email or not password:
            raise FreeRouterUnavailable("router admin account not configured")
        status, payload = _frp_request(
            "POST", "/api/auth/login",
            {"email": email, "password": password}, timeout=10,
        )
        if status == 401:
            raise FreeRouterError("router admin login was refused")
        if status >= 400:
            raise FreeRouterError(_frp_error_message(payload, "router login failed"))
        token = str(payload.get("token") or "").strip()
        if not token:
            raise FreeRouterError("router login returned no token")
        _FRP_TOKEN_CACHE.update({"base": base, "token": token, "at": time.time()})
        return token


def _frp_authed_request(method, path, body=None, timeout=10, secret=None):
    """Request with the admin Bearer token; one re-login retry on 401."""
    token = _frp_login()
    status, payload = _frp_request(method, path, body, token=token,
                               timeout=timeout, secret=secret)
    if status == 401 and not os.environ.get("CCC_FREE_ROUTER_TOKEN"):
        token = _frp_login(force=True)
        status, payload = _frp_request(method, path, body, token=token,
                                   timeout=timeout, secret=secret)
    return status, payload


def free_router_reachable():
    """Cheap liveness probe for the wizard's "starting router" state."""
    try:
        status, _ = _frp_request("GET", "/api/auth/status", timeout=4)
    except FreeRouterUnavailable:
        return False
    return status < 500


# ---------------------------------------------------------------------------
# Provider listing (contract: GET /api/free-router/providers)
# ---------------------------------------------------------------------------

def _frp_key_state():
    """{platform: {configured, status, enabled}} from the router's key rows.

    Best effort: any failure returns {} and the wizard renders the catalog
    without configured badges. Only masked metadata crosses the wire.
    Cached briefly so a view that polls /api/free-router/providers costs
    one router call per TTL, not one per request."""
    now = time.time()
    if now - _FRP_STATE_CACHE["at"] < _FRP_STATE_TTL_S:
        return _FRP_STATE_CACHE["data"]
    data = _frp_key_state_live()
    _FRP_STATE_CACHE.update({"at": now, "data": data})
    return data


def _frp_key_state_live():
    try:
        status, payload = _frp_authed_request("GET", "/api/keys", timeout=8)
    except (FreeRouterUnavailable, FreeRouterError):
        return {}
    if status >= 400:
        return {}
    # freellmapi answers a bare list; tolerate a {"keys": [...]} wrap so a
    # future shape change degrades instead of hiding every key row.
    if isinstance(payload, dict):
        rows = payload.get("keys")
    else:
        rows = payload
    if not isinstance(rows, list):
        return {}
    state = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        platform = str(row.get("platform") or "")
        if platform not in _FRP_INDEX:
            continue
        bucket = state.setdefault(platform, {"configured": False, "status": None,
                                             "enabled": False, "masked_key": None})
        bucket["configured"] = True
        if row.get("maskedKey"):
            bucket["masked_key"] = row["maskedKey"]
        if row.get("enabled"):
            bucket["enabled"] = True
        # Keep the "best" status a novice cares about: healthy beats all.
        s = row.get("status")
        if s and bucket["status"] != "healthy":
            bucket["status"] = s
    return state


def free_provider_catalog():
    """The wizard's catalog: registry rows plus live configured state.

    Returns the contract's bare list. Static registry fields always present;
    ``has_key``/``key_status`` appear when the router is reachable."""
    live = _frp_key_state()
    rows = []
    for entry in FREE_PROVIDERS:
        row = dict(entry)
        st = live.get(entry["platform"]) or {}
        if st:
            row["has_key"] = bool(st.get("configured"))
            row["key_status"] = st.get("status")
            row["key_enabled"] = bool(st.get("enabled"))
            if st.get("masked_key"):
                row["masked_key"] = st["masked_key"]
        else:
            row["has_key"] = False
            row["key_status"] = None
            row["key_enabled"] = False
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Key submission (contract: POST /api/free-router/keys -> {ok,validated,error})
# ---------------------------------------------------------------------------

def _frp_format_error(provider):
    hint = provider.get("key_hint") or ""
    if hint and " " not in hint:
        return (f"That does not look like a {provider['name']} key. "
                f"It usually starts with {hint}")
    return f"That does not look like a {provider['name']} key. Check the paste."


def submit_free_key(platform, key=None, consent=False):
    """Validate, store, and health-check a provider key through the router.

    Contract response ``{ok, validated, error}`` plus additive fields
    (``code``, ``platform``, ``masked_key``). Semantics:

      ok=True, validated=True   key stored and proven by the health probe
      ok=True, validated=False  stored but the probe could not confirm
                                (provider/network flake); the key stays and
                                the router keeps checking it on its own cycle
      ok=False                  nothing persisted (bad format, consent
                                missing, provider rejected the key, router
                                unreachable)
    """
    platform = str(platform or "").strip().lower()
    provider = _FRP_INDEX.get(platform)
    if provider is None:
        return {"ok": False, "validated": False,
                "error": f"Unknown provider '{platform}'.",
                "code": "unknown_platform"}

    key = str(key or "").strip()
    keyless = bool(provider["keyless"])
    if provider.get("needs_consent") and not consent:
        return {"ok": False, "validated": False,
                "error": provider["tos_note"],
                "code": "consent_required", "platform": platform}

    if not keyless and not key:
        return {"ok": False, "validated": False,
                "error": f"Paste your {provider['name']} API key first.",
                "code": "key_required", "platform": platform}

    pattern = _FRP_REGEX.get(platform)
    if key and pattern and not pattern.match(key):
        return {"ok": False, "validated": False,
                "error": _frp_format_error(provider),
                "code": "key_format", "platform": platform}

    try:
        body = {"platform": platform, "label": "added by CCC"}
        if key:
            body["key"] = key
        status, payload = _frp_authed_request("POST", "/api/keys", body,
                                          timeout=15, secret=key or None)
    except FreeRouterUnavailable:
        return {"ok": False, "validated": False,
                "error": "Your free router is not running yet. "
                         "Start it, then try again.",
                "code": "router_unavailable", "platform": platform}
    except FreeRouterError as e:
        return {"ok": False, "validated": False,
                "error": _frp_scrub(e, key or None),
                "code": "router_error", "platform": platform}

    if status >= 400:
        return {"ok": False, "validated": False,
                "error": _frp_scrub(_frp_error_message(
                    payload, "The router could not save that key."), key or None),
                "code": "router_rejected", "platform": platform}

    key_id = payload.get("id") if isinstance(payload, dict) else None
    masked = payload.get("maskedKey") if isinstance(payload, dict) else None
    result = {"ok": True, "platform": platform}
    if masked:
        result["masked_key"] = masked
    if keyless and not key:
        result["keyless"] = True

    if not isinstance(key_id, int):
        # 201 should always carry an id; if it did not, treat save as
        # unconfirmed rather than claiming a probe we cannot run.
        result.update({"validated": False,
                       "error": "Saved, but the health check could not run.",
                       "code": "unconfirmed"})
        return result

    return _frp_validate_row(result, key_id, provider, real_key=bool(key))


def _frp_validate_row(result, key_id, provider, real_key):
    """Health-probe one router key row; fold the verdict into `result`."""
    try:
        status, payload = _frp_authed_request(
            "POST", f"/api/health/check/{key_id}", timeout=45)
    except (FreeRouterUnavailable, FreeRouterError):
        result.update({"validated": False,
                       "error": "Saved, but the health check did not answer. "
                                "The router keeps checking it.",
                       "code": "unconfirmed"})
        return result

    verdict = str((payload or {}).get("status") or "")
    if status >= 400:
        verdict = ""
    result["key_status"] = verdict or None

    if verdict == "healthy":
        result["validated"] = True
        return result

    if verdict == "invalid":
        # Provider rejected the credential. Remove the just-added row so the
        # platform does not sit "configured" on a dead key, then tell the
        # novice plainly. Deletion is best-effort: a leftover row still
        # reads as invalid everywhere it surfaces.
        if real_key:
            try:
                _frp_authed_request("DELETE", f"/api/keys/{key_id}", timeout=10)
            except (FreeRouterUnavailable, FreeRouterError):
                pass
            return {"ok": False, "validated": False,
                    "error": f"{provider['name']} did not accept that key. "
                             "Check it and paste again.",
                    "code": "rejected", "platform": provider["platform"]}
        return {"ok": True, "validated": False,
                "error": f"{provider['name']} is not accepting connections "
                         "right now. Saved; it should recover on its own.",
                "code": "unconfirmed", "platform": provider["platform"]}

    # error / unknown / empty: transport flake or a provider that has no
    # cheap probe. The key stays; the router's own health cycle rechecks.
    result.update({"validated": False,
                   "error": "Saved. The provider did not confirm yet; "
                            "the router keeps checking it.",
                   "code": "unconfirmed"})
    return result
