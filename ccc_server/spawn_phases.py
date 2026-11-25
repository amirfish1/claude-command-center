"""Where a spawn request spends its time before the engine gets it (CCC-680).

A new-session click once took 12-21 s to start Claude while the worker ran
each spawn within a second of receiving it, and nothing recorded which step
in between had waited. This module gives one spawn request a per-thread
phase log:

- ``begin`` when the handler starts, with the moment its request line was read;
- ``mark`` at each step boundary (validated, shipped check, engine submit);
- a watchdog that, if the engine submit has not happened within
  ``STALL_AFTER_S``, logs the last step reached and samples every thread's
  stack while the request is still waiting;
- ``record_timeline`` writes the steps onto the spawn timeline as
  ``server_*`` marks so click -> request -> submit is visible per spawn;
- ``end`` (always, from the request's ``finally``) cancels the watchdog.

stdlib only; the stall reporter is injected so this module has no server
imports.
"""

import threading
import time

STALL_AFTER_S = 3.0
ENGINE_SUBMIT = "engine_submit"

_local = threading.local()


class _Phases:
    __slots__ = ("started", "steps", "last", "submitted", "timer")

    def __init__(self, started):
        self.started = started
        self.steps = {"request_received": started}
        self.last = "request_received"
        self.submitted = threading.Event()
        self.timer = None


def begin(request_epoch=None, on_stall=None, stall_after_s=STALL_AFTER_S):
    """Start tracking the current thread's spawn request."""
    end()
    started = float(request_epoch) if request_epoch else time.time()
    state = _Phases(started)
    if on_stall is not None:
        delay = max(0.0, stall_after_s - (time.time() - started))
        timer = threading.Timer(delay, _fire_stall, args=(state, on_stall))
        timer.daemon = True
        timer.name = "spawn-presubmit-watch"
        state.timer = timer
        timer.start()
    _local.state = state
    return state


def _fire_stall(state, on_stall):
    if state.submitted.is_set():
        return
    try:
        on_stall(
            last_step=state.last,
            waited_s=time.time() - state.started,
            submitted=state.submitted.is_set,
        )
    except Exception:
        pass


def mark(name):
    """Record that the current spawn request reached ``name``."""
    state = getattr(_local, "state", None)
    if state is None:
        return
    state.steps.setdefault(name, time.time())
    state.last = name
    if name == ENGINE_SUBMIT:
        state.submitted.set()
        if state.timer is not None:
            state.timer.cancel()


def steps():
    state = getattr(_local, "state", None)
    return dict(state.steps) if state is not None else {}


def record_timeline(session_id, timeline_get, timeline_mark):
    """Write this request's steps onto the session's spawn timeline.

    Values are ms after the timeline's t0 (the click), as ``server_<step>``.
    Returns True when at least one mark was written.
    """
    state = getattr(_local, "state", None)
    if state is None or not session_id:
        return False
    try:
        entry = timeline_get(session_id) or {}
        t0 = float(entry.get("t0"))
    except (TypeError, ValueError):
        return False
    wrote = False
    for name, epoch in state.steps.items():
        if timeline_mark(session_id, f"server_{name}", max(0.0, (epoch - t0) * 1000.0)):
            wrote = True
    return wrote


def end():
    """Stop tracking; safe to call on every request."""
    state = getattr(_local, "state", None)
    if state is None:
        return
    _local.state = None
    if state.timer is not None:
        state.timer.cancel()
