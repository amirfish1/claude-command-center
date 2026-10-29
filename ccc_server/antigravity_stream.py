# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Persistent Antigravity (agy) conversation processes driven over stream-json.

Antigravity documents one way to send several turns to one process:
``agy --print= --input-format stream-json --output-format stream-json``.
stdin takes one NDJSON object per line (``{"event":"user","message":
{"content":"..."}}``), every turn ends with exactly one ``result`` event on
stdout, and the process exits once stdin is closed (after the current turn).

That gives the dashboard what a one-shot ``agy -p`` cannot: an explicit
end-of-turn signal that does not depend on the process exiting. Busy/idle is
decided from the ``result`` event, never from whether the process is alive.

The docs say nothing about messages sent mid-turn, so none are: while a turn
is in flight the caller queues durably and sends after ``result``.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time

from ccc_server import core as _core

_LOCK = threading.Lock()
_PROCS = {}  # conversation id -> _StreamProc
_REAPER_STARTED = False
_DISABLED_UNTIL = 0.0   # set when a stream process dies before finishing a turn


def enabled():
    if os.environ.get("CCC_ANTIGRAVITY_STREAM", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return time.time() >= _DISABLED_UNTIL


def _idle_close_seconds():
    try:
        return max(30.0, float(os.environ.get("CCC_ANTIGRAVITY_STREAM_IDLE_S", "600")))
    except ValueError:
        return 600.0


class _StreamProc:
    def __init__(self, sid, proc, log_fh, key, cwd):
        self.sid = sid
        self.proc = proc
        self.log_fh = log_fh
        self.key = key          # (model, add_dirs): a change needs a new process
        self.cwd = cwd
        self.busy = False
        self.busy_since = 0.0
        self.idle_since = time.time()
        self.inflight = None    # text of the turn being run
        self.turns = 0
        self.last_result = None
        self.exited = False
        self.started = time.time()
        self.lock = threading.Lock()

    def alive(self):
        return not self.exited and self.proc.poll() is None

    def _read(self):
        try:
            for raw in self.proc.stdout:
                try:
                    self.log_fh.write(raw.decode("utf-8", "replace"))
                    self.log_fh.flush()
                except (OSError, ValueError):
                    pass
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("event") == "result":
                    with self.lock:
                        self.busy = False
                        self.inflight = None
                        self.idle_since = time.time()
                        self.last_result = event.get("result") or {}
        finally:
            self.exited = True
            with self.lock:
                lost, self.inflight = self.inflight, None
                self.busy = False
            if lost and self.turns <= 1 and time.time() - self.started < 30:
                # Died right after start with nothing run (signed out, bad
                # flags, older agy): let the one-shot path handle this for a
                # while instead of looping the same message into it.
                global _DISABLED_UNTIL
                _DISABLED_UNTIL = time.time() + 300
            if lost:
                # The process went away mid-turn without a result: the
                # message may never have run. Put it back on the durable
                # queue instead of dropping it.
                try:
                    queued = _core._apply_pending_input_operations(self.sid, [{
                        "field": "resume", "action": "append_tail", "value": lost,
                    }])
                    _core._log_activity(
                        "inject", "AGY_STREAM_LOST",
                        f"session={self.sid} pid={self.proc.pid} "
                        f"requeued={int(bool(queued.get('ok')))}",
                    )
                except Exception:
                    pass
            try:
                self.log_fh.close()
            except OSError:
                pass

    def write(self, text):
        line = json.dumps({"event": "user", "message": {"content": text}}) + "\n"
        with self.lock:
            self.proc.stdin.write(line.encode())
            self.proc.stdin.flush()
            self.busy = True
            self.busy_since = time.time()
            self.inflight = text
            self.turns += 1

    def close(self):
        """Ask the process to exit: closing stdin ends it after the current turn."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass


def state(sid):
    """None when no live stream process owns ``sid``; else its busy/idle state."""
    with _LOCK:
        sp = _PROCS.get(sid)
    if sp is None:
        return None
    if not sp.alive():
        with _LOCK:
            if _PROCS.get(sid) is sp:
                _PROCS.pop(sid, None)
        return None
    if sp.busy and time.time() - sp.busy_since > _core._antigravity_resume_stale_seconds():
        # A turn that outlived the print timeout is hung, not working.
        sp.close()
        try:
            sp.proc.terminate()
        except OSError:
            pass
        return None
    return {
        "pid": sp.proc.pid, "busy": sp.busy, "turns": sp.turns,
        "key": sp.key, "idle_for_s": 0.0 if sp.busy else time.time() - sp.idle_since,
        "last_result": sp.last_result,
    }


def owns_pid(pid):
    with _LOCK:
        return any(sp.proc.pid == pid and sp.alive() for sp in _PROCS.values())


def send(sid, text):
    """Write ``text`` as the next turn of the live stream process.

    Returns {"ok": True, "via": "antigravity-stream"} or an error dict; the
    caller must have checked ``state(sid)`` says idle.
    """
    with _LOCK:
        sp = _PROCS.get(sid)
    if sp is None or not sp.alive():
        return {"ok": False, "error": "no live Antigravity stream process"}
    if sp.busy:
        return {"ok": False, "code": "busy", "error": "turn in progress"}
    try:
        sp.write(text)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"stream write failed: {exc}"}
    return {"ok": True, "pid": sp.proc.pid, "via": "antigravity-stream", "turn": sp.turns}


def spawn(sid, cmd, cwd, log_path, key):
    """Start the stream process for ``sid``. ``cmd`` already ends with the
    stream-json flags; the first message is sent with ``send``."""
    log_fh = open(log_path, "a")
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log_fh,
            cwd=cwd, start_new_session=True,
        )
    except (OSError, ValueError):
        log_fh.close()
        raise
    sp = _StreamProc(sid, proc, log_fh, key, cwd)
    threading.Thread(target=sp._read, daemon=True, name=f"agy-stream-{sid[:8]}").start()
    with _LOCK:
        old = _PROCS.get(sid)
        _PROCS[sid] = sp
    if old is not None and old is not sp:
        old.close()
    _start_reaper()
    return sp


def retire(sid):
    """Close the idle process for ``sid`` (settings changed). Safe if absent."""
    with _LOCK:
        sp = _PROCS.pop(sid, None)
    if sp is not None:
        sp.close()


def _start_reaper():
    global _REAPER_STARTED
    with _LOCK:
        if _REAPER_STARTED:
            return
        _REAPER_STARTED = True
    threading.Thread(target=_reap_idle_forever, daemon=True, name="agy-stream-reaper").start()


def _reap_idle_forever():
    while True:
        time.sleep(30)
        limit = _idle_close_seconds()
        with _LOCK:
            items = list(_PROCS.items())
        for sid, sp in items:
            if not sp.alive():
                with _LOCK:
                    if _PROCS.get(sid) is sp:
                        _PROCS.pop(sid, None)
            elif not sp.busy and time.time() - sp.idle_since > limit:
                with _LOCK:
                    if _PROCS.get(sid) is sp:
                        _PROCS.pop(sid, None)
                sp.close()  # exits by itself once stdin is closed
