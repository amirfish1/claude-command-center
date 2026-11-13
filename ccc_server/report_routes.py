# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Rebindable return addresses for spawned sessions (CCC-1202).

A `report_to` spawn used to bake the dispatcher's session id straight into
the child's prompt footer. When the dispatcher is replaced mid-run (context
limit, the user moves to a fresh session), the only way to redirect the child
was to inject a message into it, which a one-shot worker reads only after it
has already reported to the stale address.

Instead each report_to spawn now gets a route id (`rr_<hex>`). The footer
tells the child to address its report to that route id, and CCC resolves it
to the route's *current* report_to at delivery time. `rebind()` moves one
route, or every route pointing at a given dispatcher, to a new session.

Children spawned before this existed carry the dispatcher's sid directly;
that address still delivers, it just can't be rebound.

Stdlib-only, no imports from server.py, so it is unit-testable in isolation.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

from ccc_server import test_isolation_active

ROUTE_PREFIX = "rr_"
MAX_ROUTES = 5000
ROUTE_TTL_S = 30 * 24 * 3600


def _default_path():
    if test_isolation_active():
        return os.path.join(
            tempfile.gettempdir(), f"ccc-test-report-routes-{os.getpid()}.json"
        )
    base = os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )
    return os.path.join(base, "report-routes.json")


def is_route_id(value):
    v = str(value or "").strip()
    return v.startswith(ROUTE_PREFIX) and 8 < len(v) <= 64 and all(
        ch.isalnum() or ch == "_" for ch in v
    )


def new_route_id():
    return ROUTE_PREFIX + uuid.uuid4().hex[:24]


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _prune(state, now):
    for rid in [r for r, e in state.items()
                if now - float((e or {}).get("updated_at") or 0) > ROUTE_TTL_S]:
        state.pop(rid, None)
    if len(state) > MAX_ROUTES:
        oldest = sorted(state, key=lambda r: float(state[r].get("updated_at") or 0))
        for rid in oldest[: len(state) - MAX_ROUTES]:
            state.pop(rid, None)


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


def create(report_to, path=None, now=None):
    """Register a new route pointing at `report_to`; returns its id."""
    now = time.time() if now is None else now
    rid = new_route_id()

    def fn(state):
        _prune(state, now)
        state[rid] = {
            "report_to": report_to,
            "original_report_to": report_to,
            "child_session_id": "",
            "created_at": now,
            "updated_at": now,
        }
        return rid

    return _mutate(path, fn)


def set_child(route_id, child_session_id, path=None):
    """Record which spawned session owns `route_id` (for rebind-by-child)."""
    if not route_id or not child_session_id:
        return False

    def fn(state):
        entry = state.get(route_id)
        if not entry:
            return False
        entry["child_session_id"] = child_session_id
        return True

    return _mutate(path, fn)


def resolve(address, path=None):
    """Return the current report_to for a route id; other values pass through.

    An unknown/expired route id also passes through unchanged, so the caller
    reports "unknown session" instead of silently delivering somewhere else.
    """
    if not is_route_id(address):
        return address
    entry = _load(path or _default_path()).get(str(address).strip())
    return (entry or {}).get("report_to") or address


def get(route_id, path=None):
    entry = _load(path or _default_path()).get(str(route_id or "").strip())
    return dict(entry, route_id=route_id) if entry else None


def list_routes(report_to=None, child_session_id=None, path=None):
    out = []
    for rid, entry in _load(path or _default_path()).items():
        if report_to and entry.get("report_to") != report_to:
            continue
        if child_session_id and entry.get("child_session_id") != child_session_id:
            continue
        out.append(dict(entry, route_id=rid))
    out.sort(key=lambda e: float(e.get("created_at") or 0))
    return out


def rebind(new_report_to, route_id=None, child_session_id=None,
           from_report_to=None, path=None, now=None):
    """Point matching routes at `new_report_to`; returns the rebound route ids.

    Selectors combine with AND; at least one is required so a bare call can
    never repoint every route in the store.
    """
    if not (route_id or child_session_id or from_report_to):
        raise ValueError("rebind needs route_id, child_session_id or from_report_to")
    now = time.time() if now is None else now

    def fn(state):
        moved = []
        for rid, entry in state.items():
            if route_id and rid != route_id:
                continue
            if child_session_id and entry.get("child_session_id") != child_session_id:
                continue
            if from_report_to and entry.get("report_to") != from_report_to:
                continue
            if entry.get("report_to") == new_report_to:
                continue
            entry["report_to"] = new_report_to
            entry["updated_at"] = now
            entry["rebound_at"] = now
            moved.append(rid)
        return moved

    return _mutate(path, fn)
