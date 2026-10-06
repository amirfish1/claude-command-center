"""$0 spawn runtime: point one spawned engine at the CCC-managed free router.

The free router (freellmapi, loopback only) is owned by the free-router
subsystem (``ccc_server.free_router``). This module is the spawn-side slice:
given ``runtime: "free"`` on a spawn request, build the env overlay that puts
the child process on $0 models, scrub inherited paid credentials so a session
can never bill the user's plan, and stamp the runtime onto the spawn records
the dashboard reads (registry row, spawn marker, log line).

Contract kept on purpose:
- ``spawn_env(engine, model)`` returns ``{}`` whenever the router cannot
  serve this engine. Callers treat {} as "unavailable" and refuse the spawn:
  a requested $0 run must never silently become a paid one.
- Nothing here writes ``~/.claude/settings.json`` or touches the user's own
  Claude auth. Free routing is per-child env only.

Engines:
- ``claude``: Anthropic endpoint. ``ANTHROPIC_BASE_URL`` +
  ``ANTHROPIC_AUTH_TOKEN`` (+ ``ANTHROPIC_MODEL`` when a model is known).
- ``opencode``/``aider``: OpenAI-compatible ``/v1`` endpoint —
  ``OPENAI_BASE_URL``/``OPENAI_API_BASE`` + ``OPENAI_API_KEY`` and a
  ``<provider>/<model>`` command-line id.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket

from ccc_server import core as _core

FREE_RUNTIME = "free"
FREE_RUNTIME_ENGINES = ("claude", "opencode", "aider")
FREE_RUNTIME_LABEL = "Free ($0)"

# Marker/runtime values are capped like the other spawn-marker fields.
_RUNTIME_VALUE_MAX = 32

# Env vars inherited from the dashboard's own process that must NOT reach a
# free-router child. The router's Anthropic endpoint accepts x-api-key; a
# subscription credential riding along in the environment would bill (or at
# minimum leak through) the paid path the user opted out of.
_PAID_CREDENTIAL_ENV = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_SESSION_KEY",
)

# Advertised to the child's hooks/tools so they can recognise a $0 session
# without re-reading the router state (hooks/session-start.py stamps the
# spawn marker; savings accounting keys off the same value).
RUNTIME_ENV_VAR = "CCC_SESSION_RUNTIME"

_DEFAULT_ROUTER_PORT = 3017
_ROUTER_PROBE_TIMEOUT_S = 0.4


def _state_file():
    override = os.environ.get("CCC_FREE_ROUTER_STATE")
    if override:
        return Path(override)
    return Path.home() / ".ccc" / "free-router.json"


def router_state():
    """The free-router state file as a dict, {} when absent/malformed.

    Written by the router installer with mode 0600: port, version, pinned
    rev, admin account, and the unified inference key. Read-only here —
    this module never provisions, only consumes.
    """
    try:
        data = json.loads(_state_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _state_value(state, *names):
    for name in names:
        value = str(state.get(name) or "").strip()
        if value:
            return value
    return ""


def router_base_url(state=None):
    state = router_state() if state is None else state
    base = _state_value(state, "base_url", "anthropic_base_url", "url")
    if base:
        return base.rstrip("/")
    try:
        port = int(state.get("port") or _DEFAULT_ROUTER_PORT)
    except (TypeError, ValueError):
        port = _DEFAULT_ROUTER_PORT
    return f"http://127.0.0.1:{port}"


def _router_port(state=None):
    state = router_state() if state is None else state
    try:
        return int(state.get("port") or _DEFAULT_ROUTER_PORT)
    except (TypeError, ValueError):
        return _DEFAULT_ROUTER_PORT


def unified_key(state=None):
    """The router's unified inference key, or "" (never logged, never echoed)."""
    state = router_state() if state is None else state
    return _state_value(state, "unified_key", "api_key", "inference_key")


def router_listening(state=None):
    """Cheap TCP probe: is something accepting connections on the router port?

    Deliberately not an HTTP request — a plain connect is enough to tell a
    stopped router from a running one and works before the HTTP stack is
    even answering.
    """
    port = _router_port(state)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_ROUTER_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def _free_router_module():
    """ccc_server.free_router when that subsystem is present, else None."""
    try:
        from ccc_server import free_router
    except ImportError:
        return None
    return free_router


def _delegate_spawn_env(model):
    """L01's authoritative env builder; {} when the module or readiness is absent."""
    mod = _free_router_module()
    spawn_env = getattr(mod, "spawn_env", None) if mod is not None else None
    if not callable(spawn_env):
        return {}
    try:
        env = spawn_env(model)
    except Exception:
        return {}
    return env if isinstance(env, dict) else {}


def _claude_env_from_state(state, model):
    """Fallback env built straight from the state file, for before/without L01."""
    key = unified_key(state)
    if not key:
        return {}
    env = {
        "ANTHROPIC_BASE_URL": router_base_url(state),
        "ANTHROPIC_AUTH_TOKEN": key,
    }
    resolved = str(model or "").strip() or _state_value(state, "default_model", "model")
    if resolved:
        env["ANTHROPIC_MODEL"] = resolved
    return env


def _openai_env_from_state(state):
    """OpenAI-compatible /v1 env for opencode/aider, from the state file."""
    key = unified_key(state)
    if not key:
        return {}
    base = router_base_url(state) + "/v1"
    return {
        # aider (litellm) reads OPENAI_API_BASE; opencode (AI SDK) reads
        # OPENAI_BASE_URL. Setting both keeps each client on its native var.
        "OPENAI_API_BASE": base,
        "OPENAI_BASE_URL": base,
        "OPENAI_API_KEY": key,
    }


def spawn_env(engine, model=None):
    """Env overlay for a $0 child of ``engine`` — {} when the router can't serve.

    ``model`` is the caller's explicit pick (or None for the router default).
    The returned dict never contains a paid-provider credential and is safe
    to merge over the inherited environment after ``scrub_paid_env``.
    """
    engine = str(engine or "").strip().lower()
    if engine not in FREE_RUNTIME_ENGINES:
        return {}
    if engine == "claude":
        env = _delegate_spawn_env(model)
        if env:
            out = dict(env)
        else:
            state = router_state()
            out = _claude_env_from_state(state, model)
            if out and not router_listening(state):
                return {}
        if not out.get("ANTHROPIC_BASE_URL") or not out.get("ANTHROPIC_AUTH_TOKEN"):
            return {}
    else:
        state = router_state()
        out = _openai_env_from_state(state)
        if out and not router_listening(state):
            return {}
    if not out:
        return {}
    out[RUNTIME_ENV_VAR] = FREE_RUNTIME
    return out


def scrub_paid_env(env):
    """Drop inherited paid-provider credentials from a child env in place."""
    for key in _PAID_CREDENTIAL_ENV:
        env.pop(key, None)
    return env


def apply_to_env(env, engine, model=None):
    """Merge the $0 overlay into ``env``; returns True when the overlay applied.

    False means the router cannot serve — caller must refuse the spawn rather
    than proceed paid. Paid credentials are scrubbed regardless of merge
    order so the child's environment never mixes auth sources.
    """
    overlay = spawn_env(engine, model=model)
    if not overlay:
        return False
    scrub_paid_env(env)
    env.update(overlay)
    return True


def model_for(engine, requested=None, env=None):
    """Command-line model for a $0 spawn.

    claude: the explicit pick, else the router-provided ANTHROPIC_MODEL, else
    "auto" (the router's own pick). opencode/aider need an OpenAI-style
    ``openai/<id>`` — "auto" means the router chooses the free model.
    """
    engine = str(engine or "").strip().lower()
    requested = str(requested or "").strip()
    if engine == "claude":
        if requested:
            return requested
        env_model = str((env or {}).get("ANTHROPIC_MODEL") or "").strip()
        return env_model or "auto"
    if engine in ("opencode", "aider"):
        target = requested or _default_router_model() or "auto"
        return target if "/" in target else f"openai/{target}"
    return requested


def _default_router_model():
    state = router_state()
    return _state_value(state, "default_model", "model")


def readiness(engine):
    """(ready, reason) for the free runtime on ``engine`` — cheap, spawn-safe."""
    engine = str(engine or "").strip().lower()
    if engine not in FREE_RUNTIME_ENGINES:
        return False, f"the free runtime does not support the {engine or '?'} engine"
    state = router_state()
    if not state:
        return False, "the free router is not installed yet"
    if not unified_key(state):
        return False, "the free router has no inference key yet"
    if engine == "claude" and _delegate_spawn_env(None):
        return True, ""
    if not router_listening(state):
        return False, "the free router is not running"
    return True, ""


def unavailable_result(engine, reason=""):
    """Spawn-result shape for a refused $0 request — never a silent fallback."""
    engine = str(engine or "").strip().lower()
    ready, why = readiness(engine)
    detail = reason or why or "the free router is not ready"
    return {
        "ok": False,
        "error": (
            "This session was asked to run free ($0), but "
            + detail
            + ". Start the free router in Settings > Free models, or send again without runtime=free."
        ),
        "code": "free_runtime_unavailable",
        "engine": engine,
        "runtime": FREE_RUNTIME,
    }


def normalize_runtime(value):
    """The payload runtime token: "free", "", or an unknown value to reject."""
    runtime = str(value or "").strip().lower()
    return runtime


def session_runtime(session_id):
    """The recorded runtime for a known session id ("free" or "").

    Spawn markers are the durable, post-restart source — a session resumed
    days later still reports the runtime it was born with.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    try:
        markers = _core._load_spawn_markers()
    except Exception:
        markers = {}
    marker = markers.get(sid) or {}
    return str(marker.get("runtime") or "").strip()[:_RUNTIME_VALUE_MAX]


def mark_spawned(session_id, *, pid=None):
    """Stamp runtime=free everywhere a spawn row is read from.

    Order matters: the marker file is the archive/transcript overlay, the
    registry tag covers /api/sessions/spawned rows, and the in-memory entry
    covers the dashboard's live list before the next poll.
    """
    sid = str(session_id or "").strip()
    try:
        if sid:
            # Merge, not replace: the session-start hook writes caller/parent
            # into the same file and must not lose this stamp (or vice versa).
            _core._merge_spawn_marker(sid, runtime=FREE_RUNTIME)
    except Exception:
        pass
    try:
        _core._tag_spawn_runtime_in_registry(
            pid=pid, session_id=sid or None, runtime=FREE_RUNTIME,
        )
    except Exception:
        pass


def spawn_log_marker_event(model=""):
    """One stream-json line for the head of a $0 session's spawn log."""
    return {
        "type": "system",
        "subtype": "ccc_runtime",
        "runtime": FREE_RUNTIME,
        "model": str(model or ""),
        "content": "This session runs on a free model ($0) via the CCC free router.",
    }
