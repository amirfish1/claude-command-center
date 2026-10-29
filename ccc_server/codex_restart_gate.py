"""Admission freeze that lets a worker restart spare live Codex turns (CCC-250).

CCC's stdio Codex app-server is a child of the worker, and the worker's exit
SIGTERMs it, which aborts every turn in flight. turn/start returns at once,
so those turns hold no ledger job and the ledger drain cannot see them.

Contract: a caller about to restart the worker calls acquire(). Under the
app-server condition it sets the freeze, so from then on the transport send
path refuses every method outside ALLOWED_WHILE_FROZEN. Only then does it
ask for proof that nothing can run: server._codex_app_server_restart_barrier()
checks CCC's own turn bookkeeping, unanswered mutating requests, and the
app-server's native `thread/loaded/list`. Codex only runs a turn on its own
(a server-side queued message or goal continuation) on a loaded thread, and
loading a thread needs a client request the freeze refuses, so an empty
loaded list held under the freeze means nothing can start before the kill.
Anything else -- a loaded thread, an error, a timeout, an untracked child --
releases the freeze and refuses the restart. Callers re-run acquire() right
before the kill, so a freeze that lapsed in between is re-proved, not
trusted. release() lifts it; the TTL only bounds how long an abandoned
restart blocks Codex, and a lapsed freeze is never treated as proof.

The freeze is a lease with one owner. A fresh acquire() mints a token and
is refused while another owner's lease is live, so a second, overlapping
restart can neither replace nor lift the first one's freeze; renewing
(the final pre-kill check) and release() need the owner's token. A lease
that lapsed is gone for good: its token renews nothing, so a restart that
lost its lease has no proof left to kill on and must start over.

The final pre-kill check commits the lease (acquire(commit=True)): the
proof and the commit happen under one lock, and a committed lease never
expires and cannot be released. Only the worker's exit ends it. Expiry
used to reopen admissions while the kill the proof had authorized was
still on its way (a wall-clock step, a stalled systemctl), so a turn could
start and be killed. Nothing a caller does after the proof can outlive a
lease that does not lapse. A restart whose kill then failed leaves Codex
frozen until the next restart. Any later acquire() takes over a committed
lease without ever lifting it, and its proof passes because nothing could
load under the freeze. Times are monotonic, so a clock step changes nothing.

Stdlib-only and free of server imports so the worker can freeze before it
ever loaded the Codex stack (no transport then means no turns to protect).
"""

import secrets
import sys
import threading
import time

# Everything else is refused while frozen, reads included: no method may
# load a thread. Cancellation keeps working; the rest are the barrier's own
# query, the liveness probe, and the handshake of a fresh transport.
ALLOWED_WHILE_FROZEN = frozenset({
    "turn/interrupt", "thread/loaded/list", "thread/list", "initialize",
})
DEFAULT_TTL_S = 300.0
MAX_TTL_S = 900.0

_state_lock = threading.Lock()
_frozen_until = 0.0
_frozen_reason = ""
_owner = ""
_committed = False


def _now():
    return time.monotonic()


def frozen(now=None):
    now = _now() if now is None else now
    return _committed or _frozen_until > now


def blocks(method, now=None):
    """True if `method` must not be sent right now. Callers hold the
    app-server condition, which acquire() sets the freeze under."""
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


def _barrier():
    server = sys.modules.get("server")
    if server is None:
        return {"safe": True, "reason": "no_codex_stack", "threads": []}
    return server._codex_app_server_restart_barrier()


def _set_unlocked(until, reason, owner):
    """Caller holds _state_lock."""
    global _frozen_until, _frozen_reason, _owner
    _frozen_until = until
    _frozen_reason = str(reason or "")[:200]
    _owner = owner


def _refused(reason, live_turns=1, threads=()):
    threads = list(threads or [])
    return {"ok": True, "frozen": False,
            "live_turns": max(int(live_turns or 0), len(threads), 1),
            "threads": threads[:20], "reason": reason}


def _take_lease_unlocked(token, ttl_s, reason, now):
    """Caller holds _state_lock. Returns (token, None) when the caller now
    owns a live lease, else (None, refusal) with the state untouched. A
    fresh caller takes over a committed lease (it stays committed): its
    kill already had authority, so no one can be refused for good."""
    live = _committed or _frozen_until > now
    if token:
        if not live or token != _owner:
            return None, _refused("lease_lost")
    elif _committed:
        token = secrets.token_hex(16)
    elif live:
        return None, _refused("restart_in_progress")
    else:
        token = secrets.token_hex(16)
    _set_unlocked(now + ttl_s, reason, token)
    return token, None


def acquire(reason="", ttl_s=DEFAULT_TTL_S, now=None, token=None, commit=False):
    """Freeze native admissions, then prove no Codex turn can run.

    Returns {"ok": True, "frozen": bool, "live_turns": n, "threads": [...],
    "reason": why, "token": t, "remaining_s": s}. frozen=False means: do
    not restart now. Without `token` this takes a new lease and is refused
    while another one is live (reason restart_in_progress). With the token
    of a live lease it renews the TTL and re-runs the whole proof (the
    final pre-kill check); a lapsed or foreign token is refused
    (lease_lost). A refusal never touches another owner's lease.
    commit=True (with a token) commits the lease when the proof holds:
    the reply then has committed=True and the freeze lasts until the
    worker exits.
    """
    try:
        ttl_s = min(max(float(ttl_s), 1.0), MAX_TTL_S)
    except (TypeError, ValueError):
        ttl_s = DEFAULT_TTL_S
    token = str(token or "") or None
    lock = _app_server_lock()
    if lock is None:
        with _state_lock:
            if _app_server_lock() is None:
                now = _now() if now is None else now
                owned, refusal = _take_lease_unlocked(token, ttl_s, reason, now)
                if refusal:
                    return refusal
                return _proved(owned, "no_codex_stack", ttl_s,
                               commit and bool(token))
        lock = _app_server_lock()
    with lock, _state_lock:
        now = _now() if now is None else now
        owned, refusal = _take_lease_unlocked(token, ttl_s, reason, now)
    if refusal:
        return refusal
    try:
        proof = _barrier()
    except Exception as e:
        proof = {"safe": False, "reason": f"barrier_error:{e!r}"[:200], "threads": []}
    if not isinstance(proof, dict) or proof.get("safe") is not True:
        proof = proof if isinstance(proof, dict) else {}
        release(owned)
        return _refused(str(proof.get("reason") or "unknown"),
                        threads=proof.get("threads"))
    with _state_lock:
        remaining = _frozen_until - _now()
        still = _owner == owned and (_committed or remaining > 0)
        if not still:
            # The lease lapsed while the barrier ran: something may have
            # been admitted in that gap, so this proof proves nothing.
            return _refused("freeze_lapsed")
        return _proved(owned, str(proof.get("reason") or "proved"),
                       remaining, commit and bool(token))


def _proved(owned, reason, remaining, commit):
    """Caller holds _state_lock and still owns a live lease."""
    global _committed
    if commit:
        _committed = True
    return {"ok": True, "frozen": True, "live_turns": 0, "threads": [],
            "reason": reason, "token": owned,
            "remaining_s": max(0.0, remaining), "committed": _committed}


def release(token=None):
    """Lift the freeze, but only for the lease's own token: a failed,
    refused or foreign restart can never lift someone else's protection.
    A committed lease is never lifted: the kill it authorized may still be
    on its way."""
    global _frozen_until, _frozen_reason, _owner
    token = str(token or "")
    with _state_lock:
        if not token or token != _owner:
            return {"ok": True, "released": False, "reason": "not_owner"}
        if _committed:
            return {"ok": True, "released": False, "reason": "committed"}
        was = _frozen_until > _now()
        _frozen_until = 0.0
        _frozen_reason = ""
        _owner = ""
    return {"ok": True, "released": was}


def status(now=None):
    now = _now() if now is None else now
    return {"frozen": frozen(now), "remaining_s": max(0.0, _frozen_until - now),
            "reason": _frozen_reason, "owned": bool(_owner) and frozen(now),
            "committed": _committed}
