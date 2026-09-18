# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Bounded auto-recovery for live sessions that never consume queued input.

A live `claude -p` child can sit idle with a user's message parked in CCC's
terminal queue and nothing ever delivering it. Recovery means retiring that
child and re-delivering the message through `claude --resume`. Doing that
naively can loop forever (kill -> resume -> stuck again -> kill ...), so every
decision here goes through `decide()`, which is pure and enforces the loop
guards, and every attempt is persisted *before* it is acted on so a crash
mid-recovery cannot be retried for free.

Guards (all must pass for a "recover"):
  * kill switch: CCC_INJECT_AUTORECOVER=0 disables everything
  * the message is held long enough (STUCK_AFTER_S)
  * the child's stdout log has been silent (LOG_SILENT_S) and no tool child is
    running -- a genuinely working turn streams events constantly
  * the child is not brand new (MIN_SPAWN_AGE_S) -- crash-loop guard
  * one recovery per message (text hash), ever
  * at most BUDGET_MAX recoveries per session per BUDGET_WINDOW_S, with
    exponential backoff between them; over budget marks the session
    `inject_stuck` for a human instead of trying again

Stdlib-only, no imports from server.py, so it is unit-testable in isolation.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

try:  # POSIX only; the state file is best-effort locked where available.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

KILL_SWITCH_ENV = "CCC_INJECT_AUTORECOVER"


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


STUCK_AFTER_S = _env_float("CCC_INJECT_RECOVER_AFTER_S", 45)
LOG_SILENT_S = _env_float("CCC_INJECT_RECOVER_LOG_SILENT_S", 120)
MIN_SPAWN_AGE_S = 60.0
BUDGET_MAX = 2
BUDGET_WINDOW_S = 1800.0
BACKOFF_BASE_S = 120.0
_KEEP_HASHES = 20

WAIT = "wait"
RECOVER = "recover"
GIVE_UP = "give_up"
SKIP = "skip"


def enabled():
    return os.environ.get(KILL_SWITCH_ENV, "1").strip() != "0"


def text_hash(text):
    return hashlib.sha256(str(text or "").encode("utf-8", "replace")).hexdigest()[:16]


def decide(entry, *, held_s, log_silent_s, spawn_age_s, tool_child,
           msg_hash, now=None):
    """Return (action, reason). `entry` is this session's persisted state."""
    now = time.time() if now is None else now
    entry = entry or {}
    if not enabled():
        return SKIP, "disabled"
    if entry.get("inject_stuck"):
        return SKIP, "inject_stuck"
    if tool_child:
        return WAIT, "tool_child"
    if held_s < STUCK_AFTER_S:
        return WAIT, "young_hold"
    if log_silent_s is None or log_silent_s < LOG_SILENT_S:
        return WAIT, "log_active"
    if spawn_age_s is not None and spawn_age_s < MIN_SPAWN_AGE_S:
        return WAIT, "young_spawn"
    if msg_hash in (entry.get("hashes") or []):
        return GIVE_UP, "already_recovered"
    recent = sorted(
        t for t in (entry.get("attempts") or []) if now - t < BUDGET_WINDOW_S
    )
    if len(recent) >= BUDGET_MAX:
        return GIVE_UP, "budget"
    if recent and now - recent[-1] < BACKOFF_BASE_S * (2 ** (len(recent) - 1)):
        return WAIT, "backoff"
    return RECOVER, "stuck"


def _default_path():
    base = os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )
    return os.path.join(base, "inject-recovery.json")


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _mutate(path, fn):
    """Apply `fn(state)` under an exclusive lock and persist atomically."""
    path = path or _default_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = open(path + ".lock", "a+")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        state = _load(path)
        result = fn(state)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        return result
    finally:
        lock.close()


def get(sid, path=None):
    return dict(_load(path or _default_path()).get(sid) or {})


def record_attempt(sid, msg_hash, now=None, path=None):
    """Persist a recovery attempt. Call BEFORE acting on it."""
    now = time.time() if now is None else now

    def fn(state):
        e = state.setdefault(sid, {})
        e["attempts"] = [
            t for t in (e.get("attempts") or []) if now - t < BUDGET_WINDOW_S
        ] + [now]
        e["hashes"] = ((e.get("hashes") or []) + [msg_hash])[-_KEEP_HASHES:]

    _mutate(path, fn)


def mark_stuck(sid, reason, now=None, path=None):
    now = time.time() if now is None else now

    def fn(state):
        e = state.setdefault(sid, {})
        e["inject_stuck"] = {"reason": reason, "at": now}

    _mutate(path, fn)


def clear_stuck(sid, path=None):
    """A human restarted the session: give it a fresh budget."""

    def fn(state):
        state.pop(sid, None)

    _mutate(path, fn)
