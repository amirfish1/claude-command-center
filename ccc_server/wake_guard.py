# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""RESIL-5: one ledger every automatic session wake goes through.

An automatic wake is anything CCC does to a session nobody asked it to touch
right now: the approved limit-reset "continue" (free_failover), stuck-inject
recovery (pending_inputs), and any future dead-session auto-restart. Each one
is judged on its own, so a session CCC misjudges as dead can be woken over and
over, burning tokens. This module is the shared brake.

Rules, checked by `request()` before the caller acts:
  * kill switch: CCC_AUTO_WAKE=0 refuses every automatic wake
  * at most MAX_WAKES per session per WINDOW_S (24h), across all sources
  * a second wake needs proof the previous one worked: the transcript grew
    since that wake AND its last turn is closed (not left mid-tool / awaiting
    the model). No proof -> refuse
  * a refusal for cap/no-progress escalates to the owner once (CCC notify,
    kind needs_input) and blocks further wakes until a human clears the
    session (`clear()` -- e.g. a manual Force restart)

Allowed wakes are persisted BEFORE the caller acts, so a crash mid-wake is
still counted. Stdlib-only apart from the lazy notify/restart_recovery
imports, so it is unit-testable in isolation.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

try:  # POSIX only; best-effort lock elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

KILL_SWITCH_ENV = "CCC_AUTO_WAKE"
MAX_WAKES = 2
WINDOW_S = 24 * 3600.0
TAIL_BYTES = 256 * 1024

ALLOW = "allow"
DENY = "deny"


def enabled():
    return os.environ.get(KILL_SWITCH_ENV, "1").strip() != "0"


def _default_path():
    base = os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )
    return os.path.join(base, "session-wakes.json")


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _mutate(path, fn):
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


def transcript_mark(transcript_path):
    """(size, turn_closed) for a transcript; (None, None) when unreadable.
    turn_closed uses restart_recovery.open_turn, which treats non-Claude
    record shapes as closed -- for those engines growth alone is the proof."""
    if not transcript_path:
        return None, None
    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        return None, None
    try:
        from ccc_server import restart_recovery as rr
        records = list(rr._records(rr._read_tail(transcript_path, size)))
        closed = rr.open_turn(records) is None
    except Exception:
        closed = None
    return size, closed


def progressed(last_wake, size, closed):
    """Did the session make progress since `last_wake`? Unknown -> False."""
    before = last_wake.get("size")
    if size is None or before is None:
        return False
    return size > before and closed is not False


def decide(entry, *, size, closed, now):
    """Pure: (action, reason) for one wake request given the session's entry."""
    entry = entry or {}
    if not enabled():
        return DENY, "disabled"
    if entry.get("escalated"):
        return DENY, "escalated"
    recent = [w for w in entry.get("wakes") or [] if now - float(w.get("at") or 0) < WINDOW_S]
    if len(recent) >= MAX_WAKES:
        return DENY, "cap"
    if recent and not progressed(recent[-1], size, closed):
        return DENY, "no_progress"
    return ALLOW, "ok"


def request(sid, *, source, incident="", transcript_path=None, now=None, path=None, notify=None):
    """Ask to wake `sid`. Returns (allowed, reason). On allow, the wake is
    already recorded; on a cap/no-progress refusal the owner is told once."""
    now = time.time() if now is None else float(now)
    size, closed = transcript_mark(transcript_path)
    sid = str(sid or "")

    def fn(state):
        entry = state.setdefault(sid, {})
        action, reason = decide(entry, size=size, closed=closed, now=now)
        entry["wakes"] = [w for w in entry.get("wakes") or [] if now - float(w.get("at") or 0) < WINDOW_S]
        newly_escalated = False
        if action == ALLOW:
            entry["wakes"].append({"at": now, "source": source, "incident": str(incident or ""), "size": size})
        elif reason in ("cap", "no_progress"):
            entry["escalated"] = {"at": now, "reason": reason, "source": source, "incident": str(incident or "")}
            newly_escalated = True
        if not entry.get("wakes") and not entry.get("escalated"):
            state.pop(sid, None)
        return action, reason, newly_escalated, len(entry.get("wakes") or [])

    action, reason, newly_escalated, count = _mutate(path, fn)
    if newly_escalated:
        _escalate(sid, source, reason, count, notify)
    return action == ALLOW, reason


def _escalate(sid, source, reason, count, notify=None):
    why = ("the last automatic wake made no progress" if reason == "no_progress"
           else f"it was already woken {count} times in 24h")
    data = {
        "kind": "needs_input",
        "title": f"Auto-wake stopped for session {sid[:8]}",
        "body": f"CCC refused another automatic wake ({source}): {why}. "
                "Look at the session and restart it by hand if it should keep going.",
        "url": f"/?session={sid}",
    }
    try:
        if notify is None:
            from ccc_server import notify as _notify
            notify = _notify.post
        notify(data)
    except Exception:
        pass


def get(sid, path=None):
    return dict(_load(path or _default_path()).get(str(sid or "")) or {})


def clear(sid, path=None):
    """A human acted on the session: forget its wakes and any escalation."""
    _mutate(path, lambda state: state.pop(str(sid or ""), None))
