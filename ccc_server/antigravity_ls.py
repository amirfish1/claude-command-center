# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Deliver messages into a LIVE Antigravity CLI (agy) session.

Every running `agy` process serves a Connect RPC "language server" on a
random localhost port. `SendUserCascadeMessage` on it runs a turn in that
process exactly as if the user typed it in the TUI; it is the agy analog of
Claude Code's peer socket. Calls need the per-process `x-codeium-csrf-token`.

The address and token exist only in memory. agy exports them only to the
shells its `run_command` tool starts (`ANTIGRAVITY_LS_ADDRESS`,
`ANTIGRAVITY_CSRF_TOKEN`, `ANTIGRAVITY_CONVERSATION_ID`); hooks and MCP
children don't get them, and a preset token is ignored. So the `ccc` CLI
registers a session whenever an agy agent runs any `ccc` command
(`register_from_env`), and the server delivers through that entry
(`send`). A missing, dead, or rejected entry returns None so callers fall
back to the `agy --conversation <id> -p` resume path.

Stdlib-only; no imports from server or ccc_server.core.
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

from ccc_server.paths import COMMAND_CENTER_STATE_DIR

REGISTRY_DIR = COMMAND_CENTER_STATE_DIR / "agy-ls"
_RPC_PREFIX = "/exa.language_server_pb.LanguageServerService/"
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_ADDR_RE = re.compile(r"^(localhost|127\.0\.0\.1):\d{1,5}$")
_TIMEOUT = 10.0

# Steer must land mid-turn; a plain send waits for the current turn to end.
DELIVERY_SEND = "MESSAGE_DELIVERY_STRATEGY_WHEN_IDLE"
DELIVERY_STEER = "MESSAGE_DELIVERY_STRATEGY_NEXT_INVOCATION"

# (conversationId, address) -> cascadeConfig. SendUserCascadeMessage needs the
# session's own config (without it agy accepts the call, then fails the turn
# with "plan model not specified"); a trajectory fetch is large, so do it once.
_config_cache: dict = {}
_config_lock = threading.Lock()


def _entry_path(conversation_id, registry_dir=None):
    return Path(registry_dir or REGISTRY_DIR) / f"{conversation_id}.json"


def register_from_env(env=None, registry_dir=None):
    """Record this agy session's LS endpoint if we run inside an agy shell.

    Cheap and silent: one small file write, no network. Returns the
    conversation id it registered, or None when the env isn't an agy shell.
    """
    env = os.environ if env is None else env
    cid = (env.get("ANTIGRAVITY_CONVERSATION_ID") or "").strip()
    addr = (env.get("ANTIGRAVITY_LS_ADDRESS") or "").strip()
    token = (env.get("ANTIGRAVITY_CSRF_TOKEN") or "").strip()
    if not (_UUID_RE.match(cid) and _ADDR_RE.match(addr) and token):
        return None
    path = _entry_path(cid, registry_dir)
    entry = {"conversationId": cid, "address": addr, "token": token,
             "registeredAt": time.time()}
    try:
        current = json.loads(path.read_text())
        if current.get("address") == addr and current.get("token") == token:
            return cid
    except (OSError, ValueError):
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(entry, fh)
        os.replace(tmp, path)
    except OSError:
        return None
    return cid


def lookup(conversation_id, registry_dir=None):
    if not conversation_id or not _UUID_RE.match(str(conversation_id)):
        return None
    try:
        entry = json.loads(_entry_path(conversation_id, registry_dir).read_text())
    except (OSError, ValueError):
        return None
    if not (_ADDR_RE.match(str(entry.get("address") or "")) and entry.get("token")):
        return None
    return entry


def forget(conversation_id, registry_dir=None):
    try:
        _entry_path(conversation_id, registry_dir).unlink()
    except OSError:
        pass


class _Unreachable(Exception):
    """The registered endpoint is gone or no longer accepts our token."""


def _rpc(entry, method, body):
    req = urllib.request.Request(
        f"http://{entry['address']}{_RPC_PREFIX}{method}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json",
                 "x-codeium-csrf-token": entry["token"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise _Unreachable(f"{method}: HTTP {e.code}")
        raise
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        raise _Unreachable(f"{method}: {e}")
    return json.loads(raw or b"{}")


def _session_config(entry):
    key = (entry["conversationId"], entry["address"])
    with _config_lock:
        if key in _config_cache:
            return _config_cache[key]
    data = _rpc(entry, "GetCascadeTrajectory", {"cascadeId": entry["conversationId"]})
    steps = ((data.get("trajectory") or {}).get("steps")) or []
    config = None
    for step in reversed(steps):
        config = ((step or {}).get("userInput") or {}).get("userConfig")
        if config:
            break
    if config:
        with _config_lock:
            _config_cache[key] = config
    return config


def send(conversation_id, text, mode="send", registry_dir=None):
    """Deliver ``text`` into the live agy session, or return None to fall back.

    The LS answers ``{}`` for an accepted message; agy then runs it (now, or
    after the current turn for a plain send) and persists it in the
    conversation like any typed turn.
    """
    entry = lookup(conversation_id, registry_dir)
    if not entry or not text:
        return None
    try:
        config = _session_config(entry)
        if not config:
            return None
        _rpc(entry, "SendUserCascadeMessage", {
            "cascadeId": entry["conversationId"],
            "cascadeConfig": config,
            "items": [{"text": text}],
            "deliveryStrategy": DELIVERY_STEER if mode == "steer" else DELIVERY_SEND,
        })
    except _Unreachable:
        forget(conversation_id, registry_dir)
        with _config_lock:
            _config_cache.pop((entry["conversationId"], entry["address"]), None)
        return None
    except (urllib.error.HTTPError, ValueError, OSError):
        return None
    return {"ok": True, "via": "antigravity-ls", "address": entry["address"]}
