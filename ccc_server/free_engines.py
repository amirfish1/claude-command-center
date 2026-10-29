# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Point CCC's non-Claude engines at a free model router.

Claude Code takes ``ANTHROPIC_BASE_URL``/``ANTHROPIC_AUTH_TOKEN`` per child
process (the managed-router lane owns ``free_router.spawn_env``). The other
engines CCC spawns have their own conventions:

  aider     env only: OPENAI_API_BASE + OPENAI_API_KEY, ``--model openai/<id>``
  opencode  a generated provider config file + OPENCODE_CONFIG per child
  codex     ~/.codex/config.toml ``[model_providers.*]`` with
            ``wire_api = "responses"`` (Codex spawns through the app-server,
            so per-child env is not available — the provider block is
            written once, marker-delimited and idempotent, only after the
            user clicks "Set up Codex")

Router resolution order for a free spawn: explicit spawn payload overrides
(``free_base_url``/``free_api_key``) -> the router the user picked in the
"use your existing router" card -> CCC's managed router state file
(``~/.ccc/free-router.json``, written by the free_router lane). Nothing ever
silently falls back to a paid model: no router means no env, and the child
fails visibly.

Never writes ``~/.claude/settings.json``. Never logs or returns key
material: previews carry ``{env:CCC_FREE_ROUTER_KEY}`` placeholders only.

Stdlib-only. Names still living in server.py are reached via ``_core``.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

from ccc_server import core as _core
from ccc_server import router_detect as _router_detect

# Engines this module can configure for a free base URL. Codex is
# config-file only (see module docstring); the rest are env-injectable.
ENV_ENGINES = ("opencode", "aider")
FILE_ENGINES = ("codex",)
ALL_ENGINES = ("claude",) + ENV_ENGINES + FILE_ENGINES

FREE_ROUTER_STATE_FILE = Path.home() / ".ccc" / "free-router.json"
CCC_STATE_FILE_HINT = "~/.ccc/free-router.json"

CODEX_PROVIDER_ID = "ccc_free"
CODEX_PROFILE = "ccc-free"
CODEX_MARKER_START = "# ccc-free:start"
CODEX_MARKER_END = "# ccc-free:end"
CODEX_KEY_ENV = "CCC_FREE_ROUTER_KEY"

OPENCODE_PROVIDER_ID = "cccfree"


# ---------------------------------------------------------------------------
# Router resolution
# ---------------------------------------------------------------------------

def _read_ccc_router_state():
    """(base_url, api_key) from the managed router's state file, or None."""
    try:
        data = json.loads(FREE_ROUTER_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    key = data.get("unified_key") or data.get("api_key") or data.get("key")
    if isinstance(key, dict):  # tolerate {"unified_key": {"value": ...}}
        key = key.get("value") or key.get("key")
    if not port:
        base = str(data.get("base_url") or "").strip() or None
        if not base:
            return None
        return {"base_url": base.rstrip("/"), "api_key": key or "", "source": "ccc"}
    return {
        "base_url": f"http://127.0.0.1:{port}",
        "api_key": key or "",
        "source": "ccc",
    }


def _chosen_external_router():
    """The detection record for the router the user picked, or None."""
    try:
        rec = _router_detect.chosen_router()
    except Exception:
        rec = None
    if not rec:
        return None
    key = ""
    try:
        data = json.loads(_router_detect._choice_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            key = str(data.get("key") or "")
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "base_url": (rec.get("base_url") or "").rstrip("/"),
        "openai_base_url": rec.get("openai_base_url"),
        "anthropic_base_url": rec.get("anthropic_base_url"),
        "api_key": key,
        "keyless": bool(rec.get("keyless")),
        "name": rec.get("name") or "your router",
        "source": rec.get("id") or "detected",
    }


def resolve_free_router(explicit_base_url=None, explicit_api_key=None):
    """Best router for a free run. Returns a dict or None when nothing is
    configured — callers treat None as "leave the engine on its normal
    provider" (never a silent paid fallback masquerading as free)."""
    if explicit_base_url:
        base = str(explicit_base_url).strip().rstrip("/")
        return {
            "base_url": base,
            "openai_base_url": base + "/v1" if not base.endswith("/v1") else base,
            "anthropic_base_url": base[:-3] if base.endswith("/v1") else base,
            "api_key": explicit_api_key or "",
            "keyless": not explicit_api_key,
            "name": "custom router",
            "source": "explicit",
        }
    ext = _chosen_external_router()
    if ext and ext.get("base_url"):
        return ext
    return _read_ccc_router_state()


def _openai_v1(router):
    """OpenAI-compatible base for a router record (adds /v1 when absent)."""
    base = (router.get("openai_base_url") or router.get("base_url") or "").rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else base + "/v1"


# ---------------------------------------------------------------------------
# OpenCode — generated provider config + OPENCODE_CONFIG per child
# ---------------------------------------------------------------------------

def _opencode_config_path():
    return _core.COMMAND_CENTER_STATE_DIR / "free-router" / "opencode.json"


def _router_model_ids(router, api_key="", limit=60):
    """Best-effort model roster from GET <base>/v1/models. [] on any error."""
    base = _openai_v1(router)
    if not base:
        return []
    req = urllib.request.Request(
        f"{base}/models",
        headers={"Accept": "application/json",
                 "Authorization": f"Bearer {api_key or 'free'}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read(512_000).decode("utf-8", "replace"))
    except Exception:
        return []
    rows = data.get("data") if isinstance(data, dict) else None
    ids = [str(m.get("id")) for m in (rows or []) if isinstance(m, dict) and m.get("id")]
    return ids[:limit]


def write_opencode_config(router, model=None, api_key=""):
    """Write CCC's opencode provider config; returns (path, model_ref)."""
    base = _openai_v1(router)
    ids = _router_model_ids(router, api_key=api_key)
    for extra in ("auto", model):
        if extra and extra not in ids:
            ids.insert(0, extra)
    if not ids:
        ids = [model or "auto"]
    chosen = model or ids[0]
    models_map = {mid: {"name": mid} for mid in ids}
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{OPENCODE_PROVIDER_ID}/{chosen}",
        "provider": {
            OPENCODE_PROVIDER_ID: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "CCC Free Router",
                "options": {
                    "baseURL": base,
                    "apiKey": "{env:CCC_FREE_ROUTER_KEY}",
                },
                "models": models_map,
            },
        },
    }
    path = _opencode_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path, f"{OPENCODE_PROVIDER_ID}/{chosen}"


# ---------------------------------------------------------------------------
# Codex — ~/.codex/config.toml provider block (marker-delimited merge)
# ---------------------------------------------------------------------------

def _codex_config_path(home=None):
    return (Path(home) if home else Path.home()) / ".codex" / "config.toml"


def codex_provider_block(base_url, model=None):
    """TOML text for the CCC free provider + a profile that selects it."""
    v1 = _openai_v1({"base_url": base_url})
    lines = [
        CODEX_MARKER_START,
        "[model_providers.ccc_free]",
        'name = "CCC Free Router"',
        f"base_url = {json.dumps(v1)}",
        'wire_api = "responses"',
        f'env_key = "{CODEX_KEY_ENV}"',
        "requires_openai_auth = false",
        "",
        f"[profiles.{CODEX_PROFILE}]",
        f"model = {json.dumps(model or 'auto')}",
        f'model_provider = "{CODEX_PROVIDER_ID}"',
        CODEX_MARKER_END,
        "",
    ]
    return "\n".join(lines)


def _strip_marker_block(text):
    pattern = re.compile(
        r"(?ms)^" + re.escape(CODEX_MARKER_START) + r".*?^" + re.escape(CODEX_MARKER_END) + r"\n?"
    )
    return pattern.sub("", text)


def install_codex_provider(base_url, model=None, home=None):
    """Idempotently merge the free provider into ~/.codex/config.toml.

    Marker-delimited: existing user config is preserved byte-for-byte, and a
    previous CCC block is replaced wholesale. One timestamped backup is
    written next to the file before the first merge.
    """
    path = _codex_config_path(home)
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return {"ok": False, "error": f"cannot read {path}"}
    cleaned = _strip_marker_block(existing).rstrip()
    new_text = (cleaned + "\n\n" if cleaned else "") + codex_provider_block(base_url, model)
    if new_text == existing:
        return {"ok": True, "path": str(path), "changed": False}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            backup = path.with_name(f"config.toml.ccc-backup-{time.strftime('%Y%m%dT%H%M%S')}")
            backup.write_text(existing, encoding="utf-8")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "path": str(path), "changed": True}


# ---------------------------------------------------------------------------
# Spawn-time env (the part /api/sessions/spawn merges per child)
# ---------------------------------------------------------------------------

def spawn_env(engine, model=None, *, base_url=None, api_key=None, router=None):
    """(env, model) for a free-runtime spawn of `engine`, or None.

    Returns None when the engine can't take a base URL or no router is
    configured — the caller leaves the spawn on its normal provider.
    """
    engine = (engine or "").strip().lower()
    if engine not in ALL_ENGINES:
        return None
    router = router or resolve_free_router(base_url, api_key)
    if not router:
        return None
    key = api_key if api_key is not None else (router.get("api_key") or "")
    key = key or "free"  # keyless endpoints still want a non-empty Bearer

    if engine == "claude":
        # L01's free_router.spawn_env is authoritative when present; this is
        # the fallback for external routers / not-yet-merged lanes.
        anthropic_base = router.get("anthropic_base_url") or router.get("base_url")
        if not anthropic_base:
            return None
        env = {
            "ANTHROPIC_BASE_URL": anthropic_base.rstrip("/"),
            "ANTHROPIC_AUTH_TOKEN": key,
        }
        if model:
            env["ANTHROPIC_MODEL"] = model
        return {"env": env, "model": model or None}

    if engine == "aider":
        v1 = _openai_v1(router)
        if not v1:
            return None
        return {
            "env": {"OPENAI_API_BASE": v1, "OPENAI_API_KEY": key},
            "model": f"openai/{model or 'auto'}",
        }

    if engine == "opencode":
        if not _openai_v1(router):
            return None
        path, model_ref = write_opencode_config(router, model=model, api_key=key)
        return {
            "env": {"OPENCODE_CONFIG": str(path), CODEX_KEY_ENV: key},
            "model": model_ref,
        }

    if engine == "codex":
        # Per-child env can't reach the app-server; the config.toml provider
        # block + profile is the free path, written by engine_setup() after a
        # user click. Nothing to inject here.
        return None
    return None


def spawn_env_for_payload(engine, model, payload):
    """server.py hook: env+model for payload {"runtime": "free"}."""
    if str((payload or {}).get("runtime") or "").strip().lower() != "free":
        return None
    base = (payload.get("free_base_url") or "").strip() or None
    key = (payload.get("free_api_key") or "").strip() or None
    return spawn_env(
        engine,
        (payload.get("free_model") or model) or None,
        base_url=base,
        api_key=key,
    )


# ---------------------------------------------------------------------------
# API-facing previews + setup (no secrets in previews)
# ---------------------------------------------------------------------------

def engine_config_preview(engine, base_url=None, model=None):
    """What `engine X on the free router` looks like — masked, never writes."""
    engine = (engine or "").strip().lower()
    if engine not in ALL_ENGINES:
        return {"ok": False, "error": f"unsupported engine: {engine}",
                "supported": list(ALL_ENGINES)}
    router = resolve_free_router(base_url)
    if not router:
        return {"ok": False, "error": "no free router configured yet",
                "supported": list(ALL_ENGINES)}
    v1 = _openai_v1(router)
    name = router.get("name") or "the free router"

    if engine == "claude":
        return {
            "ok": True, "engine": engine, "method": "env",
            "env_preview": {
                "ANTHROPIC_BASE_URL": router.get("anthropic_base_url") or router.get("base_url"),
                "ANTHROPIC_AUTH_TOKEN": "{env:CCC_FREE_ROUTER_KEY}",
            },
            "instructions": ["CCC injects this per session — nothing to install."],
        }
    if engine == "aider":
        return {
            "ok": True, "engine": engine, "method": "env",
            "env_preview": {
                "OPENAI_API_BASE": v1,
                "OPENAI_API_KEY": "{env:CCC_FREE_ROUTER_KEY}",
            },
            "instructions": [
                "CCC injects this per session — nothing to install.",
                f"Terminal: OPENAI_API_BASE={v1} OPENAI_API_KEY=… aider --model openai/auto",
            ],
        }
    if engine == "opencode":
        path = _opencode_config_path()
        return {
            "ok": True, "engine": engine, "method": "env+config",
            "file": str(path),
            "env_preview": {"OPENCODE_CONFIG": str(path), CODEX_KEY_ENV: "{set per spawn}"},
            "instructions": [
                "CCC writes a provider config for OpenCode and points each free session at it.",
                "Your own opencode.json is untouched.",
            ],
        }
    # codex
    return {
        "ok": True, "engine": engine, "method": "config-file",
        "file": str(_codex_config_path()),
        "snippet": codex_provider_block(v1, model),
        "instructions": [
            "Codex reads one file: ~/.codex/config.toml. CCC adds a marked provider block — nothing else in the file changes.",
            f'Then run: codex --profile {CODEX_PROFILE} "your task"',
            f"The profile needs ${CODEX_KEY_ENV} in the environment (CCC sets it for sessions it spawns; export it for terminal runs).",
        ],
    }


def engine_setup(engine, base_url=None, api_key=None, model=None):
    """Apply free-router config for `engine`. Only codex writes a user file."""
    engine = (engine or "").strip().lower()
    router = resolve_free_router(base_url, api_key)
    if not router:
        return {"ok": False, "error": "no free router configured yet"}
    key = api_key if api_key is not None else (router.get("api_key") or "")
    if engine == "codex":
        res = install_codex_provider(_openai_v1(router), model=model)
        if res.get("ok"):
            res.update({
                "engine": engine,
                "profile": CODEX_PROFILE,
                "run": f'codex --profile {CODEX_PROFILE} "your task"',
                "key_export": f"{CODEX_KEY_ENV}=<your router key>",
            })
        return res
    if engine == "opencode":
        path, model_ref = write_opencode_config(router, model=model, api_key=key)
        return {"ok": True, "engine": engine, "path": str(path), "model": model_ref}
    if engine in ("aider", "claude"):
        return {"ok": True, "engine": engine, "method": "env",
                "note": "Nothing to install — CCC injects the env per session."}
    return {"ok": False, "error": f"unsupported engine: {engine}",
            "supported": list(ALL_ENGINES)}
