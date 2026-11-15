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
expires and cannot be released; only recover() (below) lifts it. Expiry
used to reopen admissions while the kill the proof had authorized was
still on its way (a wall-clock step, a stalled systemctl), so a turn could
start and be killed. Nothing a caller does after the proof can outlive a
lease that does not lapse. Any later acquire() takes over a committed
lease without ever lifting it (so a failed kill never strands the normal
idle restart), and its proof passes because nothing could load under the
freeze. Times are monotonic, so a clock step changes nothing.

The fence outlives the worker, because kill authority can too: a queued
`systemctl restart` or a stalled run.sh can still land on the NEXT worker.
So a commit is written to disk (configure_fence(); the worker does it at
boot) before it is granted, listing every committed authority (token,
owner pid). A worker that boots with that file starts committed and only
lifts the fence once every listed authority provably cannot execute:

  - the owner wrote a done marker for its token (done_path()) after its
    kill commands returned. outcome "killed": lifted only by a worker that
    is not the one it killed. "uncertain" (a timeout): its commands
    returned, so only a queued service-manager job can still fire; lifted
    once there is none.
  - or the owner pid is gone, nothing of its process group is left (an
    orphaned systemctl stays in it), and the service manager has no job
    queued for the worker unit (pending_job, default `systemctl --user
    list-jobs`; unknown counts as pending).

Anything unproven fails closed: Codex stays frozen (sends get the queue
fallback) while restarts keep working. Recovery, by case:
  - normal restart: the owner's "killed" marker lifts it in the new worker
    within one recover() tick.
  - kill timed out or failed: "uncertain", lifted once no job is queued.
  - owner crashed before reporting: lifted once its pid and process group
    are gone and no job is queued (a dashboard restart gets there).
  - fence file unreadable (it is written atomically, so only disk damage
    or a hand edit): owner unknown, so it never lifts on its own; an
    operator who has confirmed no restart is running removes the file.
recover() runs these checks; the worker calls it on a timer while committed.

Stdlib-only and free of server imports so the worker can freeze before it
ever loaded the Codex stack (no transport then means no turns to protect).
"""

import json
import os
import secrets
import subprocess
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
_fence_path = None          # persisted fence; None = in-memory only (tests)
_authorities = []           # [{"token", "owner_pid", "owner_pgid", "worker_pid"}]
WORKER_UNIT = "ccc-worker.service"


def _default_pending_job():
    """True when a worker-unit job is queued, or unknown. No systemctl
    (macOS launchd, a plain spawn) has no queue: a returned launchctl or
    kill() cannot fire later."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "list-jobs", "--no-legend"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return False
    except (OSError, subprocess.TimeoutExpired):
        return True
    if proc.returncode != 0:
        return True
    return WORKER_UNIT in (proc.stdout or "")


pending_job = _default_pending_job


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


def acquire(reason="", ttl_s=DEFAULT_TTL_S, now=None, token=None, commit=False,
            owner_pid=None):
    """Freeze native admissions, then prove no Codex turn can run.

    Returns {"ok": True, "frozen": bool, "live_turns": n, "threads": [...],
    "reason": why, "token": t, "remaining_s": s}. frozen=False means: do
    not restart now. Without `token` this takes a new lease and is refused
    while another one is live (reason restart_in_progress). With the token
    of a live lease it renews the TTL and re-runs the whole proof (the
    final pre-kill check); a lapsed or foreign token is refused
    (lease_lost). A refusal never touches another owner's lease.
    commit=True (with a token) commits the lease when the proof holds:
    the reply then has committed=True, and the fence holds until recover()
    proves this authority (owner_pid: the process issuing the kill) ended.
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
                               commit and bool(token), owner_pid)
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
                       remaining, commit and bool(token), owner_pid)


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, ValueError, TypeError):
        return True
    return True


def _group_alive(pgid):
    try:
        os.killpg(int(pgid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, ValueError, TypeError):
        return True
    return True


def configure_fence(path):
    """Persist commits at `path` and adopt any fence a previous worker left
    there. Call before serving any request."""
    global _fence_path, _committed, _owner, _authorities, _frozen_reason
    with _state_lock:
        _fence_path = str(path) if path else None
        if not _fence_path or not os.path.exists(_fence_path):
            return status()
        try:
            with open(_fence_path, encoding="utf-8") as fh:
                data = json.load(fh)
            auths = [a for a in data.get("authorities") or [] if isinstance(a, dict)]
            if not auths:
                raise ValueError("no authorities")
        except (OSError, ValueError, AttributeError):
            # Unreadable: who holds kill authority is unknown. Fail closed;
            # a placeholder no done marker can match keeps the fence until
            # the next restart takes it over.
            auths = [{"token": "unreadable", "owner_pid": None,
                      "owner_pgid": None, "worker_pid": None}]
        _authorities = auths
        _committed = True
        _owner = ""
        _frozen_reason = "inherited restart fence"
    return status()


def done_path(token, fence_path=None):
    base = fence_path or _fence_path
    return f"{base}.done.{token}" if base and token else None


def _write_fence_unlocked():
    """Caller holds _state_lock. Raises when the fence cannot be persisted."""
    if not _fence_path:
        return
    tmp = f"{_fence_path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"authorities": _authorities}, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, _fence_path)


def _authority_ended(auth):
    path = done_path(auth.get("token"))
    outcome = None
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                outcome = (fh.read().strip().split() or [""])[0]
        except OSError:
            outcome = None
    if outcome == "killed":
        # The worker it killed is on its way out; never lift there.
        return auth.get("worker_pid") != os.getpid()
    if outcome != "uncertain":
        # No word from the owner: it must be gone, with nothing of its
        # process group left to run a kill it started.
        pid, pgid = auth.get("owner_pid"), auth.get("owner_pgid")
        if pid is None or _pid_alive(pid):
            return False
        if pgid is not None and _group_alive(pgid):
            return False
    try:
        return not pending_job()
    except Exception:
        return False


def recover():
    """Lift a committed fence once every authority it lists has ended.
    Returns True when it lifted."""
    global _frozen_until, _frozen_reason, _owner, _committed, _authorities
    with _state_lock:
        if not _committed:
            return False
        auths = list(_authorities)
    if not auths or not all(_authority_ended(a) for a in auths):
        return False
    with _state_lock:
        if not _committed or _authorities != auths:
            return False        # a new authority committed meanwhile
        if _fence_path:
            try:
                os.unlink(_fence_path)
            except FileNotFoundError:
                pass
            except OSError:
                return False
        for a in auths:
            path = done_path(a.get("token"))
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        _frozen_until = 0.0
        _frozen_reason = ""
        _owner = ""
        _committed = False
        _authorities = []
    return True


def _proved(owned, reason, remaining, commit, owner_pid=None):
    """Caller holds _state_lock and still owns a live lease. A commit is
    granted only once it is persisted."""
    global _committed, _authorities
    if commit:
        auth = {"token": owned, "owner_pid": owner_pid,
                "owner_pgid": None, "worker_pid": os.getpid()}
        if owner_pid is not None:
            try:
                auth["owner_pgid"] = os.getpgid(int(owner_pid))
            except (OSError, ValueError, TypeError):
                pass
        before, was = list(_authorities), _committed
        _authorities = [a for a in _authorities if a.get("token") != owned] + [auth]
        _committed = True
        try:
            _write_fence_unlocked()
        except OSError:
            _authorities, _committed = before, was
            return dict(_refused("fence_unpersisted"), token=owned)
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
            "committed": _committed, "authorities": len(_authorities)}
