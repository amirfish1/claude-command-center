# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Queued after-turn inputs held by an explicit Stop (CCC-237).

`ccc send` (mode=send) parks text in the durable queue to run *after* the
current turn. When the user then stops that turn -- the Stop/Esc button
(/api/inject-esc), the `abort` verb, or a steer that is itself a stop -- the
parked text was written for a plan that no longer holds. Delivering it when
the stopped turn ends replayed obsolete instructions as fresh turns (OPS-155).

So an explicit Stop moves the session's queued entries out of the delivery
queue into this store. Nothing here is ever delivered automatically: not when
the stopped turn finishes, and not when a new message is sent. A held entry
leaves only by an explicit release (re-sent through the normal inject path,
so it still waits for any running turn) or cancel.

One record per session: {"revision", "updated_at", "items": [...]}. A session
whose last item is released/cancelled keeps an empty record for a while so
clients that rendered the held block see an empty revision and clear it.

Stdlib-only, no imports from server.py, so it is unit-testable in isolation.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid

try:  # POSIX only; the state file is best-effort locked where available.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

# Empty records linger this long so a rendered held block can be cleared.
_EMPTY_KEEP_S = 6 * 3600

# A steer counts as a Stop when the caller says so (`stop: true`), or when its
# text opens with an all-caps STOP / HALT / ABORT or asks the agent to end its
# turn. Lower-case "stop" is ordinary redirect prose ("stop using X, do Y").
_STOP_WORD_RE = re.compile(r"^\W*(?:STOP|HALT|ABORT)\b")
_END_TURN_RE = re.compile(
    r"\bend\s+(?:your|the)\s+(?:current\s+)?turn\b", re.IGNORECASE,
)


def is_stop_steer_text(text):
    """True when a steer's own text says to stop / end the turn."""
    body = str(text or "").strip()
    return bool(body) and bool(
        _STOP_WORD_RE.match(body) or _END_TURN_RE.search(body)
    )


def _default_path():
    base = os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )
    return os.path.join(base, "stop-held-inputs.json")


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


def _prune(state, now):
    for sid in [k for k, v in state.items()
                if not isinstance(v, dict)
                or (not v.get("items")
                    and now - float(v.get("updated_at") or 0) > _EMPTY_KEEP_S)]:
        state.pop(sid, None)


def _bump(record, now):
    record["revision"] = int(record.get("revision") or 0) + 1
    record["updated_at"] = now


def hold(sid, entries, *, reason="stop", now=None, path=None):
    """Append queued entries for `sid`; returns the stored item dicts.

    `entries` are dicts with at least `text` (the exact queued string,
    attribution header included) and `queue` ("resume" | "terminal").
    """
    now = time.time() if now is None else now
    entries = [e for e in (entries or []) if str((e or {}).get("text") or "").strip()]
    if not sid or not entries:
        return []

    def fn(state):
        _prune(state, now)
        record = state.get(sid) if isinstance(state.get(sid), dict) else {}
        items = list(record.get("items") or [])
        added = []
        for entry in entries:
            item = {
                "id": str(entry.get("id") or uuid.uuid4()),
                "text": str(entry["text"]),
                "queue": str(entry.get("queue") or "resume"),
                "held_at": now,
                "reason": str(reason or "stop"),
            }
            if isinstance(entry.get("attr"), dict):
                item["attr"] = entry["attr"]
            if entry.get("queued_at"):
                item["queued_at"] = float(entry["queued_at"])
            if any(existing.get("id") == item["id"] for existing in items):
                item["id"] = str(uuid.uuid4())
            items.append(item)
            added.append(dict(item))
        record["items"] = items
        _bump(record, now)
        state[sid] = record
        return added

    return _mutate(path, fn)


_READ_MEMO = {}


def _load_cached(path):
    """`_load` memoized by (mtime_ns, size): conversation polls read this on
    every fetch, and the file almost never changes."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (st.st_mtime_ns, st.st_size)
    hit = _READ_MEMO.get(path)
    if hit and hit[0] == key:
        return hit[1]
    state = _load(path)
    _READ_MEMO[path] = (key, state)
    return state


def snapshot(sid, *, path=None):
    """{"revision", "updated_at", "items"} for `sid`, or None when unknown."""
    record = _load_cached(path or _default_path()).get(sid)
    if not isinstance(record, dict):
        return None
    return {
        "revision": int(record.get("revision") or 0),
        "updated_at": float(record.get("updated_at") or 0),
        "items": [dict(i) for i in (record.get("items") or []) if isinstance(i, dict)],
    }


def take(sid, ids=None, *, now=None, path=None):
    """Remove and return held items for `sid` (all of them when `ids` is
    None), in their original order. Unknown ids are ignored."""
    now = time.time() if now is None else now
    wanted = None if ids is None else {str(i) for i in ids}

    def fn(state):
        record = state.get(sid)
        if not isinstance(record, dict):
            return []
        items = list(record.get("items") or [])
        taken = [i for i in items if wanted is None or str(i.get("id")) in wanted]
        if not taken:
            return []
        record["items"] = [i for i in items if i not in taken]
        _bump(record, now)
        return taken

    return _mutate(path, fn)


def restore(sid, items, *, now=None, path=None):
    """Put items that failed to release back at the front, keeping order."""
    now = time.time() if now is None else now
    items = [dict(i) for i in (items or []) if isinstance(i, dict)]
    if not sid or not items:
        return

    def fn(state):
        record = state.get(sid) if isinstance(state.get(sid), dict) else {}
        present = {str(i.get("id")) for i in record.get("items") or []}
        record["items"] = [i for i in items if str(i.get("id")) not in present] + list(
            record.get("items") or []
        )
        _bump(record, now)
        state[sid] = record

    _mutate(path, fn)
