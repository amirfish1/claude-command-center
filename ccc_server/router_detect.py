# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Detect free / self-hosted model routers that already exist on this machine.

Many users who land on CCC already run something that can serve models for
free or nearly free — a hand-installed freellmapi on :3001, a 9Router or
OmniRoute gateway on :20128, a free-claude-code proxy on :8082, Ollama on
:11434, LM Studio on :1234 — or already hold an OpenRouter key in CCC's BYOK
store. The onboarding flow (and the dashboard card in
static/router-detected.js) uses this inventory to offer "use your existing
router" instead of installing a second one.

Backs:
  GET  /api/free-router/detected          -> detect_routers() payload
  POST /api/free-router/detected/prefer   -> set_preferred_router()
  GET  /api/free-router/detected/<id>     -> router_by_id()

Design rules:
  - Loopback probes only. Ports and paths are fixed in _PROBE_PLAN; nothing
    user-controlled ever becomes a URL (no SSRF surface).
  - Probes run in parallel threads, each with a hard sub-second timeout, so
    the endpoint answers in ~1s worst case and is TTL-cached on top.
  - Never raises: a wedged probe degrades to "not found", not a 500.
  - Never reads or returns secrets. Key-bearing detections (BYOK, env) report
    presence only.

Stdlib-only, same contract as every ccc_server module. Names still living in
server.py are reached via ``_core`` at call time.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Probe plan
# ---------------------------------------------------------------------------
# Each HTTP probe: (probe_id, port, list of (method, path) tried in order).
# A probe only counts as "detected" when a fingerprint function confirms the
# product — an open port serving something unrecognizable is never reported
# as a router (a random dev server on :8082 is not free-claude-code).

_PROBE_TIMEOUT_S = 0.8
_SCAN_TTL_S = 45.0
_MAX_BODY = 96_000

FREELLMAPI_USER_PORT = 3001       # default of a user's own freellmapi install
FREELLMAPI_CCC_PORT = 3017        # the CCC-managed free router (L01)
NINEROUTER_PORT = 20128           # 9Router and OmniRoute share this default
FREECLAUDECODE_PORT = 8082        # free-claude-code FastAPI proxy
OLLAMA_PORT = 11434
LMSTUDIO_PORT = 1234

_scan_cache = {"ts": 0.0, "data": None}
_scan_lock = threading.Lock()


def _fetch_json(url, timeout=_PROBE_TIMEOUT_S):
    """GET a URL, return (status, parsed_json_or_None, raw_head_text)."""
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "ccc-router-detect"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(_MAX_BODY)
            status = resp.status
    except urllib.error.HTTPError as e:
        # A non-2xx still tells us something is listening; keep the body.
        try:
            body = e.read(_MAX_BODY)
        except Exception:
            body = b""
        status = e.code
    except Exception:
        return 0, None, ""
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    return status, parsed, text


def _looks_like_freellmapi(port):
    """Fingerprint: /v1/openapi.json advertises info.title == 'FreeLLMAPI'."""
    base = f"http://127.0.0.1:{port}"
    status, spec, _ = _fetch_json(f"{base}/v1/openapi.json")
    if isinstance(spec, dict):
        title = str((spec.get("info") or {}).get("title") or "")
        if "freellmapi" in title.lower():
            return True, str((spec.get("info") or {}).get("version") or "")
    # Older builds may not serve the spec; /api/ping is the cheap fallback.
    status, ping, _ = _fetch_json(f"{base}/api/ping")
    if status == 200 and isinstance(ping, dict) and ping.get("status") == "ok":
        return True, ""
    return False, ""


def _detect_freellmapi():
    """freellmapi on :3001 (user's own) and :3017 (CCC-managed)."""
    found = []
    for port, managed in ((FREELLMAPI_USER_PORT, False), (FREELLMAPI_CCC_PORT, True)):
        is_fl, version = _looks_like_freellmapi(port)
        if not is_fl:
            continue
        base = f"http://127.0.0.1:{port}"
        found.append({
            "id": "freellmapi-ccc" if managed else "freellmapi",
            "name": "FreeLLMAPI (managed by CCC)" if managed else "FreeLLMAPI",
            "kind": "router",
            "status": "running",
            "port": port,
            "base_url": base,
            "openai_base_url": f"{base}/v1",
            "anthropic_base_url": base,
            "keyless": False,
            "free": True,
            "managed_by_ccc": managed,
            "version": version or None,
            "supports": ["openai", "anthropic", "gemini", "responses"],
            "detail": (
                "The router CCC installs for you."
                if managed else
                "Your own freellmapi — already routes to free provider tiers."
            ),
        })
    return found


def _detect_port20128():
    """9Router / OmniRoute share :20128. Fingerprint via the dashboard HTML."""
    base = f"http://127.0.0.1:{NINEROUTER_PORT}"
    status, _, html = _fetch_json(base + "/", timeout=1.0)
    if not status:
        return []
    low = html.lower()
    if "omniroute" in low:
        rid, name = "omniroute", "OmniRoute"
    elif "9router" in low or "9-router" in low or "n9router" in low:
        rid, name = "ninerouter", "9Router"
    else:
        # Unknown page on 20128 — check the API surface before claiming it.
        mstatus, models, _ = _fetch_json(f"{base}/v1/models")
        looks_openai = isinstance(models, dict) and (
            models.get("object") == "list" or "data" in models or "error" in models
        )
        if not looks_openai:
            return []
        rid, name = "router-20128", "AI router"
    keyless = False
    models_count = None
    mstatus, models, _ = _fetch_json(f"{base}/v1/models")
    if mstatus == 200 and isinstance(models, dict):
        data = models.get("data")
        keyless = True
        if isinstance(data, list):
            models_count = len(data)
    return [{
        "id": rid,
        "name": name,
        "kind": "router",
        "status": "running",
        "port": NINEROUTER_PORT,
        "base_url": base,
        "openai_base_url": f"{base}/v1",
        "anthropic_base_url": base,
        "keyless": keyless,
        "free": True,
        "managed_by_ccc": False,
        "version": None,
        "models_count": models_count,
        "supports": ["openai", "responses"],
        "detail": (
            f"Serving {models_count} models."
            if models_count else
            "OpenAI-compatible gateway with free tiers and auto-fallback."
        ),
    }]


def _detect_freeclaudecode():
    """free-claude-code FastAPI proxy on :8082 (plus installed-not-running)."""
    base = f"http://127.0.0.1:{FREECLAUDECODE_PORT}"
    status, spec, _ = _fetch_json(f"{base}/openapi.json")
    identified = False
    if isinstance(spec, dict):
        title = str((spec.get("info") or {}).get("title") or "").lower()
        identified = "claude" in title or "free" in title
    if not identified:
        # FastAPI /docs (Swagger UI) or /admin both fingerprint the proxy.
        dstatus, _, docs = _fetch_json(f"{base}/docs")
        if dstatus == 200 and "swagger" in docs.lower():
            identified = True
    if identified:
        return [{
            "id": "freeclaudecode",
            "name": "free-claude-code",
            "kind": "router",
            "status": "running",
            "port": FREECLAUDECODE_PORT,
            "base_url": base,
            "openai_base_url": None,
            "anthropic_base_url": base,
            "keyless": True,
            "free": True,
            "managed_by_ccc": False,
            "version": None,
            "supports": ["anthropic"],
            "detail": "Anthropic-compatible proxy onto NIM / OpenRouter / local models.",
        }]
    # Installed but not running: the CLI writes ~/.config/free-claude-code/.env.
    try:
        cfg = Path.home() / ".config" / "free-claude-code" / ".env"
        if cfg.is_file():
            return [{
                "id": "freeclaudecode",
                "name": "free-claude-code",
                "kind": "router",
                "status": "installed",
                "port": FREECLAUDECODE_PORT,
                "base_url": base,
                "openai_base_url": None,
                "anthropic_base_url": base,
                "keyless": True,
                "free": True,
                "managed_by_ccc": False,
                "version": None,
                "supports": ["anthropic"],
                "detail": "Installed — start it with the `free-claude-code` command.",
            }]
    except OSError:
        pass
    return []


def _detect_ollama():
    base = f"http://127.0.0.1:{OLLAMA_PORT}"
    status, payload, _ = _fetch_json(f"{base}/api/version")
    if status != 200 or not isinstance(payload, dict) or "version" not in payload:
        # Installed-but-not-running hint: ~/.ollama exists.
        try:
            if (Path.home() / ".ollama").is_dir():
                return [{
                    "id": "ollama", "name": "Ollama", "kind": "local_models",
                    "status": "installed", "port": OLLAMA_PORT, "base_url": base,
                    "openai_base_url": f"{base}/v1", "anthropic_base_url": None,
                    "keyless": True, "free": True, "managed_by_ccc": False,
                    "version": None, "supports": ["openai"],
                    "detail": "Installed — start it with `ollama serve`.",
                }]
        except OSError:
            pass
        return []
    models_count = None
    tstatus, tags, _ = _fetch_json(f"{base}/api/tags")
    if tstatus == 200 and isinstance(tags, dict) and isinstance(tags.get("models"), list):
        models_count = len(tags["models"])
    return [{
        "id": "ollama",
        "name": "Ollama",
        "kind": "local_models",
        "status": "running",
        "port": OLLAMA_PORT,
        "base_url": base,
        "openai_base_url": f"{base}/v1",
        "anthropic_base_url": None,
        "keyless": True,
        "free": True,
        "managed_by_ccc": False,
        "version": str(payload.get("version") or "") or None,
        "models_count": models_count,
        "supports": ["openai", "ollama"],
        "detail": (
            f"{models_count} local model{'s' if models_count != 1 else ''} — "
            "runs free, fully on your Mac."
            if models_count is not None else
            "Local models — free, fully on your Mac."
        ),
    }]


def _detect_lmstudio():
    base = f"http://127.0.0.1:{LMSTUDIO_PORT}"
    # LM Studio serves an OpenAI-compatible /v1 plus its own /api/v0/models.
    status, payload, _ = _fetch_json(f"{base}/api/v0/models")
    models_count = None
    identified = False
    if status == 200 and isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            identified = True
            models_count = len(data)
    if not identified:
        vstatus, vm, _ = _fetch_json(f"{base}/v1/models")
        if vstatus == 200 and isinstance(vm, dict) and vm.get("object") == "list":
            identified = True
            data = vm.get("data")
            if isinstance(data, list):
                models_count = len(data)
    if not identified:
        return []
    return [{
        "id": "lmstudio",
        "name": "LM Studio",
        "kind": "local_models",
        "status": "running",
        "port": LMSTUDIO_PORT,
        "base_url": base,
        "openai_base_url": f"{base}/v1",
        "anthropic_base_url": None,
        "keyless": True,
        "free": True,
        "managed_by_ccc": False,
        "version": None,
        "models_count": models_count,
        "supports": ["openai"],
        "detail": (
            f"{models_count} local model{'s' if models_count != 1 else ''} loaded."
            if models_count is not None else "Local models — free, on your Mac."
        ),
    }]


def _detect_byok_openrouter():
    """An OpenRouter key already saved in CCC's BYOK store = free-tier access
    without any router at all (OpenRouter hosts `*:free` models)."""
    try:
        profiles = _core.byok_list_profiles()
    except Exception:
        profiles = []
    has_key = any("openrouter" in (p.get("providers") or []) for p in profiles)
    if not has_key:
        has_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    if not has_key:
        return []
    return [{
        "id": "openrouter-key",
        "name": "OpenRouter key",
        "kind": "key",
        "status": "configured",
        "port": None,
        "base_url": "https://openrouter.ai/api/v1",
        "openai_base_url": "https://openrouter.ai/api/v1",
        "anthropic_base_url": None,
        "keyless": False,
        "free": "partial",
        "managed_by_ccc": False,
        "version": None,
        "supports": ["openai"],
        "detail": "You already have an OpenRouter key — its :free models cost $0.",
    }]


def _detect_env_routes():
    """Env vars that already point an engine at a custom gateway."""
    found = []
    anthropic = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip()
    if anthropic and anthropic != "https://api.anthropic.com":
        found.append({
            "id": "env-anthropic-base-url",
            "name": "ANTHROPIC_BASE_URL",
            "kind": "env",
            "status": "configured",
            "port": None,
            "base_url": anthropic,
            "openai_base_url": None,
            "anthropic_base_url": anthropic,
            "keyless": None,
            "free": None,
            "managed_by_ccc": False,
            "version": None,
            "supports": ["anthropic"],
            "detail": f"Claude Code launches already route to {anthropic}.",
        })
    openai = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    if openai and "api.openai.com" not in openai:
        found.append({
            "id": "env-openai-base-url",
            "name": "OPENAI_BASE_URL",
            "kind": "env",
            "status": "configured",
            "port": None,
            "base_url": openai,
            "openai_base_url": openai,
            "anthropic_base_url": None,
            "keyless": None,
            "free": None,
            "managed_by_ccc": False,
            "version": None,
            "supports": ["openai"],
            "detail": f"OpenAI-compatible tools already route to {openai}.",
        })
    return found


_DETECTORS = (
    _detect_freellmapi,
    _detect_port20128,
    _detect_freeclaudecode,
    _detect_ollama,
    _detect_lmstudio,
    _detect_byok_openrouter,
    _detect_env_routes,
)


# ---------------------------------------------------------------------------
# Preferred-router choice (what "Use this router" persists)
# ---------------------------------------------------------------------------

def _choice_path():
    return _core.COMMAND_CENTER_STATE_DIR / "free-router-choice.json"


def preferred_router_id():
    try:
        data = json.loads(_choice_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rid = data.get("preferred")
    return rid if isinstance(rid, str) and rid else None


def set_preferred_router(router_id, key=None):
    """Persist the user's pick. None clears. Returns the stored id.

    ``key`` is the router's own API key when the router isn't keyless —
    stored in the same 0600 file and read back by free_engines at spawn
    time. It is never returned by any API."""
    path = _choice_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if router_id:
            data = {"preferred": router_id, "set_at": time.time()}
            if isinstance(key, str) and key.strip():
                data["key"] = key.strip()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return None
    return router_id


def _rank(entry):
    order = {"running": 0, "configured": 1, "installed": 2}
    return order.get(entry.get("status") or "", 3)


def detect_routers(fresh=False):
    """Payload for GET /api/free-router/detected. TTL-cached, never raises."""
    now = time.monotonic()
    with _scan_lock:
        if (
            not fresh
            and _scan_cache["data"] is not None
            and now - _scan_cache["ts"] < _SCAN_TTL_S
        ):
            cached = dict(_scan_cache["data"])
            cached["cached"] = True
            return cached
    started = time.monotonic()
    routers = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(_DETECTORS)) as pool:
        futures = [pool.submit(fn) for fn in _DETECTORS]
        for fut in futures:
            try:
                for entry in fut.result() or []:
                    if isinstance(entry, dict) and entry.get("id"):
                        routers.append(entry)
            except Exception:
                continue
    routers.sort(key=lambda e: (_rank(e), e.get("name") or ""))
    preferred = preferred_router_id()
    if preferred and not any(r.get("id") == preferred for r in routers):
        preferred = None  # chosen router vanished; drop the stale pointer
    data = {
        "ok": True,
        "routers": routers,
        "preferred": preferred,
        "count": len(routers),
        "scan_ms": round((time.monotonic() - started) * 1000),
    }
    with _scan_lock:
        _scan_cache["ts"] = time.monotonic()
        _scan_cache["data"] = dict(data)
    return data


def router_by_id(router_id, fresh=False):
    """The detection record for one id, or None (also when it stopped)."""
    for entry in detect_routers(fresh=fresh).get("routers") or []:
        if entry.get("id") == router_id:
            return entry
    return None


def chosen_router():
    """The router record the user picked, or None. Fresh look so a router
    that was just stopped doesn't keep getting offered."""
    rid = preferred_router_id()
    if not rid:
        return None
    return router_by_id(rid, fresh=True)
