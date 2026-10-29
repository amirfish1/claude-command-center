"""Admission freeze that lets a worker restart spare live Codex turns (CCC-250).

CCC's stdio Codex app-server is a child of the worker, and the worker's exit
SIGTERMs it, which aborts every turn in flight. turn/start returns at once,
so those turns hold no ledger job and the ledger drain cannot see them.

Contract: a caller about to restart the worker calls acquire(). Under the
app-server condition it counts in-flight turns (including a turn/start
already admitted but not yet answered) and, only when there are none, sets
the freeze in that same critical section. The transport send path checks
frozen() under the same condition before writing any mutating request, so
no CCC-issued turn can start between the count and the restart. The freeze
expires after its TTL, and release() lifts it, so an aborted restart never
blocks Codex for long. It dies with the worker process on a real restart.

Not covered: a turn the app-server starts on its own (a server-side queued
message or goal continuation) with no CCC request. The restart path rechecks
just before the kill to narrow that window; it cannot close it.

Stdlib-only and free of server imports so the worker can freeze before it
ever loaded the Codex stack (no transport then means no turns to protect).
"""

import sys
import threading
import time

# Cancellation must keep working while frozen.
ALLOWED_WHILE_FROZEN = frozenset({"turn/interrupt"})
DEFAULT_TTL_S = 300.0
MAX_TTL_S = 900.0

_state_lock = threading.Lock()
_frozen_until = 0.0
_frozen_reason = ""


def frozen(now=None):
    now = time.time() if now is None else now
    return _frozen_until > now


def blocks(method, now=None):
    """True if `method` must not be sent right now. Callers hold the
    app-server condition, which is what makes acquire() atomic with it."""
    return method not in ALLOWED_WHILE_FROZEN and frozen(now)


def refusal():
    return {
        "ok": False,
        "code": "codex_restart_pending",
        "fallback": "queue",
        "error": "The CCC worker is about to restart; Codex will take this "
                 "once it is back.",
    }


def _app_server_lock():
    server = sys.modules.get("server")
    return getattr(server, "_CODEX_APP_SERVER_LOCK", None) if server else None


def live_turn_threads(now=None):
    server = sys.modules.get("server")
    if server is None:
        return []
    return list(server._codex_app_server_live_turns(now=now))


def acquire(reason="", ttl_s=DEFAULT_TTL_S, now=None):
    """Freeze native admissions if no Codex turn is in flight.

    Returns {"ok": True, "frozen": bool, "live_turns": n, "threads": [...]}.
    frozen=False with live_turns>0 means: do not restart now.
    """
    global _frozen_until, _frozen_reason
    try:
        ttl_s = min(max(float(ttl_s), 1.0), MAX_TTL_S)
    except (TypeError, ValueError):
        ttl_s = DEFAULT_TTL_S
    lock = _app_server_lock()
    if lock is None:
        with _state_lock:
            if _app_server_lock() is None:
                now = time.time() if now is None else now
                _frozen_until = now + ttl_s
                _frozen_reason = str(reason or "")[:200]
                return {"ok": True, "frozen": True, "live_turns": 0, "threads": []}
        lock = _app_server_lock()
    with lock:
        now = time.time() if now is None else now
        threads = live_turn_threads(now=now)
        if threads:
            return {"ok": True, "frozen": False, "live_turns": len(threads),
                    "threads": threads[:20]}
        with _state_lock:
            _frozen_until = now + ttl_s
            _frozen_reason = str(reason or "")[:200]
        return {"ok": True, "frozen": True, "live_turns": 0, "threads": []}


def release():
    global _frozen_until, _frozen_reason
    with _state_lock:
        was = _frozen_until > time.time()
        _frozen_until = 0.0
        _frozen_reason = ""
    return {"ok": True, "released": was}


def status(now=None):
    now = time.time() if now is None else now
    return {"frozen": frozen(now), "remaining_s": max(0.0, _frozen_until - now),
            "reason": _frozen_reason}
