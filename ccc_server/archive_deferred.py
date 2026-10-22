# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Archive actions that wait for a session's turn to end.

A phone swipe means one of two things, and both must leave a working
session alone until its turn is over:

- "background": hide the row now, keep the session running, and bring the
  row back to Active the next time the session needs the user (its turn
  ended, it asks a question, or it wants a permission).
- "stop": the user is done. Archive now, but only stop the process once
  the turn ends, so a mid-turn archive never cuts off finished work.

State is a small sidecar ({sid: {mode, at, mtime}}); one daemon thread
polls only those sids, and exits when the map is empty.
"""

from __future__ import annotations

import json
import threading
import time

from ccc_server import core as _core
from ccc_server import test_isolation_active as _test_isolation_active

MODES = ("background", "stop")
POLL_S = 20.0
# A backgrounded idle session that never wakes stays archived; forget it.
MAX_AGE_S = 7 * 24 * 3600

_lock = threading.RLock()
_thread = None


def _state_file():
    return _core.LOG_VIEWER_STATE_DIR / "archive-deferred.json"


def _load():
    try:
        data = json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(sid): entry for sid, entry in data.items()
        if isinstance(entry, dict) and entry.get("mode") in MODES
    }


def _save(entries):
    try:
        _core.LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps(entries, indent=2))
    except OSError:
        pass


def _transcript_mtime(sid):
    try:
        path, _parser = _core._resolve_conversation_reader(sid)
        if path and path.is_file():
            return path.stat().st_mtime
    except Exception:
        pass
    return 0.0


def _turn_state(sid):
    """(live, busy, needs_user) for one session."""
    try:
        status = _core.session_live_status(sid, _core.find_session_cwd(sid)) or {}
    except Exception:
        status = {}
    live = bool(status.get("live"))
    busy = live and _core._session_status_is_busy(status)
    needs_user = False
    try:
        notif = _core._read_notification_state(sid)
        if notif and _core._notification_is_blocking(notif):
            needs_user = True
    except Exception:
        pass
    try:
        if _core._pending_ask_user_question_for_session(sid):
            needs_user = True
    except Exception:
        pass
    return live, busy, needs_user


def is_busy(sid):
    return _turn_state(sid)[1]


def record(sid, mode):
    if not sid or mode not in MODES:
        return
    with _lock:
        entries = _load()
        entries[sid] = {"mode": mode, "at": time.time(), "mtime": _transcript_mtime(sid)}
        _save(entries)
    ensure_running()


def clear(sid):
    with _lock:
        entries = _load()
        if entries.pop(sid, None) is not None:
            _save(entries)


def pending(sid):
    entry = _load().get(sid)
    return entry.get("mode") if entry else None


def _wake(sid):
    _core._set_conversation_archived(sid, False, source="background-wake")
    try:
        from ccc_server import log_parse
        log_parse._recently_auto_unarchived[sid] = time.time()
    except Exception:
        pass
    try:
        _core._invalidate_dashboard("archive", entity_id=sid, reason="background-wake")
    except Exception:
        pass


def sweep_once(now=None):
    """Resolve every deferred entry whose turn has ended. Returns actions."""
    now = now if now is not None else time.time()
    actions = []
    with _lock:
        entries = _load()
        if not entries:
            return actions
        archived = set(_core._load_archived_conversations(sweep=False))
        archived -= set(_core._load_trashed_conversations(sweep=False))
        changed = False
        for sid, entry in list(entries.items()):
            # Moved back to Active, or trashed, by the user: nothing to do.
            if sid not in archived or now - float(entry.get("at") or 0) > MAX_AGE_S:
                entries.pop(sid)
                changed = True
                continue
            live, busy, needs_user = _turn_state(sid)
            if entry["mode"] == "stop":
                if busy and not needs_user:
                    continue
                if live:
                    _core._log_archive_event("deferred-stop", sid, "turn ended")
                    _core._kill_session_by_id(sid)
                actions.append((sid, "stopped" if live else "gone"))
            else:
                moved = _transcript_mtime(sid) > float(entry.get("mtime") or 0) + 0.5
                if not (needs_user or (moved and not busy)):
                    continue
                _wake(sid)
                actions.append((sid, "woke"))
            entries.pop(sid)
            changed = True
        if changed:
            _save(entries)
    return actions


def _loop():
    global _thread
    while True:
        time.sleep(POLL_S)
        try:
            sweep_once()
        except Exception:
            pass
        with _lock:
            if not _load():
                _thread = None
                return


def ensure_running():
    """Start the poller if any entry is pending and it isn't already up."""
    global _thread
    if _test_isolation_active():
        return
    with _lock:
        if _thread is not None or not _load():
            return
        _thread = threading.Thread(target=_loop, daemon=True, name="ccc-archive-deferred")
        _thread.start()
