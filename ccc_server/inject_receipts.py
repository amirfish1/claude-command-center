# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Durable delivery receipts for queued injects (CCC-27/CCC-28).

`/api/inject-input` recording an "interaction" only proves the user clicked
Send — it says nothing about whether the text ever reached the session. CCC-28
found a composer inject that got accepted into the terminal queue
(`queued=True`) and then never landed: no transcript line, no spawn-log line,
no held/dropped log line either, because the terminal-queue watcher's retry
path re-parks a failed delivery silently on every tick (see the paired log
lines added in ccc_server/pending_inputs.py alongside this module).

A receipt is opened the moment an inject is accepted-but-queued (not on
immediate delivery -- there is nothing to prove there) and closed the moment
the terminal-queue watcher (or any other queued-delivery path) proves the text
actually reached the session. `outstanding()` is what a future triage session
-- or the additive `/api/session/<sid>/inject-receipt` field -- reads instead
of inferring "stuck" from last-interactions-vs-transcript timing by hand.

One receipt per session at a time: the terminal queue drains one head entry
at a time, so a fresh `open_receipt()` for the same sid supersedes whatever
was outstanding (the old text is either still ahead of it in the queue, in
which case it is still covered by watching this sid, or it already landed and
just never got its own `close_receipt()` call for some reason -- either way,
holding more than one stale entry per sid forever is worse than tracking the
newest).

Stdlib-only, no imports from server.py, so it is unit-testable in isolation.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

try:  # POSIX only; the state file is best-effort locked where available.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

_KEEP_LANDED = 20


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


# How long a queued-but-unlanded receipt must sit before it counts as
# "outstanding" for triage purposes -- a receipt that's 2s old is just an
# ordinary queue hop, not a stuck message.
STALE_AFTER_S = _env_float("CCC_INJECT_RECEIPT_STALE_S", 45)


def _default_path():
    base = os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )
    return os.path.join(base, "inject-receipts.json")


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


def open_receipt(sid, inject_id, text, *, source="", now=None, path=None):
    """Record that `text` was accepted-but-queued for `sid`. Idempotent on
    inject_id: re-opening the same inject_id is a no-op (a retried API call
    for the same message must not reset its sent_ts)."""
    now = time.time() if now is None else now
    inject_id = str(inject_id or "")

    def fn(state):
        e = state.get(sid)
        if isinstance(e, dict) and e.get("inject_id") == inject_id:
            return
        state[sid] = {
            "inject_id": inject_id,
            "text_preview": str(text or "")[:160],
            "source": str(source or ""),
            "sent_ts": now,
        }

    _mutate(path, fn)


def close_receipt(sid, *, inject_id=None, text=None, now=None, path=None):
    """Mark whatever is outstanding for `sid` as landed.

    When `inject_id` or `text` is given, only close a matching receipt --
    guards against a late confirmation for an old inject closing a newer,
    still-genuinely-outstanding one for the same session.
    """
    now = time.time() if now is None else now

    def fn(state):
        e = state.get(sid)
        if not isinstance(e, dict):
            return
        if inject_id is not None and e.get("inject_id") != str(inject_id):
            return
        if text is not None and e.get("text_preview") != str(text or "")[:160]:
            return
        state.pop(sid, None)
        landed = state.setdefault("_landed", [])
        landed.append({"sid": sid, "inject_id": e.get("inject_id"),
                        "sent_ts": e.get("sent_ts"), "landed_ts": now})
        state["_landed"] = landed[-_KEEP_LANDED:]

    _mutate(path, fn)


def outstanding(sid, *, now=None, stale_after_s=None, path=None):
    """The receipt still open for `sid` past the staleness floor, or None."""
    now = time.time() if now is None else now
    floor = STALE_AFTER_S if stale_after_s is None else float(stale_after_s)
    e = _load(path or _default_path()).get(sid)
    if not isinstance(e, dict):
        return None
    sent_ts = float(e.get("sent_ts") or 0.0)
    age_s = now - sent_ts
    if age_s < floor:
        return None
    return {
        "inject_id": e.get("inject_id") or "",
        "text_preview": e.get("text_preview") or "",
        "source": e.get("source") or "",
        "sent_ts": sent_ts,
        "age_s": age_s,
    }
