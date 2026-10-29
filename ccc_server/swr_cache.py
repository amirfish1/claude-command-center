"""Stale-while-revalidate cache for polled status/aggregate GET endpoints.

Polled endpoints (vercel-deploy, issues/all, savings, headroom, ...) used to
recompute on the request thread whenever their own TTL lapsed, so a saturated
host stretched every poll (and every other request via the GIL). Here a hit
inside ``fresh_s`` returns instantly; a hit older than that still returns
instantly but kicks one single-flight background refresh; only a cold key (or
one older than ``max_stale_s``) computes inline.
"""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_ENTRIES = {}      # key -> {"ts": float, "value": any}
_INFLIGHT = set()  # keys with a refresh running


def _ok(value):
    """Only cache successful payloads: (payload, status) or bare payload."""
    status = 200
    payload = value
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        payload, status = value
    if status >= 400:
        return False
    return not (isinstance(payload, dict) and payload.get("ok") is False)


def _refresh(key, compute):
    try:
        value = compute()
        if _ok(value):
            with _LOCK:
                _ENTRIES[key] = {"ts": time.time(), "value": value}
    except Exception:
        pass
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)


def cached(key, compute, *, fresh_s, max_stale_s=600.0, bypass=False):
    """Return ``compute()`` through the SWR cache. ``bypass`` forces inline."""
    now = time.time()
    if not bypass:
        with _LOCK:
            entry = _ENTRIES.get(key)
            if entry:
                age = now - entry["ts"]
                if age < fresh_s:
                    return entry["value"]
                if age < max_stale_s:
                    if key not in _INFLIGHT:
                        _INFLIGHT.add(key)
                        threading.Thread(
                            target=_refresh, args=(key, compute),
                            name="swr-refresh", daemon=True,
                        ).start()
                    return entry["value"]
    value = compute()
    if _ok(value):
        with _LOCK:
            _ENTRIES[key] = {"ts": time.time(), "value": value}
    return value


def clear():
    with _LOCK:
        _ENTRIES.clear()
        _INFLIGHT.clear()
