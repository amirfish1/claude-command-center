# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Hold / Release for runaway sessions (the runaway card's Hold button).

A held session stops waking itself; nothing is killed and a running turn is
left to finish. Each wake source is blocked where it enters the session:

  * Claude timers (ScheduleWakeup, CronCreate, /loop), Claude peer messages
    (native UDS, WatchTower's FIFO) and every other prompt: the
    UserPromptSubmit hook (hooks/user-prompt-submit.py) reads HOLDS_FILE and
    blocks the prompt before any model call (a blocked fire costs 0 tokens).
  * CCC /api/inject-input: refuses non-composer deliveries (WatchTower's
    delegate, peers, CCC's own wakes) with ``held: true``. WatchTower keeps a
    refused message in its outbox and retries it after Release. A composer
    send from the dashboard is the human taking over, so it releases the hold.
  * Codex goal continuations: Hold pauses an active goal in Codex's native
    goal store; Release resumes it (only when Hold was what paused it).

The hook reads HOLDS_FILE directly (no server round trip), so its schema is
the contract: ``{session_id: {"at": epoch, "engine": str, "goal_paused": bool}}``.
"""

from __future__ import annotations

import json
import os
import threading
import time

from ccc_server import restart_recovery as _rr

HOLDS_FILE = _rr.STATE_DIR / "runaway-holds.json"
BLOCKS_LOG = _rr.STATE_DIR / "runaway-hold-blocks.log"

_LOCK = threading.Lock()
_CACHE = {"key": None, "data": {}}


def _path(holds_file=None):
    return holds_file or HOLDS_FILE


def _read(holds_file=None):
    path = _path(holds_file)
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _CACHE["key"] == key:
        return _CACHE["data"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    _CACHE.update(key=key, data=data)
    return data


def _write(data, holds_file=None):
    path = _path(holds_file)
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def is_held(session_id, holds_file=None):
    """Cheap per-inject check: one stat, re-read only when the file changed."""
    sid = str(session_id or "")
    return bool(sid) and sid in _read(holds_file)


def holds(holds_file=None):
    return dict(_read(holds_file))


def _goal_action(session_id, action):
    """Pause / resume a Codex goal; True when the store changed."""
    try:
        from ccc_server import core as _core
        if action == "pause":
            goal = _core._codex_goals_snapshot().get(session_id) or {}
            if str(goal.get("status") or "").lower() != "active":
                return False
        return bool(_core._codex_goal_via_app_server(session_id, action).get("ok"))
    except Exception:
        return False


def hold(session_id, engine="claude", now=None, holds_file=None, goal_fn=None):
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}
    engine = str(engine or "claude").strip().lower() or "claude"
    goal_fn = goal_fn or _goal_action
    with _LOCK:
        data = dict(_read(holds_file))
        entry = data.get(sid) or {"at": int(now or time.time()), "engine": engine, "goal_paused": False}
        if engine == "codex" and not entry.get("goal_paused"):
            entry["goal_paused"] = bool(goal_fn(sid, "pause"))
        data[sid] = entry
        _write(data, holds_file)
    _log("HOLD", sid, f"engine={engine} goal_paused={entry['goal_paused']}")
    return {"ok": True, "session_id": sid, "hold": entry}


def release(session_id, why="release", holds_file=None, goal_fn=None):
    sid = str(session_id or "").strip()
    goal_fn = goal_fn or _goal_action
    with _LOCK:
        data = dict(_read(holds_file))
        entry = data.pop(sid, None)
        if entry is None:
            return {"ok": True, "session_id": sid, "released": False}
        _write(data, holds_file)
    resumed = bool(entry.get("goal_paused")) and bool(goal_fn(sid, "resume"))
    _log("RELEASE", sid, f"why={why} goal_resumed={resumed}")
    return {"ok": True, "session_id": sid, "released": True, "goal_resumed": resumed}


def _log(kind, sid, detail):
    try:
        from ccc_server import core as _core
        _core._log_activity("runaway", kind, f"{sid} {detail}")
    except Exception:
        pass


def blocked_counts():
    """Wakes the hook blocked per held session (one log line per block)."""
    out = {}
    try:
        with open(BLOCKS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    out[parts[1]] = out.get(parts[1], 0) + 1
    except OSError:
        pass
    return out


def state():
    data = holds()
    counts = blocked_counts() if data else {}
    return {"ok": True, "held": [dict(v, session_id=k, blocked=counts.get(k, 0))
                                 for k, v in sorted(data.items(), key=lambda kv: -kv[1].get("at", 0))]}


def handle_api_get(handler):
    handler.send_json(state())


def handle_api_post(handler, path):
    if path not in ("/api/runaway/hold", "/api/runaway/release"):
        return False
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        body = json.loads(handler.rfile.read(length) or b"{}") if length > 0 else {}
    except (ValueError, OSError):
        body = {}
    sid = str((body or {}).get("session_id") or "").strip()
    if not sid:
        handler.send_json({"ok": False, "error": "missing session_id"}, 400)
    elif path == "/api/runaway/hold":
        handler.send_json(hold(sid, body.get("engine") or "claude"))
    else:
        handler.send_json(release(sid))
    return True
