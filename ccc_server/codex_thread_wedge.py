"""Detect and recover a Codex thread the shared app-server daemon stopped serving.

Incident: the managed daemon (days old) stopped answering every request
naming one thread -- thread/resume, turn/start, even a read-only thread/read
-- while answering every other thread instantly; a fresh app-server read the
same rollout in about a second. CCC's queue pump retried that thread every
~90s for hours. Each retry timed out at 20s and left one more unanswered
request inside the daemon, the session never showed as stuck, and two queued
messages were lost along the way.

Nothing on the client side can unwedge a daemon-internal per-thread hang.
This module:

* counts consecutive app-server timeouts per thread (any successful reply for
  that thread resets it), and marks the thread wedged after
  ``WEDGE_TIMEOUTS`` timeouts spanning at least ``WEDGE_MIN_SPAN_S``;
* while wedged, stops the pump from piling new requests onto the daemon;
* restarts the daemon once it is safe (graceful first, then a forced
  stop + start, because a wedged daemon never finishes draining):
  no CCC-tracked turn is live and no other Codex rollout was written in the
  last ``OTHER_ACTIVITY_WINDOW_S`` -- a restart kills in-flight turns -- and
  no restart happened in the last ``RESTART_COOLDOWN_S``;
* publishes the state to ``codex-thread-wedge.json`` so the dashboard
  process (which does not own the transport) can show why delivery waits.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

WEDGE_TIMEOUTS = 3
WEDGE_MIN_SPAN_S = 60.0
RESTART_COOLDOWN_S = 30 * 60.0
OTHER_ACTIVITY_WINDOW_S = 120.0
RESTART_TIMEOUT_S = 60.0
GRACEFUL_RESTART_TIMEOUT_S = 20.0
GRACEFUL_STOP_S = 10.0
# While wedged, requests naming the thread fail fast instead of hanging in the
# daemon; one real request per interval still goes through as a probe.
PROBE_INTERVAL_S = 300.0
SKIPPED_ERROR = "Codex app-server is not answering this thread; request skipped (wedged)"

_lock = threading.Lock()
_threads: dict = {}  # sid -> {"timeouts": int, "first_at": float, "last_at": float, "method": str}
_last_restart_at = 0.0
_last_restart_result: dict | None = None


def _state_file() -> Path:
    base = os.environ.get("CCC_STATE_DIR") or (Path.home() / ".claude" / "command-center")
    return Path(base) / "codex-thread-wedge.json"


def _codex_sessions_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")) / "sessions"


def is_timeout(response) -> bool:
    if not isinstance(response, dict):
        return False
    err = response.get("error")
    text = err if isinstance(err, str) else (err or {}).get("message", "") if isinstance(err, dict) else ""
    return "timed out" in str(text).lower()


def thread_id_of(params) -> str:
    if isinstance(params, dict):
        return str(params.get("threadId") or params.get("thread_id") or "")
    return ""


def note_result(method, params, response, *, now=None) -> None:
    """Record one app-server reply (or timeout) for the thread it named."""
    sid = thread_id_of(params)
    if not sid:
        return
    now = time.time() if now is None else float(now)
    changed = False
    with _lock:
        if is_timeout(response):
            entry = _threads.setdefault(sid, {"timeouts": 0, "first_at": now})
            entry["timeouts"] += 1
            entry["last_at"] = now
            entry["method"] = str(method or "")
            changed = True
        elif sid in _threads:
            _threads.pop(sid, None)
            changed = True
    if changed:
        _publish()


def wedged(sid, *, now=None) -> dict | None:
    """The wedge record for ``sid`` once it crosses the threshold, else None."""
    with _lock:
        entry = _threads.get(str(sid or ""))
        if not entry:
            return None
        span = float(entry.get("last_at", 0)) - float(entry.get("first_at", 0))
        if entry["timeouts"] >= WEDGE_TIMEOUTS and span >= WEDGE_MIN_SPAN_S:
            return dict(entry)
    return None


def short_circuit(method, params, *, now=None) -> dict | None:
    """A fast-fail response for a request to a wedged thread, or None to send.

    Every caller (busy probes, recovery watchdogs, delivery) goes through the
    app-server choke point, so failing fast there is what actually stops hung
    requests piling up inside the daemon. A probe is let through once per
    PROBE_INTERVAL_S so a thread that recovers by itself is noticed.
    """
    sid = thread_id_of(params)
    if not sid:
        return None
    now = time.time() if now is None else float(now)
    with _lock:
        entry = _threads.get(sid)
        if not entry:
            return None
        span = float(entry.get("last_at", 0)) - float(entry.get("first_at", 0))
        if entry["timeouts"] < WEDGE_TIMEOUTS or span < WEDGE_MIN_SPAN_S:
            return None
        if now - float(entry.get("last_attempt_at") or entry.get("last_at") or 0) >= PROBE_INTERVAL_S:
            entry["last_attempt_at"] = now
            return None
    return {"error": SKIPPED_ERROR, "wedged": True}


def _other_rollout_active(exclude_sid, now, sessions_dir=None) -> str | None:
    """A recently written rollout for any other thread means live Codex work."""
    root = Path(sessions_dir) if sessions_dir else _codex_sessions_dir()
    days = {time.strftime("%Y/%m/%d", time.localtime(now - d)) for d in (0, 86400)}
    for day in days:
        folder = root / day
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if not entry.name.endswith(".jsonl") or exclude_sid in entry.name:
                continue
            try:
                if now - entry.stat().st_mtime < OTHER_ACTIVITY_WINDOW_S:
                    return entry.name
            except OSError:
                continue
    return None


def _run(cmd, timeout):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "rc": proc.returncode,
                "output": (proc.stdout + proc.stderr)[-400:]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}


def _daemon_pid() -> int:
    """The managed app-server pid from Codex's daemon.pid, if it is that process."""
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    try:
        pid = int(json.loads((home / "app-server-daemon" / "daemon.pid").read_text())["pid"])
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return pid if b"app-server" in cmdline else 0


def _stop_pid(pid, grace_s=GRACEFUL_STOP_S) -> bool:
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.time() + grace_s
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return True


def _restart_daemon(codex_bin, *, run=_run, daemon_pid=_daemon_pid, stop_pid=_stop_pid) -> dict:
    """Restart the managed daemon; force it when a graceful restart hangs.

    A graceful ``daemon restart`` puts the daemon into draining, and a daemon
    wedged on a thread never finishes draining -- it then refuses new turns
    for EVERY thread (observed 2026-11-02). So the graceful attempt is
    bounded, and on failure the daemon pid is stopped and started fresh.
    The caller has already established that no Codex turn is running.
    """
    graceful = run([codex_bin, "app-server", "daemon", "restart"], GRACEFUL_RESTART_TIMEOUT_S)
    if graceful.get("ok"):
        return dict(graceful, mode="graceful")
    pid = daemon_pid()
    if pid:
        stop_pid(pid)
    started = run([codex_bin, "app-server", "daemon", "start"], RESTART_TIMEOUT_S)
    return {"ok": bool(started.get("ok")), "mode": "forced", "killed_pid": pid,
            "graceful": graceful, "output": started.get("output") or started.get("error")}


def maybe_recover(sid, *, turn_active, codex_bin, log=None, now=None,
                  sessions_dir=None, restart=None) -> dict:
    """Decide what the queue pump does for a wedged thread.

    Returns {"wedged": False} when the thread is healthy (pump proceeds),
    {"wedged": True, "action": "wait", "reason": ...} when it must not send,
    or {"wedged": True, "action": "restarted", ...} after a daemon restart
    (pump proceeds; the next request reconnects to the fresh daemon).
    ``turn_active`` is a zero-arg callable: is any CCC-tracked turn live?
    """
    global _last_restart_at, _last_restart_result
    now = time.time() if now is None else float(now)
    record = wedged(sid, now=now)
    if not record:
        return {"wedged": False}
    base = {"wedged": True, "timeouts": record["timeouts"], "since": record["first_at"]}

    def wait(reason):
        out = dict(base, action="wait", reason=reason)
        _publish(waiting={str(sid): reason})
        return out

    if now - _last_restart_at < RESTART_COOLDOWN_S:
        return wait("daemon restarted recently; still not answering this thread")
    try:
        if turn_active():
            return wait("another Codex turn is running; restart deferred")
    except Exception:
        return wait("could not confirm other Codex turns are idle")
    other = _other_rollout_active(str(sid), now, sessions_dir)
    if other:
        return wait(f"another Codex session is writing ({other[:60]}); restart deferred")
    if not codex_bin:
        return wait("Codex CLI not found; cannot restart the daemon")
    _last_restart_at = now
    result = (restart or _restart_daemon)(codex_bin)
    _last_restart_result = dict(result, at=now, sid=str(sid))
    if log:
        try:
            log(f"session={sid} timeouts={record['timeouts']} since={int(record['first_at'])} "
                f"ok={result.get('ok')} {str(result.get('error') or result.get('output') or '')[:160]}")
        except Exception:
            pass
    with _lock:
        _threads.clear()
    _publish()
    return dict(base, action="restarted", restart=result)


def _publish(waiting=None) -> None:
    with _lock:
        snapshot = {sid: dict(v) for sid, v in _threads.items()}
    for sid, reason in (waiting or {}).items():
        if sid in snapshot:
            snapshot[sid]["waiting"] = reason
    payload = {"updated_at": time.time(), "threads": snapshot,
               "last_restart": _last_restart_result}
    path = _state_file()
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
    except OSError:
        pass


def read_published(sid) -> dict | None:
    """Dashboard-side view of a wedged thread (written by the worker)."""
    try:
        data = json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return None
    entry = (data.get("threads") or {}).get(str(sid or ""))
    if not entry:
        return None
    span = float(entry.get("last_at", 0)) - float(entry.get("first_at", 0))
    if entry.get("timeouts", 0) < WEDGE_TIMEOUTS or span < WEDGE_MIN_SPAN_S:
        return None
    return entry


def reset_for_tests() -> None:
    global _last_restart_at, _last_restart_result
    with _lock:
        _threads.clear()
    _last_restart_at = 0.0
    _last_restart_result = None
