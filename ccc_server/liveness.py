# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""One liveness snapshot per delivery attempt.

The inject router and the terminal-queue watcher used to each compute
``session_live_status()``, the live spawn entry, the tool-child ``ps`` scan
and the headless-turn check on their own, a beat apart. A status flicker
between the two reads re-parked a message that the watcher had just cleared
for delivery, and the generic 60 s backoff then cost every real recovery an
extra minute (CCC-799). A :class:`Probe` is built once per attempt and handed
down, so the gate and the routing decision see the same world.

Every field is memoised on first use and never refreshed inside the attempt;
that is the point, not a cache to tune. Decisions that KILL a process (stale
retire, write-failed retire) deliberately keep their own fresh checks: a
memoised "not busy" must never destroy work.

Slice 4 of the delivery-owner work. Stdlib only; reaches server-side helpers
through the ``_core`` proxy at call time so test monkeypatches still apply.
"""

from __future__ import annotations

from ccc_server import core as _core

_UNSET = object()


class Probe:
    """Memoised liveness facts for one session during one delivery attempt."""

    __slots__ = (
        "session_id", "_cwd", "_status", "_spawn", "_tool_child",
        "_headless_turn",
    )

    def __init__(self, session_id, *, cwd=_UNSET, status=None):
        self.session_id = session_id
        self._cwd = cwd
        self._status = status if isinstance(status, dict) else None
        self._spawn = _UNSET
        self._tool_child = _UNSET
        self._headless_turn = _UNSET

    # ── session status ──────────────────────────────────────────────────
    @property
    def cwd(self):
        if self._cwd is _UNSET:
            self._cwd = _core.find_session_cwd(self.session_id)
        return self._cwd

    @property
    def status(self):
        """The ``session_live_status`` dict, probed at most once."""
        if self._status is None:
            self._status = _core.session_live_status(self.session_id, self.cwd) or {}
        return self._status

    @property
    def tty(self):
        return self.status.get("tty")

    @property
    def has_tty(self):
        return bool(_core._is_real_tty(self.tty))

    @property
    def live(self):
        return bool(self.status.get("live"))

    @property
    def busy(self):
        return bool(_core._session_status_is_busy(self.status))

    # ── CCC-owned spawn ─────────────────────────────────────────────────
    def spawn(self):
        """The live ``_spawned_sessions`` entry for this session, or None."""
        if self._spawn is _UNSET:
            self._spawn = _core._find_live_spawn_entry_for_session(self.session_id)
        return self._spawn

    def tool_child(self):
        """The spawn's active tool child (one ``ps`` scan), or None."""
        if self._tool_child is _UNSET:
            spawn = self.spawn()
            self._tool_child = (
                _core._spawn_entry_active_tool_child(spawn)
                if spawn is not None else None
            )
        return self._tool_child

    def headless_turn(self):
        """True while the spawn owes a result for a turn CCC started."""
        if self._headless_turn is _UNSET:
            spawn = self.spawn()
            self._headless_turn = bool(
                spawn is not None and _core._headless_turn_in_progress(spawn)
            )
        return self._headless_turn

    def __repr__(self):
        return f"Probe({self.session_id!r}, probed={self._status is not None})"


def ensure(probe, session_id):
    """Return ``probe`` when a caller supplied one, else a fresh unprobed one."""
    if probe is not None:
        return probe
    return Probe(session_id)
