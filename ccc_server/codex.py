"""Extracted from server.py (originally lines 30432-40159).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import ast
import base64
import fcntl
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Codex CLI binary resolution
# ---------------------------------------------------------------------------
# Tested against codex-cli 0.125.0-alpha.3, the version currently shipping
# inside /Applications/Codex.app.

CODEX_APP_BUNDLE_PATH = "/Applications/Codex.app/Contents/Resources/codex"
CODEX_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"
# Codex stores per-thread goals (the native `/goal` feature) in their own
# sqlite, separate from the thread index. Table `thread_goals` is keyed by
# `thread_id` (== our codex session_id). Both the legacy and the sqlite/-nested
# path have been observed; newest non-empty wins.
KIMI_SESSIONS_ROOT = Path.home() / ".kimi-code" / "sessions"
_CODEX_META_VERSION = 6
_CODEX_APP_SERVER_STATE_SCHEMA = 1
_CODEX_THREAD_REGISTRY_SCHEMA = 1
_CODEX_THREAD_VISIBILITY_RANK = {
    "unknown": 0,
    "registered-agent": 1,
    "worker": 2,
    "user-visible": 3,
}
_CODEX_THREAD_OWNER_RANK = {
    "codex-exec": 1,
    "wt-codex-exec": 1,
    "ccc-codex-exec": 1,
    "wt-private-app-server": 2,
    "ccc-managed-app-server": 3,
}
_CODEX_APP_SERVER_LOCK = threading.Condition()
_CODEX_APP_SERVER_READER = None
_CODEX_APP_SERVER_NEXT_ID = 1
_CODEX_APP_SERVER_RESPONSES = {}
_CODEX_APP_SERVER_THREAD_STATE = {}
_CODEX_APP_SERVER_TURN_THREAD = {}
_CODEX_APP_SERVER_WARMUP_LOCK = threading.Lock()
_CODEX_APP_SERVER_WARMUP_LAST = 0.0
_CODEX_APP_SERVER_LIVENESS_INTERVAL = 20.0
_CODEX_APP_SERVER_LIVENESS_TIMEOUT = 8.0
_CODEX_APP_SERVER_LIVENESS_MISS_THRESHOLD = 2
_CODEX_APP_SERVER_INFLIGHT_LOCK = threading.Lock()
# `thread/list` is a GLOBAL call -- one reply carries every thread the
# app-server knows -- so it must be throttled globally too. It used to be
# throttled per session id, and /api/session-status is polled once per
# visible Codex session, so N open sessions meant N identical thread/list
# requests every couple of seconds against the single serialized stdio
# channel. The backlog that built up starved everything sharing the channel,
# including a new session's thread/start (see the TIMEOUT/LATE storms in
# activity.log and docs/HANDOFF_codex_appserver_liveness.md).
# thread/name/set resolves against the thread's rollout file, so it can lose a
# race with Codex's first write to it ("rollout ... is empty"). Cheap bounded
# retry -- this runs in the background finalizer, so nobody is waiting on it.
_CODEX_NAME_SET_ATTEMPTS = 3
_CODEX_NAME_SET_RETRY_DELAY = 1.0
# Most recent _codex_finalize_spawn_async thread. Test-only synchronisation
# handle so a spawn assertion can join the background finalizer rather than
# sleeping; production never reads it.
_CODEX_THREAD_LIST_COND = threading.Condition()
# Longest we let a coalesced caller block on someone else's in-flight call
# before giving up and returning stale data. Comfortably above thread/list's
# own 3s timeout so the waiter normally sees the real result.
_CODEX_THREAD_LIST_WAIT_TIMEOUT = 5.0
# req_id -> (method, timed_out_at, timeout) for requests whose client-side
# timeout already fired. If the reply still arrives later, the reader logs
# LATE with the lateness -- this distinguishes "reply eventually arrives"
# (reader/scheduling/queueing delay) from "reply never arrives" (a prior
# request wedged the app-server's in-order stdio channel). Diagnostic for
# the liveness-miss investigation (docs/HANDOFF_codex_appserver_liveness.md).
_CODEX_APP_SERVER_ORPHANED_WAITERS = {}
# Wall time of the most recent message (response OR notification) received
# from the app-server. Any recent traffic proves the connection works in
# both directions, making the thread/list liveness probe redundant -- and
# probing during an active turn is actively harmful, because the app-server
# services one stdio connection largely in order, so the probe queues
# behind turn work and "fails" on a perfectly healthy server.
_CODEX_APP_SERVER_LAST_MSG_AT = 0.0
# How long a known-active turn with no new activity still counts as
# "server-side busy" for liveness purposes. Turns emit a steady stream of
# notifications, so a genuinely live turn keeps last_activity_at fresh; a
# stale one must not shield a wedged server from replacement forever.
_CODEX_APP_SERVER_TURN_BUSY_GRACE_S = 120.0
_CODEX_TELEMETRY_TURNS = {}
_CODEX_APP_SERVER_RECENT_ITEM_MAX = 24
_CODEX_APP_SERVER_ITEM_TEXT_MAX = 1200
_CODEX_COMPACTION_RECOVERY_GRACE_S = 10.0
_CODEX_COMPACTION_RECOVERY_STALL_S = 120.0
_CODEX_COMPACTION_RECOVERY_RETRY_S = 30.0
_CODEX_COMPACTION_RECOVERY_INTERRUPT_SETTLE_S = 2.0
_CODEX_COMPACTION_RECOVERY_MAX_ATTEMPTS = 2
_CODEX_COMPACTION_RECOVERY_TERMINAL = {"recovered", "suppressed", "exhausted"}
_CODEX_SILENT_TURN_STALL_S = 15 * 60.0
_CODEX_RECOVERY_RECONCILE_S = 60.0
_codex_recovery_reconciled_at = {}


def _codex_recovery_is_silent_turn(recovery):
    return isinstance(recovery, dict) and recovery.get("trigger") == "silent-turn"


def _codex_recovery_event_kind(recovery, suffix):
    prefix = (
        "turn_recovery"
        if _codex_recovery_is_silent_turn(recovery)
        else "compaction_recovery"
    )
    return f"{prefix}_{suffix}"


def _codex_recovery_activity_text(recovery):
    if _codex_recovery_is_silent_turn(recovery):
        return "Recovering stalled Codex turn"
    return "Recovering after compaction"


# ── Spawn timeline (debug stats) ─────────────────────────────────────
# Why a session "feels slow" is rarely one number. A Codex spawn crosses four
# boundaries -- CCC -> app-server -> Codex's rollout file on disk -> CCC's
# session list -> the browser -- and the user-visible wait is the LAST of
# those, not the first. Recording a mark at each boundary is what turns
# "it took 60 seconds" into a specific culprit.
#
# Bounded in-memory ring, mirrored to disk so a dashboard restart mid-debug
# does not lose the evidence. Marks are milliseconds since the spawn request
# was accepted, so every number reads as "time until X".
_SPAWN_TIMELINE_MAX = 200
_SPAWN_TIMELINE_LOCK = threading.Lock()
_SPAWN_TIMELINE = {}


def _spawn_timeline_enabled():
    """Stats collection is on by default and killable without a code change.

    Set CCC_SPAWN_STATS=0 to turn the whole thing off (collection *and* the
    UI panel) once it has served its purpose.
    """
    return os.environ.get("CCC_SPAWN_STATS", "1") not in ("0", "false", "no")


def _spawn_timeline_start(session_id, t0_epoch_ms=None, **fields):
    """Open a timeline for a spawn. t0 is when CCC accepted the request."""
    if not _spawn_timeline_enabled() or not session_id:
        return
    # Adopt the shared history before adding to it, so a freshly booted
    # process does not hold a dict that knows only about its own spawns.
    _core._spawn_timeline_sync()
    now = time.time()
    try:
        requested_t0 = float(t0_epoch_ms) / 1000.0
    except (TypeError, ValueError):
        requested_t0 = now
    # The browser and server share the local machine clock. Accept its click
    # timestamp only inside a tight sanity window so a malformed/stale client
    # cannot produce absurd or negative timing rows.
    t0 = requested_t0 if abs(now - requested_t0) <= 60.0 else now
    with _SPAWN_TIMELINE_LOCK:
        _core._SPAWN_TIMELINE[str(session_id)] = {
            "session_id": str(session_id),
            "t0": t0,
            "marks": {},
            **fields,
        }
        while len(_core._SPAWN_TIMELINE) > _SPAWN_TIMELINE_MAX:
            _core._SPAWN_TIMELINE.pop(next(iter(_core._SPAWN_TIMELINE)), None)
    if fields.get("engine"):
        try:
            _core.record_model_picker_pick(
                fields.get("engine"),
                fields.get("model") or "",
                fields.get("reasoning_effort") or "",
            )
        except Exception:
            pass


def _spawn_timeline_sync():
    """Pull in whatever the *other* process has written since we last looked.

    The dashboard and the worker each run their own copy of this module
    (worker_engines.py lazily imports server), and the marks are split across
    both: the engine ones (thread_start_done ... finalize_done) are recorded
    in the worker, row_in_session_list in the dashboard. The JSON file is the
    only thing they share.

    Reading it once at startup is not enough, and that is exactly how this
    first shipped broken: the dashboard boots, loads an empty file, the worker
    writes a spawn a minute later, and every query is answered from the
    dashboard's stale dict -- so the panel showed nothing at all. Stat-gated
    so the common case costs one stat() rather than a parse.
    """
    if not _spawn_timeline_enabled():
        return
    try:
        st = _core._SPAWN_TIMELINE_FILE.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return
    if sig == _core._SPAWN_TIMELINE_FILE_SIG:
        return
    try:
        data = json.loads(_core._SPAWN_TIMELINE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    _core._SPAWN_TIMELINE_FILE_SIG = sig
    if not isinstance(data, dict):
        return
    with _SPAWN_TIMELINE_LOCK:
        for sid, entry in data.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("marks"), dict):
                continue
            mine = _core._SPAWN_TIMELINE.get(sid)
            if mine is None:
                _core._SPAWN_TIMELINE[sid] = entry
                continue
            # Our own marks win on conflict: we recorded them live, the file
            # copy may predate them and first-write-wins is the contract.
            merged = dict(entry["marks"])
            merged.update(mine.get("marks") or {})
            mine["marks"] = merged
        while len(_core._SPAWN_TIMELINE) > _SPAWN_TIMELINE_MAX:
            _core._SPAWN_TIMELINE.pop(next(iter(_core._SPAWN_TIMELINE)), None)


def _spawn_timeline_mark(session_id, name, value_ms=None):
    """Record a named mark. Defaults to 'now, relative to t0'.

    First write wins: these are "time until X *first* happened" and a later
    poll must not overwrite the moment it actually occurred.

    Returns True only when this call actually wrote the mark, so a caller on a
    hot path can persist on the transition instead of on every poll.
    """
    if not _spawn_timeline_enabled() or not session_id:
        return False
    _core._spawn_timeline_sync()
    with _SPAWN_TIMELINE_LOCK:
        entry = _core._SPAWN_TIMELINE.get(str(session_id))
        if not entry or name in entry["marks"]:
            return False
        entry["marks"][name] = (
            round(float(value_ms), 1) if value_ms is not None
            else round((time.time() - entry["t0"]) * 1000, 1)
        )
        return True


def _spawn_timeline_get(session_id):
    _core._spawn_timeline_sync()
    with _SPAWN_TIMELINE_LOCK:
        entry = _core._SPAWN_TIMELINE.get(str(session_id))
        return json.loads(json.dumps(entry)) if entry else None


def _spawn_timeline_save():
    """Persist the shared store. ALWAYS read-merge-write, never blind-write.

    This file is written by two processes (the worker records the engine
    marks, the dashboard records row_in_session_list), and a save writes the
    whole dict. A process that boots, starts one spawn and saves would
    otherwise replace the file with that single entry and wipe every other
    spawn -- which is exactly what happened: the file kept collapsing to one
    record. Syncing first makes the write additive.
    """
    if not _spawn_timeline_enabled():
        return
    _core._spawn_timeline_sync()
    try:
        with _SPAWN_TIMELINE_LOCK:
            payload = json.loads(json.dumps(_core._SPAWN_TIMELINE))
        tmp = _core._SPAWN_TIMELINE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, _core._SPAWN_TIMELINE_FILE)
        # Adopt our own write as the seen signature, so the next sync() does
        # not re-parse the file we just produced.
        st = _core._SPAWN_TIMELINE_FILE.stat()
        _core._SPAWN_TIMELINE_FILE_SIG = (st.st_mtime_ns, st.st_size)
    except (OSError, ValueError):
        pass


def _spawn_timeline_load():
    _core._spawn_timeline_sync()


def _codex_telemetry_append(event, **fields):
    rec = {
        "ts": time.time(),
        "event": str(event or "codex_event"),
    }
    rec.update({k: v for k, v in fields.items() if v is not None})
    try:
        _core.CODEX_TELEMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _core.CODEX_TELEMETRY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")
    except OSError:
        pass


def _codex_elapsed_ms(start):
    try:
        return round((time.monotonic() - float(start)) * 1000.0, 1)
    except (TypeError, ValueError):
        return None


def _codex_telemetry_register_turn(thread_id, turn_id, *, path, started_at_monotonic=None, **fields):
    if not turn_id:
        return
    rec = {
        "thread_id": str(thread_id or ""),
        "turn_id": str(turn_id),
        "path": str(path or "codex"),
        "started_at_monotonic": float(started_at_monotonic or time.monotonic()),
    }
    for key in ("transport", "model", "cwd"):
        if fields.get(key) is not None:
            rec[key] = fields[key]
    _core._CODEX_TELEMETRY_TURNS[str(turn_id)] = rec


def _codex_telemetry_note_notification(method, params, thread_id, turn_id):
    if not turn_id:
        return
    rec = _core._CODEX_TELEMETRY_TURNS.get(str(turn_id))
    if not rec:
        return
    latency_ms = _codex_elapsed_ms(rec.get("started_at_monotonic"))
    common = {
        "thread_id": thread_id or rec.get("thread_id"),
        "turn_id": str(turn_id),
        "path": rec.get("path"),
        "transport": rec.get("transport"),
        "model": rec.get("model"),
        "cwd": rec.get("cwd"),
        "latency_ms": latency_ms,
        "method": method,
    }
    if not rec.get("first_notification_seen"):
        rec["first_notification_seen"] = True
        _core._codex_telemetry_append("codex_turn_first_notification", **common)
    if method == "item/agentMessage/delta" and not rec.get("first_visible_output_seen"):
        rec["first_visible_output_seen"] = True
        _core._codex_telemetry_append("codex_turn_first_visible_output", **common)
    if method == "thread/tokenUsage/updated":
        usage = params.get("tokenUsage") or params.get("token_usage") or params.get("usage")
        if isinstance(usage, dict):
            rec["token_usage"] = usage
    if method == "turn/completed":
        _core._codex_telemetry_append(
            "codex_turn_complete",
            **common,
            token_usage=rec.get("token_usage"),
        )
        _core._CODEX_TELEMETRY_TURNS.pop(str(turn_id), None)


def _codex_thread_registry_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _codex_thread_registry_empty():
    return {
        "schema_version": _CODEX_THREAD_REGISTRY_SCHEMA,
        "authoritative": False,
        "source": "ccc-wt-codex-reconciliation",
        "updated_at": _codex_thread_registry_now(),
        "threads": {},
    }


def _load_codex_thread_registry():
    try:
        with _core.CODEX_THREAD_REGISTRY_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return _codex_thread_registry_empty()
    if not isinstance(data, dict):
        return _codex_thread_registry_empty()
    if not isinstance(data.get("threads"), dict):
        data["threads"] = {}
    data["schema_version"] = _CODEX_THREAD_REGISTRY_SCHEMA
    data["authoritative"] = False
    data.setdefault("source", "ccc-wt-codex-reconciliation")
    return data


def _save_codex_thread_registry(data):
    data["schema_version"] = _CODEX_THREAD_REGISTRY_SCHEMA
    data["authoritative"] = False
    data["source"] = "ccc-wt-codex-reconciliation"
    data["updated_at"] = _codex_thread_registry_now()
    _core.CODEX_THREAD_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _core.CODEX_THREAD_REGISTRY_FILE.with_suffix(_core.CODEX_THREAD_REGISTRY_FILE.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(_core.CODEX_THREAD_REGISTRY_FILE)


class _CodexThreadRegistryLock:
    def __init__(self):
        self._fh = None

    def __enter__(self):
        lock_path = _core.CODEX_THREAD_REGISTRY_FILE.with_suffix(_core.CODEX_THREAD_REGISTRY_FILE.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = lock_path.open("a+")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()


def _codex_thread_registry_merge_nested(dst, src):
    if not isinstance(src, dict):
        return dst
    out = dict(dst or {})
    for key, value in src.items():
        if value is None or value == "":
            continue
        out[key] = value
    return out


def _codex_thread_registry_merge_record(existing, fields, now):
    rec = dict(existing or {})
    rec.setdefault("created_at", now)
    rec["updated_at"] = now
    rec["thread_id"] = fields.get("thread_id") or rec.get("thread_id")
    rec["engine"] = "codex"
    source = fields.get("source")
    sources = [str(s) for s in rec.get("sources") or [] if s]
    if source and str(source) not in sources:
        sources.append(str(source))
    if sources:
        rec["sources"] = sources
    visibility = fields.get("visibility")
    if visibility and _CODEX_THREAD_VISIBILITY_RANK.get(str(visibility), 0) >= _CODEX_THREAD_VISIBILITY_RANK.get(str(rec.get("visibility") or "unknown"), 0):
        rec["visibility"] = str(visibility)
    owner = fields.get("transport_owner")
    if owner and _CODEX_THREAD_OWNER_RANK.get(str(owner), 0) >= _CODEX_THREAD_OWNER_RANK.get(str(rec.get("transport_owner") or ""), 0):
        rec["transport_owner"] = str(owner)
    for key in (
        "cwd",
        "repo_path",
        "transport",
        "title",
        "name",
        "parent_session_id",
        "report_to",
        "model",
        "reasoning_effort",
        "worker_id",
        "queue",
        "ref",
    ):
        value = fields.get(key)
        if value is not None and value != "":
            rec[key] = value
    for key in ("ccc", "wt"):
        value = fields.get(key)
        if isinstance(value, dict):
            nested = _codex_thread_registry_merge_nested(rec.get(key), value)
            if nested:
                rec[key] = nested
    return rec


def _codex_thread_registry_upsert(thread_id, **fields):
    sid = str(thread_id or "").strip()
    if not sid:
        return None
    fields["thread_id"] = sid
    try:
        with _CodexThreadRegistryLock():
            data = _load_codex_thread_registry()
            threads = data.setdefault("threads", {})
            now = _codex_thread_registry_now()
            existing = threads.get(sid) if isinstance(threads.get(sid), dict) else {}
            rec = _codex_thread_registry_merge_record(existing, fields, now)
            threads[sid] = rec
            _save_codex_thread_registry(data)
            return rec
    except OSError:
        return None


def _codex_thread_registry_entries():
    try:
        threads = _load_codex_thread_registry().get("threads") or {}
    except Exception:
        return {}
    return {
        str(sid): dict(rec)
        for sid, rec in threads.items()
        if sid and isinstance(rec, dict)
    }


def _codex_thread_registry_entry(thread_id):
    return _core._codex_thread_registry_entries().get(str(thread_id or "").strip())


def _codex_thread_registry_spawn_shape(entry):
    if not isinstance(entry, dict):
        return {}
    ccc = entry.get("ccc") if isinstance(entry.get("ccc"), dict) else {}
    wt = entry.get("wt") if isinstance(entry.get("wt"), dict) else {}
    return {
        "pid": ccc.get("spawn_id") or entry.get("worker_id") or wt.get("worker_id") or "",
        "alive": False,
        "log": ccc.get("log") or wt.get("log") or "",
        "cwd": entry.get("cwd") or "",
        "repo_path": entry.get("repo_path") or entry.get("cwd") or "",
        "spawned_at": ccc.get("spawned_at") or wt.get("started_at") or entry.get("created_at") or "",
        "prompt": ccc.get("prompt") or "",
        "model": entry.get("model") or "",
        "parent_session_id": entry.get("parent_session_id") or "",
        "worker_id": entry.get("worker_id") or wt.get("worker_id") or "",
        "queue": entry.get("queue") or wt.get("queue") or "",
        "ref": entry.get("ref") or wt.get("ref") or "",
        "visibility": entry.get("visibility") or "",
        "transport_owner": entry.get("transport_owner") or "",
    }


def _codex_app_server_state_payload_unlocked():
    threads = {}
    for sid, state in (_core._CODEX_APP_SERVER_THREAD_STATE or {}).items():
        if not sid or not isinstance(state, dict):
            continue
        public = {}
        for key in (
            "thread_id",
            "status",
            "active_flags",
            "thread_needs_approval",
            "event_seq",
            "last_event",
            "last_event_at",
            "last_activity_at",
            "last_turn_id",
            "active_turn_id",
            "last_completed_turn_id",
            "token_usage",
            "thread_settings",
            "pending_approval_request",
            "active_item",
            "last_item",
            "recent_items",
            "coordination_events",
            "compaction_recovery",
        ):
            if key in state:
                public[key] = state[key]
        threads[str(sid)] = public
    return {
        "schema_version": _CODEX_APP_SERVER_STATE_SCHEMA,
        "authoritative": False,
        "source": "codex-app-server-notifications",
        "updated_at": time.time(),
        "transport": _core._codex_app_server_transport_kind(),
        "threads": threads,
    }


def _save_codex_app_server_state_unlocked():
    """Persist compact last-known Codex app-server state for UI/diagnostics.

    This is intentionally not the durable transcript. Consumers must treat
    Codex rollout/session files as authoritative history and use this only for
    live status, telemetry, and coordination hints.
    """
    try:
        _core.CODEX_APP_SERVER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _core.CODEX_APP_SERVER_STATE_FILE.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(_core._codex_app_server_state_payload_unlocked(), f, indent=2, sort_keys=True)
        tmp.replace(_core.CODEX_APP_SERVER_STATE_FILE)
    except OSError:
        pass


def _codex_managed_app_server_socket_path():
    raw = os.environ.get("CCC_CODEX_APP_SERVER_SOCKET")
    if raw:
        return Path(os.path.expanduser(raw))
    return Path.home() / ".codex" / "app-server-control" / "app-server-control.sock"


def _codex_managed_app_server_enabled():
    return os.environ.get("CCC_CODEX_MANAGED_APP_SERVER", "1").lower() not in ("0", "false", "no")


def _codex_shared_state_db_files():
    """Codex SQLite files that must not be held by more than one app-server writer."""
    return [
        Path.home() / ".codex" / "state_5.sqlite",
        Path.home() / ".codex" / "logs_2.sqlite",
    ]


_CODEX_SHARED_STATE_HOLDER_CACHE = {"ts": 0.0, "holders": None}
_CODEX_SHARED_STATE_HOLDER_LOCK = threading.Lock()
_CODEX_SHARED_STATE_HOLDER_TTL_S = 5.0


def _codex_shared_state_db_holders(now=None):
    """Processes other than CCC's own app-server that hold the shared state DBs.

    Returns a list of {pid, command, file} dicts. The result is TTL-cached
    because the callers are spawn/resume operations and the underlying lsof
    fork is expensive.
    """
    now = time.time() if now is None else float(now)
    with _CODEX_SHARED_STATE_HOLDER_LOCK:
        if now - _CODEX_SHARED_STATE_HOLDER_CACHE["ts"] < _CODEX_SHARED_STATE_HOLDER_TTL_S:
            cached = _CODEX_SHARED_STATE_HOLDER_CACHE["holders"]
            return list(cached) if cached is not None else []
        _CODEX_SHARED_STATE_HOLDER_CACHE["ts"] = now
    files = [str(p) for p in _codex_shared_state_db_files() if p.exists()]
    if not files:
        with _CODEX_SHARED_STATE_HOLDER_LOCK:
            _CODEX_SHARED_STATE_HOLDER_CACHE["holders"] = []
        return []
    own_pgid = None
    try:
        proc = _core._CODEX_APP_SERVER_PROC
        if proc is not None and proc.pid:
            own_pgid = os.getpgid(proc.pid)
    except Exception:
        own_pgid = None
    lsof_bin = shutil.which("lsof") or "/usr/sbin/lsof"
    if not os.path.isfile(lsof_bin):
        with _CODEX_SHARED_STATE_HOLDER_LOCK:
            _CODEX_SHARED_STATE_HOLDER_CACHE["holders"] = []
        return []
    try:
        out = subprocess.run(
            [lsof_bin, "-w", "-Fpcn", *files],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3.0,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        with _CODEX_SHARED_STATE_HOLDER_LOCK:
            _CODEX_SHARED_STATE_HOLDER_CACHE["holders"] = []
        return []
    holders = []
    pid = None
    cmd = None
    for line in out.splitlines():
        if not line:
            continue
        tag, rest = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(rest)
            except ValueError:
                pid = None
            cmd = None
        elif tag == "c":
            cmd = rest
        elif tag == "n" and pid is not None:
            try:
                if own_pgid is not None and os.getpgid(pid) == own_pgid:
                    continue
            except Exception:
                pass
            holders.append({"pid": pid, "command": cmd or "codex", "file": rest})
    seen = set()
    unique = []
    for h in holders:
        if h["pid"] not in seen:
            seen.add(h["pid"])
            unique.append(h)
    with _CODEX_SHARED_STATE_HOLDER_LOCK:
        _CODEX_SHARED_STATE_HOLDER_CACHE["holders"] = unique
    return unique


def _codex_shared_state_conflict(now=None):
    """Return a human-readable conflict summary if another Codex writer holds the shared DBs."""
    holders = _core._codex_shared_state_db_holders(now)
    if not holders:
        return None
    pids = ",".join(str(h["pid"]) for h in holders)
    cmds = " / ".join(h["command"] or "codex" for h in holders)
    return {
        "holders": holders,
        "summary": f"pids={pids} commands={cmds}",
        "message": (
            f"Another Codex process is already using the shared state database "
            f"(pids {pids}: {cmds}). Running two Codex writers against the same "
            f"state store can cross-post messages between sessions. Quit the other "
            f"Codex process and retry."
        ),
    }


def _codex_app_server_stdio_safe_to_spawn(now=None):
    """True when no foreign Codex process holds the shared state DBs.

    CCC's private stdio app-server must be the only persistent writer against
    ~/.codex/state_5.sqlite. If a terminal `codex` TUI, the managed daemon, or
    another integration already holds those files, spawning a second private
    app-server risks cross-posting input between sessions.
    """
    return _core._codex_shared_state_conflict(now) is None


class _CodexAppServerTransport:
    def __init__(self, kind, *, proc=None, sock=None):
        self.kind = kind
        self.proc = proc
        self.sock = sock
        self.started_at = time.time()
        self._send_lock = threading.Lock()
        self.consecutive_liveness_misses = 0

    def alive(self):
        if self.kind == "stdio":
            return self.proc is not None and self.proc.poll() is None
        return self.sock is not None

    def send_json(self, payload):
        data = json.dumps(payload)
        with self._send_lock:
            if self.kind == "stdio":
                self.proc.stdin.write(data + "\n")
                self.proc.stdin.flush()
            else:
                _websocket_send_text(self.sock, data)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        proc = self.proc
        if proc is not None and proc.poll() is None:
            _core._app_server_trace("close-begin", child_pid=proc.pid)
            close_started = time.time()
            # terminate() only *asks*. The stdio reader thread sits in
            # `for line in proc.stdout`, which ends only at EOF -- and EOF only
            # arrives once this process is actually gone. So a SIGTERM the
            # app-server is slow to honour strands that reader forever, and the
            # spawn path has already dropped the handle to it: one leaked
            # `codex-app-server-reader-*` thread per reconnect, each pinning the
            # subprocess it was reading. Measured at 170 threads / 45% CPU in a
            # ccc_worker after a day of reconnects.
            #
            # So: ask, wait, escalate, reap. wait() also keeps the exited
            # process from lingering as a zombie.
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            except OSError:
                pass
            _core._app_server_trace(
                "close-end", child_pid=proc.pid,
                elapsed=round(time.time() - close_started, 2),
                reaped=proc.poll() is not None,
            )


def _read_exact(sock, n):
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _websocket_send_frame(sock, opcode, payload=b"", *, mask=True):
    payload = payload or b""
    first = 0x80 | (opcode & 0x0F)
    length = len(payload)
    if length <= 125:
        header = bytes([first, (0x80 if mask else 0) | length])
    elif length <= 0xFFFF:
        header = bytes([first, (0x80 if mask else 0) | 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([first, (0x80 if mask else 0) | 127]) + length.to_bytes(8, "big")
    if not mask:
        sock.sendall(header + payload)
        return
    key = os.urandom(4)
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    sock.sendall(header + key + masked)


def _websocket_send_text(sock, text):
    _websocket_send_frame(sock, 0x1, str(text).encode("utf-8"), mask=True)


def _websocket_recv_text(sock):
    fragments = []
    while True:
        first, second = _read_exact(sock, 2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(_read_exact(sock, 2), "big")
        elif length == 127:
            length = int.from_bytes(_read_exact(sock, 8), "big")
        mask_key = _read_exact(sock, 4) if masked else b""
        payload = _read_exact(sock, length) if length else b""
        if masked and payload:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            raise OSError("websocket closed")
        if opcode == 0x9:
            _websocket_send_frame(sock, 0xA, payload, mask=True)
            continue
        if opcode == 0xA:
            continue
        if opcode == 0x1:
            fragments = [payload]
        elif opcode == 0x0:
            fragments.append(payload)
        else:
            continue
        if fin:
            return b"".join(fragments).decode("utf-8", "replace")


def _connect_codex_managed_app_server(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect(str(path))
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 16384:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError("managed app-server closed during websocket handshake")
            response += chunk
        header = response.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1", "replace")
        lines = header.split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            raise OSError("managed app-server did not accept websocket upgrade")
        accept = ""
        for line in lines[1:]:
            name, _, value = line.partition(":")
            if name.lower() == "sec-websocket-accept":
                accept = value.strip()
                break
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()).decode("ascii")
        if accept and accept != expected:
            raise OSError("managed app-server websocket accept key mismatch")
        sock.settimeout(None)
        return _core._CodexAppServerTransport("managed-unix", sock=sock)
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


def _resolve_codex_bin():
    """Locate a usable Codex CLI binary.

    Priority order:
      1. $CCC_CODEX_BIN (env override) — if set and executable.
      2. `shutil.which("codex")` — picks up Homebrew / Cargo / npm-global.
      3. /Applications/Codex.app/Contents/Resources/codex (macOS Codex
         desktop app's bundled CLI).

    Returns a dict so the caller and the availability endpoint can share
    one shape:
      {available: True,  bin: "<abs path>", source: "env|path|bundle"}
      {available: False, reason: "<human readable>", bin: None}
    """
    env_bin = os.environ.get("CCC_CODEX_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return {"available": True, "bin": env_bin, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "codex_unavailable",
            "reason": f"CCC_CODEX_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("codex")
    # A PATH hit can still be a symlink into a deleted Codex.app
    # (/opt/homebrew/bin/codex -> /Applications/Codex.app/...); os.path.isfile
    # follows symlinks, so a dangling one falls through to the candidates.
    if which_bin and os.path.isfile(which_bin):
        return {"available": True, "bin": which_bin, "source": "path"}
    if os.path.isfile(_core.CODEX_APP_BUNDLE_PATH) and os.access(_core.CODEX_APP_BUNDLE_PATH, os.X_OK):
        return {"available": True, "bin": _core.CODEX_APP_BUNDLE_PATH, "source": "bundle"}
    # launchd services (the engine worker especially) run with a minimal PATH
    # that omits user bins like ~/.local/bin, where the standalone Codex CLI
    # installs. Same fallback the Gemini/Cursor resolvers use.
    for candidate in _core._iter_common_cli_candidates("codex"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return {"available": True, "bin": str(candidate), "source": "candidate"}
    return {
        "available": False,
        "bin": None,
        "code": "codex_unavailable",
        "reason": (
            "Codex CLI not found. Install Codex.app, "
            "`npm i -g @openai/codex`, or set CCC_CODEX_BIN."
        ),
    }


def _codex_notification_thread_id(method, params):
    for key in ("threadId", "thread_id", "conversationId", "conversation_id"):
        value = params.get(key) if isinstance(params, dict) else None
        if value:
            return str(value)
    thread = params.get("thread") if isinstance(params, dict) else None
    if isinstance(thread, dict) and thread.get("id"):
        return str(thread["id"])
    turn_id = _codex_notification_turn_id(method, params)
    if turn_id:
        return _core._CODEX_APP_SERVER_TURN_THREAD.get(turn_id)
    return None


def _codex_notification_turn_id(method, params):
    for key in ("turnId", "turn_id", "expectedTurnId"):
        value = params.get(key) if isinstance(params, dict) else None
        if value:
            return str(value)
    turn = params.get("turn") if isinstance(params, dict) else None
    if isinstance(turn, dict) and turn.get("id"):
        return str(turn["id"])
    item = params.get("item") if isinstance(params, dict) else None
    if isinstance(item, dict):
        for key in ("turnId", "turn_id"):
            if item.get(key):
                return str(item[key])
    return None


def _codex_app_server_request_thread_id(method, params):
    return _codex_notification_thread_id(method, params)


def _codex_app_server_request_turn_id(method, params):
    return _codex_notification_turn_id(method, params)


def _codex_app_server_flag_token(value):
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def _codex_app_server_flags_need_approval(flags):
    if not isinstance(flags, (list, tuple, set)):
        flags = [flags] if flags else []
    tokens = {_codex_app_server_flag_token(flag) for flag in flags if flag}
    return bool(tokens & {
        "waiting_on_approval",
        "waiting_for_approval",
        "approval_required",
        "approval_requested",
        "needs_approval",
        "requires_approval",
        "permission_required",
        "waiting_on_permission",
    })


def _codex_app_server_status_fields(status_obj):
    if isinstance(status_obj, dict):
        status = status_obj.get("type") or status_obj.get("status") or status_obj.get("state")
        flags = (
            status_obj.get("activeFlags")
            or status_obj.get("active_flags")
            or status_obj.get("flags")
            or []
        )
    else:
        status = status_obj
        flags = []
    if not isinstance(flags, list):
        flags = [flags] if flags else []
    return {
        "status": str(status or ""),
        "active_flags": [str(flag) for flag in flags if flag],
        "thread_needs_approval": _codex_app_server_flags_need_approval(flags),
    }


def _codex_app_server_thread_needs_approval(state):
    if not isinstance(state, dict):
        return False
    if state.get("thread_needs_approval"):
        return True
    return _codex_app_server_flags_need_approval(state.get("active_flags") or [])


def _codex_app_server_thread_approval_message(state):
    if not isinstance(state, dict):
        return "Codex is waiting for approval"
    last_item = state.get("last_item") if isinstance(state.get("last_item"), dict) else {}
    command = (last_item or {}).get("command")
    if command:
        return _codex_app_server_trim_text(
            f"Codex is waiting for approval after: {_core._shell_command_activity_label(command, max_len=200)}",
            240,
        )
    return "Codex is waiting for approval"


def _codex_pending_approval_hint(session_id):
    """The pending approval prompt's text when this thread's live turn is
    blocked on a Codex approval, else None.

    A turn stuck on `waitingOnApproval` holds the writer-gate open, so every
    queued inject sits behind it until someone answers the prompt (a
    goal-continuation turn once hung an hour on a `wt claim` escalation while
    wake messages piled up as generic "queued"). Never raises.
    """
    if not session_id:
        return None
    try:
        state = _core._codex_app_server_thread_state(session_id)
        if not _codex_app_server_thread_needs_approval(state):
            return None
        pending = _codex_app_server_pending_approval_item(state)
        message = ""
        if isinstance(pending, dict):
            message = str(
                pending.get("approval_message") or pending.get("command") or ""
            ).strip()
        if not message:
            message = _codex_app_server_thread_approval_message(state)
        return _codex_app_server_trim_text(message, 240)
    except Exception:
        return None


def _codex_app_server_record_thread(thread_id, thread):
    if not thread_id or not isinstance(thread, dict):
        return
    status_fields = _codex_app_server_status_fields(thread.get("status"))
    status = status_fields.get("status")
    state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(thread_id, {})
    previous_turn_id = str(
        state.get("active_turn_id") or state.get("last_turn_id") or ""
    )
    if status:
        state["status"] = status
    state["active_flags"] = status_fields.get("active_flags") or []
    state["thread_needs_approval"] = bool(status_fields.get("thread_needs_approval"))
    state["thread_id"] = thread_id
    state["last_event_at"] = time.time()
    turns = thread.get("turns") or []
    active = _codex_latest_active_turn(thread)
    if active and active.get("id"):
        turn_id = str(active["id"])
        state["active_turn_id"] = turn_id
        state["last_turn_id"] = turn_id
        # A thread/read after CCC restarts is observation, not progress. Keep
        # the persisted activity timestamp when it rediscovers the same turn;
        # otherwise every watcher reconciliation would make a silent turn look
        # newly active and it could sleep forever.
        if previous_turn_id != turn_id or not state.get("last_activity_at"):
            state["last_activity_at"] = state["last_event_at"]
        _core._CODEX_APP_SERVER_TURN_THREAD[turn_id] = thread_id
    elif str(status or "").lower() == "idle":
        state.pop("active_turn_id", None)
        state.pop("active_writer", None)
        state["thread_needs_approval"] = False
        state["active_flags"] = []
    for turn in turns:
        if isinstance(turn, dict) and turn.get("id"):
            _core._CODEX_APP_SERVER_TURN_THREAD[str(turn["id"])] = thread_id


def _codex_app_server_trim_text(value, limit=_CODEX_APP_SERVER_ITEM_TEXT_MAX):
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = text.strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _codex_app_server_json_preview(value, limit=240):
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return _codex_app_server_trim_text(value, limit)
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return _codex_app_server_trim_text(text, limit)


def _codex_app_server_item_id(item, params=None):
    params = params if isinstance(params, dict) else {}
    for value in (params.get("itemId"), params.get("item_id")):
        if value:
            return str(value)
    if isinstance(item, dict):
        for key in ("id", "call_id", "callId"):
            if item.get(key):
                return str(item[key])
    return ""


def _codex_app_server_file_change_detail(changes):
    if not isinstance(changes, list):
        return ""
    parts = []
    for change in changes[:6]:
        if not isinstance(change, dict):
            continue
        path = change.get("path") or change.get("move_path") or ""
        kind = change.get("kind")
        if isinstance(kind, dict):
            kind = kind.get("type")
        kind = str(kind or "update")
        if path:
            parts.append(f"{kind} {path}")
    if len(changes) > 6:
        parts.append(f"+{len(changes) - 6} more")
    return ", ".join(parts)


def _codex_app_server_command_text(command):
    if isinstance(command, list):
        try:
            return shlex.join([str(part) for part in command])
        except Exception:
            return " ".join(str(part) for part in command)
    return "" if command is None else str(command)


def _codex_app_server_file_changes_preview(changes):
    if isinstance(changes, list):
        return _codex_app_server_file_change_detail(changes)
    if not isinstance(changes, dict):
        return ""
    parts = []
    items = list(changes.items())
    for path, change in items[:6]:
        kind = "update"
        if isinstance(change, dict):
            raw_kind = change.get("type") or change.get("kind")
            if isinstance(raw_kind, dict):
                raw_kind = raw_kind.get("type")
            if raw_kind:
                kind = str(raw_kind)
        parts.append(f"{kind} {path}")
    if len(items) > 6:
        parts.append(f"+{len(items) - 6} more")
    return ", ".join(parts)


_CODEX_APP_SERVER_APPROVAL_ITEM_TYPES = {
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
    "collabAgentToolCall",
    "permissions",
}
_CODEX_APP_SERVER_COMMAND_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "execCommandApproval",
}
_CODEX_APP_SERVER_FILE_APPROVAL_METHODS = {
    "item/fileChange/requestApproval",
    "applyPatchApproval",
}
_CODEX_APP_SERVER_PERMISSION_APPROVAL_METHODS = {
    "item/permissions/requestApproval",
}
_CODEX_APP_SERVER_APPROVAL_REQUEST_METHODS = (
    _CODEX_APP_SERVER_COMMAND_APPROVAL_METHODS
    | _CODEX_APP_SERVER_FILE_APPROVAL_METHODS
    | _CODEX_APP_SERVER_PERMISSION_APPROVAL_METHODS
)
_CODEX_APP_SERVER_APPROVAL_STATUSES = {
    "approval_required",
    "approval_requested",
    "awaiting_approval",
    "blocked_on_approval",
    "needs_approval",
    "pending_approval",
    "permission_required",
    "permission_requested",
    "requires_approval",
    "requires_permission",
    "waiting_for_approval",
    "waiting_for_permission",
}
_CODEX_APP_SERVER_APPROVAL_BOOL_KEYS = (
    "approvalRequired",
    "approval_required",
    "requiresApproval",
    "requires_approval",
    "needsApproval",
    "needs_approval",
    "permissionRequired",
    "permission_required",
)
_CODEX_APP_SERVER_APPROVAL_MESSAGE_KEYS = (
    "approvalMessage",
    "approval_message",
    "approvalPrompt",
    "approval_prompt",
    "permissionMessage",
    "permission_message",
    "justification",
    "message",
    "prompt",
    "reason",
)


def _codex_app_server_status_text(value):
    if isinstance(value, dict):
        for key in ("type", "status", "state", "name"):
            if value.get(key):
                return str(value[key])
        return ""
    return str(value or "")


def _codex_app_server_status_token(value):
    text = _codex_app_server_status_text(value).strip()
    if not text:
        return ""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def _codex_app_server_truthy_approval_flag(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("", "0", "false", "no", "none", "null", "off"):
        return False
    return True


def _codex_app_server_approval_dicts(item, params=None):
    params = params if isinstance(params, dict) else {}
    for obj in (item, params):
        if not isinstance(obj, dict):
            continue
        yield obj
        for key in ("approval", "permission", "permissions", "confirmation", "sandbox"):
            child = obj.get(key)
            if isinstance(child, dict):
                yield child


def _codex_app_server_item_needs_approval(item, params=None):
    if not isinstance(item, dict):
        item = {}
    params = params if isinstance(params, dict) else {}
    typ = str(item.get("type") or params.get("itemType") or params.get("item_type") or "")
    if typ not in _CODEX_APP_SERVER_APPROVAL_ITEM_TYPES:
        return False
    status = item.get("status") if "status" in item else params.get("status")
    if _codex_app_server_status_token(status) in _CODEX_APP_SERVER_APPROVAL_STATUSES:
        return True
    for obj in _codex_app_server_approval_dicts(item, params):
        for key in _CODEX_APP_SERVER_APPROVAL_BOOL_KEYS:
            if key in obj and _codex_app_server_truthy_approval_flag(obj.get(key)):
                return True
        for key in ("status", "state", "type"):
            if _codex_app_server_status_token(obj.get(key)) in _CODEX_APP_SERVER_APPROVAL_STATUSES:
                return True
    actions = []
    for obj in (item, params):
        if not isinstance(obj, dict):
            continue
        for key in ("commandActions", "actions", "availableActions", "approvalActions"):
            value = obj.get(key)
            if isinstance(value, list):
                actions.extend(value)
    for action in actions:
        if isinstance(action, dict):
            text = " ".join(
                str(action.get(key) or "")
                for key in ("id", "type", "kind", "name", "label")
            ).lower()
        else:
            text = str(action or "").lower()
        if any(marker in text for marker in ("approval", "approve", "permission", "allow command", "deny")):
            return True
    return False


def _codex_app_server_approval_message(item, params=None, fallback=""):
    if not isinstance(item, dict):
        item = {}
    params = params if isinstance(params, dict) else {}
    for obj in _codex_app_server_approval_dicts(item, params):
        for key in _CODEX_APP_SERVER_APPROVAL_MESSAGE_KEYS:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return _codex_app_server_trim_text(value, 240)
    command = item.get("command") or params.get("command")
    if command:
        label = _core._shell_command_activity_label(command, max_len=220)
        return _codex_app_server_trim_text(f"Approval required for: {label}", 240)
    return _codex_app_server_trim_text(fallback or "Codex is waiting for approval", 240)


def _codex_app_server_item_summary(item, params=None, *, in_flight=None, now=None):
    """Compact a Codex app-server ThreadItem into CCC live-activity fields.

    App-server notifications are faster than rollout JSONL flushes and include
    rich non-JSONL item types. This summary is intentionally small: it drives
    live status UI only; persisted Codex rollout remains the durable transcript.
    """
    if not isinstance(item, dict):
        item = {}
    params = params if isinstance(params, dict) else {}
    typ = str(item.get("type") or params.get("itemType") or params.get("item_type") or "")
    status = item.get("status") or params.get("status") or ""
    item_id = _codex_app_server_item_id(item, params)
    needs_approval = _codex_app_server_item_needs_approval(item, params)
    if in_flight is None:
        in_flight = _codex_app_server_status_token(status) in ("", "inprogress", "in_progress", "running", "pending")
    if needs_approval:
        in_flight = True
    ts_ms = params.get("startedAtMs") or params.get("completedAtMs") or params.get("timestampMs")
    try:
        ts = float(ts_ms) / 1000.0 if ts_ms else float(now if now is not None else time.time())
    except (TypeError, ValueError):
        ts = float(now if now is not None else time.time())

    tool = ""
    detail = ""
    command = ""
    output = ""
    is_error = False

    if typ == "commandExecution":
        tool = "Bash"
        command = item.get("command") or ""
        detail = _core._shell_command_activity_label(command, max_len=240) if command else ""
        output = item.get("aggregatedOutput") or ""
        is_error = str(status or "").lower() in ("failed", "declined") or item.get("exitCode") not in (None, 0)
    elif typ == "fileChange":
        tool = "apply_patch"
        detail = _codex_app_server_file_change_detail(item.get("changes"))
        output = item.get("output") or ""
        is_error = str(status or "").lower() in ("failed", "declined")
    elif typ == "mcpToolCall":
        tool_name = item.get("tool") or "mcpToolCall"
        server = item.get("server") or ""
        tool = f"{server}.{tool_name}" if server else str(tool_name)
        detail = _codex_app_server_json_preview(item.get("arguments"))
        result = item.get("result")
        output = _codex_app_server_json_preview(result, limit=600)
        is_error = bool(item.get("error")) or str(status or "").lower() in ("failed", "errored")
    elif typ == "dynamicToolCall":
        tool_name = item.get("tool") or "dynamicToolCall"
        namespace = item.get("namespace") or ""
        tool = f"{namespace}.{tool_name}" if namespace else str(tool_name)
        detail = _codex_app_server_json_preview(item.get("arguments"))
        output = _codex_app_server_json_preview(item.get("contentItems"), limit=600)
        is_error = item.get("success") is False or str(status or "").lower() in ("failed", "errored")
    elif typ == "collabAgentToolCall":
        tool = item.get("tool") or "collabAgent"
        detail = _codex_app_server_json_preview(item.get("input") or item.get("arguments"))
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        output = _codex_app_server_trim_text(state.get("message") or "", 600)
        is_error = str((state.get("status") if state else status) or "").lower() in ("failed", "errored", "notfound")
    elif typ == "webSearch":
        tool = "WebSearch"
        detail = _codex_app_server_json_preview(item.get("action") or item.get("query"))
    elif typ == "imageView":
        tool = "view_image"
        detail = item.get("path") or item.get("url") or item.get("imageUrl") or ""
    elif typ == "imageGeneration":
        tool = "image_gen"
        detail = item.get("prompt") or item.get("description") or ""
    elif typ == "subAgentActivity":
        tool = "Task"
        detail = item.get("message") or item.get("title") or item.get("description") or ""
    elif typ == "agentMessage":
        detail = item.get("text") or ""
    elif typ == "plan":
        tool = "Plan"
        detail = item.get("text") or ""
    elif typ == "reasoning":
        tool = "Thinking"
        summary = item.get("summary") if isinstance(item.get("summary"), list) else []
        detail = " ".join(str(x) for x in summary if x)[:240]
    elif typ:
        tool = typ
        detail = _codex_app_server_json_preview(item)

    summary = {
        "id": item_id,
        "type": typ,
        "tool": _codex_app_server_trim_text(tool, 120),
        "detail": _codex_app_server_trim_text(detail, 240),
        "status": _codex_app_server_status_text(status) or ("approvalRequired" if needs_approval else ("inProgress" if in_flight else "completed")),
        "in_flight": bool(in_flight),
        "ts": ts,
        "updated_at": float(now if now is not None else time.time()),
    }
    if command:
        summary["command"] = _codex_app_server_trim_text(command)
    if output:
        summary["output"] = _codex_app_server_trim_text(output, 600)
    if is_error:
        summary["is_error"] = True
    if needs_approval:
        summary["needs_approval"] = True
        summary["approval_message"] = _codex_app_server_approval_message(item, params, fallback=detail or output)
    return summary


def _codex_app_server_latest_active_item(state):
    active = state.get("active_items") if isinstance(state, dict) else None
    if not isinstance(active, dict) or not active:
        return None
    items = [v for v in active.values() if isinstance(v, dict)]
    if not items:
        return None
    return max(items, key=lambda x: float(x.get("updated_at") or x.get("ts") or 0))


def _codex_app_server_pending_approval_item(state):
    if not isinstance(state, dict):
        return None
    candidates = []
    pending = state.get("pending_approval_request") if isinstance(state.get("pending_approval_request"), dict) else None
    if pending:
        candidates.append(pending)
    active_item = state.get("active_item") if isinstance(state.get("active_item"), dict) else None
    if active_item:
        candidates.append(active_item)
    active_items = state.get("active_items") if isinstance(state.get("active_items"), dict) else {}
    for item in active_items.values():
        if isinstance(item, dict):
            candidates.append(item)
    if state.get("active_turn_id") or str(state.get("status") or "").lower() == "active":
        last_item = state.get("last_item") if isinstance(state.get("last_item"), dict) else None
        if last_item:
            candidates.append(last_item)
    seen = set()
    deduped = []
    for item in candidates:
        key = item.get("id") or id(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    deduped.sort(key=lambda x: float(x.get("updated_at") or x.get("ts") or 0), reverse=True)
    for item in deduped:
        if item.get("needs_approval"):
            return item
    return None


def _codex_app_server_push_recent_item(state, item):
    if not isinstance(state, dict) or not isinstance(item, dict):
        return
    recent = state.setdefault("recent_items", [])
    if not isinstance(recent, list):
        recent = []
        state["recent_items"] = recent
    item_id = item.get("id")
    if item_id:
        recent[:] = [x for x in recent if not (isinstance(x, dict) and x.get("id") == item_id)]
    recent.append(dict(item))
    del recent[:-_CODEX_APP_SERVER_RECENT_ITEM_MAX]


def _codex_app_server_record_item_notification(state, method, params, now):
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_id = _codex_app_server_item_id(item, params)
    active = state.setdefault("active_items", {})
    if not isinstance(active, dict):
        active = {}
        state["active_items"] = active

    if method == "item/started":
        summary = _codex_app_server_item_summary(item, params, in_flight=True, now=now)
        if item_id:
            active[item_id] = summary
        state["active_item"] = summary
        state["last_item"] = summary
        return

    if method == "item/completed":
        summary = _codex_app_server_item_summary(item, params, in_flight=False, now=now)
        if item_id and isinstance(active.get(item_id), dict):
            merged = dict(active[item_id])
            merged.update({k: v for k, v in summary.items() if v not in (None, "", [], {})})
            summary = merged
        if item_id:
            active.pop(item_id, None)
        _codex_app_server_push_recent_item(state, summary)
        state["last_item"] = summary
        latest = _codex_app_server_latest_active_item(state)
        if latest:
            state["active_item"] = latest
        else:
            state.pop("active_item", None)
        return

    if method in ("item/commandExecution/outputDelta", "item/fileChange/outputDelta"):
        delta = params.get("delta") or ""
        if not item_id:
            return
        current = active.get(item_id)
        if not isinstance(current, dict):
            item_type = "fileChange" if method == "item/fileChange/outputDelta" else "commandExecution"
            current = _codex_app_server_item_summary(
                {"id": item_id, "type": item_type, "status": "inProgress"},
                params,
                in_flight=True,
                now=now,
            )
            active[item_id] = current
        current["output"] = _codex_app_server_trim_text((current.get("output") or "") + str(delta), 600)
        current["updated_at"] = now
        state["active_item"] = current
        state["last_item"] = current
        return

    if method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
        # Codex streams its reasoning summary token-by-token before the first
        # visible agentMessage delta (5-45s of otherwise-silent "Thinking…").
        # Accumulate it into the active item's detail so the sidecar shows
        # live progress instead of a static spinner (CCC-885).
        delta = params.get("delta") or ""
        if not item_id:
            return
        current = active.get(item_id)
        if not isinstance(current, dict):
            current = _codex_app_server_item_summary(
                {"id": item_id, "type": "reasoning", "status": "inProgress"},
                params,
                in_flight=True,
                now=now,
            )
            active[item_id] = current
        # Deltas can end/start mid-word or on whitespace; trimming on every
        # append (like _codex_app_server_trim_text does) would eat the join
        # points between chunks, so only cap total length here.
        accumulated = (current.get("detail") or "") + str(delta)
        if len(accumulated) > 240:
            accumulated = accumulated[-240:]
        current["detail"] = accumulated
        current["updated_at"] = now
        state["active_item"] = current
        state["last_item"] = current
        return

    if method == "item/fileChange/patchUpdated":
        changes = params.get("changes")
        if not item_id:
            return
        current = active.get(item_id)
        if not isinstance(current, dict):
            current = _codex_app_server_item_summary(
                {"id": item_id, "type": "fileChange", "status": "inProgress", "changes": changes},
                params,
                in_flight=True,
                now=now,
            )
            active[item_id] = current
        current["detail"] = _codex_app_server_file_change_detail(changes)
        current["updated_at"] = now
        state["active_item"] = current
        state["last_item"] = current


def _codex_compaction_recovery_note_notification_unlocked(
    state, method, params, turn_id, now
):
    """Update the durable post-compaction recovery latch from one notification."""
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_type = str(
        item.get("type") or params.get("itemType") or params.get("item_type") or ""
    )
    recovery = state.get("compaction_recovery")

    if item_type == "contextCompaction" and method in ("item/started", "item/completed"):
        episode_id = _codex_app_server_item_id(item, params)
        if not episode_id and isinstance(recovery, dict) and (
            recovery.get("compaction_in_flight")
            and str(recovery.get("compaction_turn_id") or "") == str(turn_id or "")
        ):
            episode_id = recovery.get("episode_id")
        episode_id = episode_id or f"compaction-{turn_id or int(float(now) * 1000)}"
        if not isinstance(recovery, dict) or recovery.get("episode_id") != episode_id:
            recovery = {
                "episode_id": episode_id,
                "trigger": "compaction",
                "compaction_turn_id": str(turn_id or state.get("active_turn_id") or ""),
                "compacted_at": float(now),
                "last_progress_at": float(now),
                "status": "waiting",
                "attempts": 0,
                "next_attempt_at": float(now) + _CODEX_COMPACTION_RECOVERY_GRACE_S,
                "reason": "Waiting for Codex to continue after compaction",
                "compaction_in_flight": method == "item/started",
                "saw_agent_output": False,
            }
            state["compaction_recovery"] = recovery
            _core._codex_coordination_event_unlocked(
                state,
                "compaction_recovery_armed",
                detail="Watching for Codex to continue after compaction",
                now=now,
            )
        else:
            recovery["compaction_in_flight"] = method == "item/started"
            recovery["last_progress_at"] = float(now)
        return

    if not isinstance(recovery, dict):
        return
    if str(recovery.get("status") or "") in _CODEX_COMPACTION_RECOVERY_TERMINAL:
        return

    if method == "turn/started":
        if turn_id and str(turn_id) != str(recovery.get("compaction_turn_id") or ""):
            recovery["status"] = "recovering"
            recovery["recovery_turn_id"] = str(turn_id)
            recovery["last_progress_at"] = float(now)
            recovery["reason"] = (
                "Codex continued after the turn went silent"
                if _codex_recovery_is_silent_turn(recovery)
                else "Codex continued after compaction"
            )
            recovery["saw_agent_output"] = False
        return

    if method == "item/agentMessage/delta":
        if str(params.get("delta") or "").strip():
            recovery["saw_agent_output"] = True
            recovery["last_progress_at"] = float(now)
        return

    if method in ("item/started", "item/completed"):
        recovery["last_progress_at"] = float(now)
        if item_type == "agentMessage" and str(item.get("text") or "").strip():
            recovery["saw_agent_output"] = True
        return

    if method.startswith("item/"):
        recovery["last_progress_at"] = float(now)
        return

    if method == "turn/completed":
        relevant_turns = {
            str(recovery.get("compaction_turn_id") or ""),
            str(recovery.get("recovery_turn_id") or ""),
        }
        if turn_id and str(turn_id) not in relevant_turns:
            return
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        completion_status = _codex_app_server_status_token(
            turn.get("status") or params.get("status")
        )
        interrupted = completion_status in {
            "aborted", "cancelled", "canceled", "failed", "interrupted"
        }
        if recovery.get("saw_agent_output") and not interrupted:
            recovery["status"] = "recovered"
            recovery["reason"] = (
                "Codex produced a final reply after recovering a silent turn"
                if _codex_recovery_is_silent_turn(recovery)
                else "Codex produced a final reply after compaction"
            )
            recovery["recovered_at"] = float(now)
            _core._codex_coordination_event_unlocked(
                state,
                _codex_recovery_event_kind(recovery, "recovered"),
                detail=recovery["reason"],
                now=now,
            )
        elif int(recovery.get("attempts") or 0) >= _CODEX_COMPACTION_RECOVERY_MAX_ATTEMPTS:
            recovery["status"] = "exhausted"
            recovery["reason"] = (
                "Silent-turn recovery ended without a final reply"
                if _codex_recovery_is_silent_turn(recovery)
                else "Compaction recovery ended without a final reply"
            )
            _core._codex_coordination_event_unlocked(
                state,
                _codex_recovery_event_kind(recovery, "exhausted"),
                detail=recovery["reason"],
                now=now,
            )
        else:
            recovery["status"] = "waiting"
            recovery["last_progress_at"] = float(now)
            recovery["next_attempt_at"] = float(now) + _CODEX_COMPACTION_RECOVERY_GRACE_S
            recovery["reason"] = (
                "Codex went idle after silent-turn recovery without a final reply"
                if _codex_recovery_is_silent_turn(recovery)
                else "Codex went idle after compaction without a final reply"
            )


def _codex_app_server_approval_request_summary(request_id, method, params, now):
    if not isinstance(params, dict):
        params = {}
    raw_request_id = request_id
    request_id = str(request_id or "").strip()
    approval_id = params.get("approvalId") or params.get("approval_id") or ""
    item_id = (
        params.get("itemId")
        or params.get("item_id")
        or params.get("callId")
        or params.get("call_id")
        or approval_id
        or request_id
    )
    turn_id = _codex_app_server_request_turn_id(method, params) or ""
    try:
        ts = float(params.get("startedAtMs") or 0) / 1000.0
    except (TypeError, ValueError):
        ts = 0.0
    if not ts:
        ts = float(now or time.time())

    tool = "Approval"
    typ = "approval"
    detail = ""
    command = ""
    message = str(params.get("reason") or "").strip()
    can_approve = False

    if method in _CODEX_APP_SERVER_COMMAND_APPROVAL_METHODS:
        typ = "commandExecution"
        tool = "Bash"
        command = _codex_app_server_command_text(params.get("command"))
        detail = _core._shell_command_activity_label(command, max_len=240) if command else ""
        message = message or (f"Approve command: {detail}" if detail else "Codex is waiting for command approval")
        can_approve = True
    elif method in _CODEX_APP_SERVER_FILE_APPROVAL_METHODS:
        typ = "fileChange"
        tool = "apply_patch"
        detail = (
            _codex_app_server_file_changes_preview(params.get("changes"))
            or _codex_app_server_file_changes_preview(params.get("fileChanges"))
            or str(params.get("grantRoot") or "").strip()
        )
        if params.get("grantRoot") and not message:
            message = f"Approve file writes under {params.get('grantRoot')}"
        message = message or (f"Approve file changes: {detail}" if detail else "Codex is waiting for file-change approval")
        can_approve = True
    elif method in _CODEX_APP_SERVER_PERMISSION_APPROVAL_METHODS:
        typ = "permissions"
        tool = "Permissions"
        permissions = params.get("permissions")
        detail = (
            str(params.get("cwd") or "").strip()
            or _codex_app_server_json_preview(permissions)
        )
        message = message or "Codex is requesting additional permissions"
        can_approve = isinstance(permissions, dict)
    else:
        return None

    summary = {
        "id": str(item_id or request_id),
        "type": typ,
        "tool": tool,
        "detail": _codex_app_server_trim_text(detail, 240),
        "status": "waiting_for_approval",
        "in_flight": True,
        "needs_approval": True,
        "approval_message": _codex_app_server_trim_text(message, 240),
        "request_id": request_id,
        "request_id_raw": raw_request_id,
        "approval_method": method,
        "item_id": str(item_id or ""),
        "turn_id": str(turn_id or ""),
        "approval_id": str(approval_id or ""),
        "can_approve": bool(can_approve),
        "ts": ts,
        "updated_at": float(now or time.time()),
    }
    if command:
        summary["command"] = _codex_app_server_trim_text(command)
    available = params.get("availableDecisions") or params.get("available_decisions")
    if isinstance(available, list):
        summary["available_decisions"] = available
    if method in _CODEX_APP_SERVER_PERMISSION_APPROVAL_METHODS and isinstance(params.get("permissions"), dict):
        summary["requested_permissions"] = params.get("permissions")
    return summary


def _codex_app_server_record_server_request(request_id, method, params):
    if not isinstance(params, dict):
        params = {}
    thread_id = _codex_app_server_request_thread_id(method, params)
    turn_id = _codex_app_server_request_turn_id(method, params)
    if thread_id and turn_id:
        _core._CODEX_APP_SERVER_TURN_THREAD[turn_id] = thread_id
    if not thread_id:
        return False
    if method not in _CODEX_APP_SERVER_APPROVAL_REQUEST_METHODS:
        return False

    _core._CODEX_APP_SERVER_EVENT_SEQ += 1
    now = time.time()
    state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(thread_id, {})
    state.update({
        "thread_id": thread_id,
        "event_seq": _core._CODEX_APP_SERVER_EVENT_SEQ,
        "last_event": method,
        "last_event_at": now,
        "last_activity_at": now,
        "status": "active",
        "thread_needs_approval": True,
    })
    if turn_id:
        state["last_turn_id"] = turn_id
        state["active_turn_id"] = turn_id
    flags = [str(flag) for flag in (state.get("active_flags") or []) if flag]
    if "waitingOnApproval" not in flags:
        flags.append("waitingOnApproval")
    state["active_flags"] = flags

    summary = _codex_app_server_approval_request_summary(request_id, method, params, now)
    if summary:
        active = state.setdefault("active_items", {})
        if not isinstance(active, dict):
            active = {}
            state["active_items"] = active
        item_key = summary.get("id") or summary.get("request_id")
        if item_key:
            active[item_key] = summary
        state["pending_approval_request"] = summary
        state["active_item"] = summary
        state["last_item"] = summary
    _core._save_codex_app_server_state_unlocked()
    try:
        _codex_telemetry_note_notification(method, params, thread_id, turn_id)
    except Exception:
        pass
    return True


def _codex_app_server_handle_server_request(request_id, method, params):
    if method in _CODEX_APP_SERVER_APPROVAL_REQUEST_METHODS:
        return _codex_app_server_record_server_request(request_id, method, params)
    return False


def _codex_app_server_activity_fields(session_id):
    routed = _core._control_plane_engine_call(
        "codex", "activity", {"session_id": session_id}, mutate=False,
    )
    if routed is not None and isinstance(routed.get("activity"), dict):
        return routed["activity"]
    fields = {
        "sidecar_status": None,
        "sidecar_has_writes": False,
        "sidecar_tool": None,
        "sidecar_file": None,
        "sidecar_ts": 0,
        "sidecar_in_flight": False,
        "needs_approval": False,
        "needs_approval_message": "",
    }
    state = _core._codex_app_server_thread_state(session_id)
    if not state:
        return fields
    approval_item = _codex_app_server_pending_approval_item(state)
    if approval_item:
        message = approval_item.get("approval_message") or approval_item.get("detail") or approval_item.get("output") or ""
        fields.update({
            "sidecar_status": "active",
            "sidecar_has_writes": False,
            "sidecar_tool": approval_item.get("tool") or "Approval",
            "sidecar_file": message,
            "sidecar_ts": approval_item.get("ts") or state.get("last_activity_at") or time.time(),
            "sidecar_in_flight": True,
            "needs_approval": True,
            "needs_approval_message": message or "Codex is waiting for approval",
        })
        return fields
    if _codex_app_server_thread_needs_approval(state):
        message = _codex_app_server_thread_approval_message(state)
        fields.update({
            "sidecar_status": "active",
            "sidecar_has_writes": False,
            "sidecar_tool": "Approval",
            "sidecar_file": message,
            "sidecar_ts": state.get("last_activity_at") or state.get("last_event_at") or time.time(),
            "sidecar_in_flight": True,
            "needs_approval": True,
            "needs_approval_message": message,
        })
        return fields
    recovery = state.get("compaction_recovery")
    recovery_status = str(
        recovery.get("status") if isinstance(recovery, dict) else ""
    )
    if recovery_status in ("interrupting", "recovering"):
        fields.update({
            "sidecar_status": "active",
            "sidecar_has_writes": False,
            "sidecar_tool": "Recovery",
            "sidecar_file": _codex_recovery_activity_text(recovery),
            "sidecar_ts": recovery.get("last_attempt_at") or state.get("last_activity_at") or time.time(),
            "sidecar_in_flight": True,
        })
        return fields
    status = str(state.get("status") or "").lower()
    active = bool(state.get("active_turn_id") or status == "active")
    if not active:
        return fields
    item = state.get("active_item") if isinstance(state.get("active_item"), dict) else None
    if item and item.get("tool") and item.get("tool") != "Thinking":
        fields.update({
            "sidecar_status": "active",
            "sidecar_has_writes": False,
            "sidecar_tool": item.get("tool"),
            "sidecar_file": item.get("detail") or item.get("output") or "",
            "sidecar_ts": item.get("ts") or state.get("last_activity_at") or time.time(),
            "sidecar_in_flight": bool(item.get("in_flight", True)),
        })
        return fields
    fields.update({
        "sidecar_status": "active",
        "sidecar_tool": "Thinking",
        "sidecar_file": (item or {}).get("detail") or "",
        "sidecar_ts": state.get("last_activity_at") or state.get("last_event_at") or time.time(),
        "sidecar_in_flight": True,
    })
    return fields


def _codex_app_server_handle_notification(method, params):
    if not isinstance(params, dict):
        params = {}
    thread = params.get("thread") if isinstance(params.get("thread"), dict) else None
    thread_id = _codex_notification_thread_id(method, params)
    turn_id = _codex_notification_turn_id(method, params)
    if thread_id and turn_id:
        _core._CODEX_APP_SERVER_TURN_THREAD[turn_id] = thread_id
    if thread and not thread_id and thread.get("id"):
        thread_id = str(thread["id"])
    if not thread_id:
        return
    pump_after_notification = False
    delivered_user_text = None
    _core._CODEX_APP_SERVER_EVENT_SEQ += 1
    now = time.time()
    state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(thread_id, {})
    state.update({
        "thread_id": thread_id,
        "event_seq": _core._CODEX_APP_SERVER_EVENT_SEQ,
        "last_event": method,
        "last_event_at": now,
    })
    if turn_id:
        state["last_turn_id"] = turn_id
    if thread:
        _core._codex_app_server_record_thread(thread_id, thread)
    if method == "thread/status/changed":
        status_fields = _codex_app_server_status_fields(params.get("status"))
        status = status_fields.get("status")
        if status:
            state["status"] = str(status)
            state["active_flags"] = status_fields.get("active_flags") or []
            state["thread_needs_approval"] = bool(status_fields.get("thread_needs_approval"))
            if str(status).lower() == "idle":
                pump_after_notification = True
                state.pop("active_turn_id", None)
                state.pop("active_writer", None)
                state.pop("active_item", None)
                state.pop("active_items", None)
                state["thread_needs_approval"] = False
                state["active_flags"] = []
    elif method == "thread/settings/updated":
        settings = params.get("threadSettings") or params.get("thread_settings")
        if isinstance(settings, dict):
            state["thread_settings"] = settings
    elif method == "turn/started":
        known_ccc_turn = bool(
            state.get("active_writer") == "ccc"
            and turn_id
            and str(state.get("active_turn_id") or "") == str(turn_id)
        )
        if turn_id:
            state["active_turn_id"] = turn_id
            state["last_activity_at"] = now
        # A notification without our transient start marker proves that a turn
        # is active, but not who started it. This commonly happens when CCC
        # reconnects after a restart while its own earlier turn is still
        # running. Treat unproven ownership as unknown; the writer gate still
        # serializes behind it.
        writer = "ccc" if state.get("ccc_turn_start_pending") or known_ccc_turn else "unknown"
        state["active_writer"] = writer
        state["active_items"] = {}
        state.pop("active_item", None)
        state["status"] = "active"
        _core._codex_coordination_event_unlocked(
            state,
            "ccc_turn_started" if writer == "ccc" else "external_turn_started",
            writer=writer,
            now=now,
        )
    elif method in (
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/commandExecution/outputDelta",
        "item/fileChange/patchUpdated",
        "item/fileChange/outputDelta",
        "item/mcpToolCall/progress",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
        "item/plan/delta",
        "thread/tokenUsage/updated",
    ):
        # A command can outlive its Codex turn and keep emitting output after
        # turn/completed. Those late deltas are process output, not evidence of
        # a new turn; accepting them resurrected idle threads as "Thinking".
        # A genuine new turn always arrives through turn/started first.
        late_completed_turn = bool(
            turn_id
            and str(state.get("last_completed_turn_id") or "") == str(turn_id)
            and not state.get("active_turn_id")
        )
        if not late_completed_turn:
            state["last_activity_at"] = now
            if turn_id:
                state["active_turn_id"] = turn_id
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or params.get("token_usage") or params.get("usage")
            if isinstance(usage, dict):
                state["token_usage"] = usage
        elif not late_completed_turn and method in (
            "item/started",
            "item/completed",
            "item/commandExecution/outputDelta",
            "item/fileChange/patchUpdated",
            "item/fileChange/outputDelta",
            "item/reasoning/summaryTextDelta",
            "item/reasoning/textDelta",
        ):
            _codex_app_server_record_item_notification(state, method, params, now)
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if method == "item/completed" and item.get("type") == "userMessage":
                delivered_user_text = str(item.get("text") or "").strip()
                if delivered_user_text:
                    state["last_delivered_user_text"] = delivered_user_text
                    state["last_delivered_user_turn_id"] = str(turn_id or "")
                    state["last_delivered_user_at"] = now
    elif method == "turn/completed":
        pump_after_notification = True
        completed_writer = str(state.get("active_writer") or "external")
        state["last_activity_at"] = now
        state["last_completed_turn_id"] = turn_id
        if not turn_id or state.get("active_turn_id") == turn_id:
            state.pop("active_turn_id", None)
            state.pop("active_writer", None)
        state.pop("active_item", None)
        state.pop("active_items", None)
        state["status"] = "idle"
        _core._codex_coordination_event_unlocked(
            state,
            "ccc_turn_completed" if completed_writer == "ccc" else "external_turn_completed",
            writer=completed_writer,
            now=now,
        )
    _codex_compaction_recovery_note_notification_unlocked(
        state, method, params, turn_id, now
    )
    _core._save_codex_app_server_state_unlocked()
    try:
        _codex_telemetry_note_notification(method, params, thread_id, turn_id)
    except Exception:
        pass
    if delivered_user_text:
        # The app-server's userMessage item is the authoritative delivery ack.
        # Reconcile any durable copy left queued by an unconfirmed/phantom-owner
        # attempt; duplicate prompts remain valid because only one copy is
        # consumed.
        _core._consume_matching_pending_input(thread_id, delivered_user_text)
    if pump_after_notification:
        _core._schedule_codex_queue_pump(thread_id)


def _codex_app_server_handle_message(payload):
    if not isinstance(payload, dict):
        return
    global _CODEX_APP_SERVER_LAST_MSG_AT
    with _core._CODEX_APP_SERVER_LOCK:
        _CODEX_APP_SERVER_LAST_MSG_AT = time.time()
        method = payload.get("method")
        if "id" in payload and method:
            _core._app_server_trace("msg-server-req", id=payload.get("id"), method=method)
            _codex_app_server_handle_server_request(
                payload.get("id"),
                str(method),
                payload.get("params") or {},
            )
            _core._CODEX_APP_SERVER_LOCK.notify_all()
            return
        if "id" in payload:
            result = payload.get("result")
            if isinstance(result, dict):
                thread = result.get("thread")
                if isinstance(thread, dict) and thread.get("id"):
                    _core._codex_app_server_record_thread(str(thread["id"]), thread)
                    _core._save_codex_app_server_state_unlocked()
            orphaned = _CODEX_APP_SERVER_ORPHANED_WAITERS.pop(payload.get("id"), None)
            _core._app_server_trace(
                "msg-response", id=payload.get("id"),
                has_result=isinstance(payload.get("result"), dict),
                has_error="error" in payload,
                orphaned=orphaned is not None,
            )
            _core._CODEX_APP_SERVER_RESPONSES[payload.get("id")] = payload
            _core._CODEX_APP_SERVER_LOCK.notify_all()
            if orphaned is not None:
                # The waiter already gave up and logged TIMEOUT; this reply
                # arriving now proves the channel still works, just slower
                # than the timeout. Rare path, so logging under the lock is
                # acceptable here (open+append only; the mkdir is cached).
                o_method, o_timed_out_at, o_timeout, o_inflight = orphaned
                _core._log_activity(
                    "app-server", "LATE",
                    f"method={o_method} reply arrived "
                    f"{round(time.time() - o_timed_out_at, 1)}s after its "
                    f"{o_timeout}s timeout ({'real' if o_inflight else 'probe'})",
                )
            return
        if method:
            _core._app_server_trace("msg-notification", method=method)
            _core._codex_app_server_handle_notification(str(method), payload.get("params") or {})
            _core._CODEX_APP_SERVER_LOCK.notify_all()


def _codex_app_server_thread_state(session_id):
    if not session_id:
        return {}
    with _core._CODEX_APP_SERVER_LOCK:
        return dict(_core._CODEX_APP_SERVER_THREAD_STATE.get(session_id) or {})


def _codex_app_server_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _codex_app_server_pick_int(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        value = _codex_app_server_int(obj.get(key))
        if value is not None:
            return value
    return None


def _codex_app_server_token_breakdown_public(raw):
    if not isinstance(raw, dict):
        raw = {}
    input_tokens = _codex_app_server_pick_int(raw, "input_tokens", "inputTokens", "input")
    cached_input_tokens = _codex_app_server_pick_int(
        raw,
        "cached_input_tokens",
        "cachedInputTokens",
        "cache_read_input_tokens",
        "cacheReadInputTokens",
        "cached",
    )
    output_tokens = _codex_app_server_pick_int(raw, "output_tokens", "outputTokens", "output")
    reasoning_output_tokens = _codex_app_server_pick_int(
        raw,
        "reasoning_output_tokens",
        "reasoningOutputTokens",
        "reasoning",
    )
    total_tokens = _codex_app_server_pick_int(raw, "total_tokens", "totalTokens", "total")
    if total_tokens is None:
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return {
        "input_tokens": input_tokens or 0,
        "cached_input_tokens": cached_input_tokens or 0,
        "output_tokens": output_tokens or 0,
        "reasoning_output_tokens": reasoning_output_tokens or 0,
        "total_tokens": total_tokens or 0,
    }


def _codex_app_server_token_usage_public(raw):
    if not isinstance(raw, dict) or not raw:
        return None
    last_raw = raw.get("last") or raw.get("last_token_usage") or raw.get("lastTokenUsage")
    total_raw = raw.get("total") or raw.get("total_token_usage") or raw.get("totalTokenUsage")
    if not isinstance(last_raw, dict):
        last_raw = {}
    if not isinstance(total_raw, dict):
        total_raw = raw
    last = _codex_app_server_token_breakdown_public(last_raw)
    total = _codex_app_server_token_breakdown_public(total_raw)
    context_limit = _codex_app_server_pick_int(
        raw,
        "modelContextWindow",
        "model_context_window",
        "context_limit",
        "contextLimit",
    )
    used_percent = None
    if context_limit and context_limit > 0:
        used_percent = round((total.get("total_tokens") or 0) * 100.0 / context_limit, 1)
    public = dict(total)
    public.update({
        "context_limit": context_limit or 0,
        "used_percent": used_percent,
        "last": last,
        "total": total,
    })
    return public


def _codex_app_server_item_public(item):
    if not isinstance(item, dict) or not item:
        return None
    public = {}
    for key in (
        "id",
        "type",
        "tool",
        "detail",
        "status",
        "command",
        "output",
        "approval_message",
        "request_id",
        "approval_method",
        "approval_id",
        "turn_id",
        "item_id",
    ):
        value = item.get(key)
        if value not in (None, "", [], {}):
            public[key] = value
    for key in ("in_flight", "needs_approval", "is_error", "can_approve"):
        if key in item:
            public[key] = bool(item.get(key))
    available = item.get("available_decisions")
    if isinstance(available, list):
        public["available_decisions"] = available
    for key in ("ts", "updated_at"):
        try:
            public[key] = float(item.get(key) or 0)
        except (TypeError, ValueError):
            public[key] = 0
    return public or None


def _codex_app_server_active_item_public(state):
    if not isinstance(state, dict) or not state:
        return None
    approval_item = _codex_app_server_pending_approval_item(state)
    if approval_item:
        return _codex_app_server_item_public(approval_item)
    active_item = state.get("active_item") if isinstance(state.get("active_item"), dict) else None
    if active_item:
        return _codex_app_server_item_public(active_item)
    latest = _codex_app_server_latest_active_item(state)
    if latest:
        return _codex_app_server_item_public(latest)
    status = str(state.get("status") or "").lower()
    if state.get("active_turn_id") or status == "active":
        ts = state.get("last_activity_at") or state.get("last_event_at") or time.time()
        return {
            "id": "",
            "type": "reasoning",
            "tool": "Thinking",
            "detail": "",
            "status": "inProgress",
            "in_flight": True,
            "needs_approval": False,
            "ts": float(ts or 0),
            "updated_at": float(ts or 0),
        }
    return None


def _codex_app_server_thread_public_status(session_id):
    """Small status payload for UI polling; no transcript/file reads."""
    routed = _core._control_plane_engine_call(
        "codex", "status", {"session_id": session_id}, mutate=False,
    )
    if routed is not None:
        return routed.get("status") or {}
    state = _core._codex_app_server_thread_state(session_id)
    if not state:
        return {
            "event_seq": 0,
            "last_activity_at": 0,
            "last_event_at": 0,
            "last_item_id": "",
            "active_item": None,
            "token_usage": None,
            "compaction_recovery": None,
        }
    last_item = state.get("last_item") if isinstance(state.get("last_item"), dict) else {}
    active_item = _codex_app_server_active_item_public(state)
    item = active_item or last_item
    return {
        "event_seq": int(state.get("event_seq") or 0),
        "last_activity_at": float(state.get("last_activity_at") or 0),
        "last_event_at": float(state.get("last_event_at") or 0),
        "last_item_id": str((item or {}).get("id") or ""),
        "active_item": active_item,
        "token_usage": _codex_app_server_token_usage_public(state.get("token_usage")),
        "compaction_recovery": (
            dict(state.get("compaction_recovery"))
            if isinstance(state.get("compaction_recovery"), dict)
            else None
        ),
    }


def _codex_app_server_reader(transport):
    """Collect JSON-RPC responses and notifications from Codex app-server."""
    _core._app_server_trace("reader-start", kind=transport.kind,
                      child_pid=getattr(transport.proc, "pid", None))
    exit_reason = "eof"
    try:
        if transport.kind == "stdio":
            for line in transport.proc.stdout:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    _core._app_server_trace("reader-badjson", text=line[:120])
                    continue
                try:
                    _core._codex_app_server_handle_message(payload)
                except Exception as e:
                    # Traced, then re-raised: killing the reader on one bad
                    # message is the PRE-EXISTING behavior -- preserve it, but
                    # stop letting it happen silently.
                    _core._app_server_trace("reader-msg-error", error=repr(e))
                    raise
        else:
            while True:
                try:
                    message = _websocket_recv_text(transport.sock)
                except OSError:
                    break
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                try:
                    _core._codex_app_server_handle_message(payload)
                except Exception as e:
                    _core._app_server_trace("reader-msg-error", error=repr(e))
                    raise
    except Exception as e:
        exit_reason = f"error:{e!r}"
        raise
    finally:
        _core._app_server_trace("reader-exit", reason=exit_reason,
                          child_pid=getattr(transport.proc, "pid", None))
        with _core._CODEX_APP_SERVER_LOCK:
            if _core._CODEX_APP_SERVER_TRANSPORT is transport:
                _core._CODEX_APP_SERVER_TRANSPORT = None
                _core._CODEX_APP_SERVER_PROC = None
                _core._CODEX_APP_SERVER_INITIALIZED = False
                _core._CODEX_APP_SERVER_INITIALIZING = False
            _core._CODEX_APP_SERVER_LOCK.notify_all()


def _codex_app_server_request_to_transport(
    transport, method, params=None, timeout=20, count_as_inflight=True,
):
    """Send one JSON-RPC request to an already-started Codex app-server.

    `count_as_inflight` marks this call in `_CODEX_APP_SERVER_INFLIGHT` for the
    whole span it's waiting on a reply. The periodic liveness probe (see
    _codex_app_server_transport_responsive) passes False so it never counts
    itself -- it uses this counter to tell "busy servicing a real request"
    apart from "actually wedged".
    """
    global _CODEX_APP_SERVER_NEXT_ID
    if count_as_inflight:
        with _core._CODEX_APP_SERVER_INFLIGHT_LOCK:
            _core._CODEX_APP_SERVER_INFLIGHT += 1
    req_id = None
    send_error = None
    timed_out = None
    sent_at = None
    try:
        with _core._CODEX_APP_SERVER_LOCK:
            req_id = _CODEX_APP_SERVER_NEXT_ID
            _CODEX_APP_SERVER_NEXT_ID += 1
            try:
                transport.send_json({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": method,
                    "params": params or {},
                })
                sent_at = time.time()
                _core._app_server_trace(
                    "send", id=req_id, method=method, timeout=timeout,
                    kind="real" if count_as_inflight else "probe",
                    child_pid=getattr(transport.proc, "pid", None),
                )
            except (BrokenPipeError, OSError) as e:
                send_error = e
            if send_error is None:
                deadline = time.time() + timeout
                while time.time() < deadline:
                    response = _core._CODEX_APP_SERVER_RESPONSES.pop(req_id, None)
                    if response is not None:
                        _core._app_server_trace(
                            "resp-ok", id=req_id, method=method,
                            elapsed=round(time.time() - sent_at, 3),
                        )
                        return response
                    remaining = max(0.05, deadline - time.time())
                    _core._CODEX_APP_SERVER_LOCK.wait(min(0.5, remaining))
                # Register before releasing the lock so a reply racing in right
                # now is still caught by the reader's LATE check below.
                if len(_CODEX_APP_SERVER_ORPHANED_WAITERS) < 256:
                    _CODEX_APP_SERVER_ORPHANED_WAITERS[req_id] = (
                        method, time.time(), timeout, count_as_inflight,
                    )
                timed_out = (method, req_id, timeout, count_as_inflight)
        if send_error is not None:
            # The liveness probe failing HERE (not at the wait deadline) is
            # the current prime suspect in the liveness-miss investigation:
            # the child is alive but writing to its stdin fails. Log the
            # exact errno instead of guessing.
            _core._app_server_trace("send-fail", id=req_id, method=method, error=repr(send_error))
            _core._log_activity(
                "app-server", "SENDFAIL",
                f"method={method} id={req_id} send failed: {send_error!r}",
            )
            return {"ok": False, "error": str(send_error), "fallback": "exec"}
        if timed_out is not None:
            _core._app_server_trace(
                "wait-timeout", id=req_id, method=method,
                elapsed=round(time.time() - sent_at, 3),
                kind="real" if count_as_inflight else "probe",
            )
            _core._log_activity(
                "app-server", "TIMEOUT",
                f"method={timed_out[0]} id={timed_out[1]} no reply within {timed_out[2]}s "
                f"({'real' if timed_out[3] else 'probe'}); watching for late arrival",
            )
            return {
                "ok": False,
                "error": f"Codex app-server request timed out: {method}",
                "fallback": "exec",
            }
    finally:
        if count_as_inflight:
            with _core._CODEX_APP_SERVER_INFLIGHT_LOCK:
                _core._CODEX_APP_SERVER_INFLIGHT -= 1


def _codex_context_window_args():
    """Opt every Codex invocation into the model's full context window.

    Codex CLAMPS `model_context_window` to the model's advertised
    `max_context_window`, so passing 1M is a safe "give me the max" request, not
    a foot-gun — it is silently capped per model:
      - gpt-5.4: 272K default, 1M max -> reports ~950K effective context.
      - gpt-5.5: 272K max -> clamped to ~258K after reserved output/system
        tokens. CCC still defaults Codex spawns to gpt-5.5 because it is the
        stronger general model; choose gpt-5.4 explicitly when the larger
        window matters more than the 5.5 model quality.
    Passed as a global `-c` override on spawn, resume, and the app-server so the
    window is consistent across a session's whole life. Set CCC_CODEX_CONTEXT_1M=0
    to fall back to the model default.
    """
    if not _core._codex_context_1m_enabled():
        return []
    return ["-c", "model_context_window=1000000"]


_CODEX_APP_SERVER_FALSE_MISS_LIMIT = 3
# Thread-level methods whose "thread not found" can be checked against disk.
_CODEX_APP_SERVER_FALSE_MISS_METHODS = {
    "thread/resume",
    "turn/start",
    "turn/steer",
    "turn/interrupt",
    "thread/name/set",
    "thread/settings/update",
    "thread/compact/start",
}


def _codex_rollout_exists_on_disk(thread_id):
    """True when a rollout JSONL for `thread_id` exists under ~/.codex/sessions.

    Registry-independent on purpose: a wedged app-server may also have lost
    the registry row, and this check is the ground truth for 'the thread
    exists, the server just can't see it'.
    """
    sid = str(thread_id or "").strip()
    if not sid:
        return False
    try:
        root = Path.home() / ".codex" / "sessions"
        if not root.is_dir():
            return False
        return any(root.rglob(f"*{sid}.jsonl"))
    except OSError:
        return False


def _codex_app_server_track_thread_health(method, thread_id, response):
    """Recycle a wedged app-server child on verified false 'thread not found'.

    The liveness probe only proves the JSON-RPC loop still answers
    `thread/list` — a half-dead child can fail every thread-level call for
    hours while staying 'warm' (observed: ~10h of continuous thread/resume
    and turn/start failures). 'thread not found' for a thread whose rollout
    exists on disk is a definitive false miss: the server lost track of
    durable state. After _CODEX_APP_SERVER_FALSE_MISS_LIMIT consecutive
    verified misses, recycle the child so the next request respawns a fresh
    app-server. Genuine misses (deleted/archived threads, brand-new threads
    whose rollout hasn't landed yet) are neutral — no strike, no reset.
    """
    if _core._codex_response_succeeded(response):
        if method in _CODEX_APP_SERVER_FALSE_MISS_METHODS:
            _core._CODEX_APP_SERVER_FALSE_MISSES = 0
        return
    if method not in _CODEX_APP_SERVER_FALSE_MISS_METHODS or not thread_id:
        return
    if "thread not found" not in _codex_error_text(response).lower():
        return
    if not _core._codex_rollout_exists_on_disk(thread_id):
        return
    _core._CODEX_APP_SERVER_FALSE_MISSES += 1
    if _core._CODEX_APP_SERVER_FALSE_MISSES >= _CODEX_APP_SERVER_FALSE_MISS_LIMIT:
        _core._CODEX_APP_SERVER_FALSE_MISSES = 0
        _core._log_activity(
            "app-server",
            "RECYCLE",
            f"{_CODEX_APP_SERVER_FALSE_MISS_LIMIT} verified"
            " false 'thread not found' misses with rollouts on disk;"
            " recycling app-server child",
        )
        _core._codex_app_server_shutdown()


def _codex_app_server_request(method, params=None, timeout=20):
    """Send one JSON-RPC request to Codex app-server.

    The app-server is the only local Codex interface that can append input to
    a loaded thread; `codex exec resume` can only start a one-shot process.
    """
    params = params or {}
    mutating = method in {
        "turn/start",
        "turn/steer",
        "turn/interrupt",
        "thread/start",
        "thread/name/set",
        "thread/settings/update",
        "thread/compact/start",
        "thread/goal/set",
        "thread/goal/clear",
        "item/tool/call/approve",
        "item/file/change/approve",
        "item/command/execution/approve",
    }
    routed = _core._control_plane_engine_call(
        "codex", "rpc", {
            "method": method,
            "params": params,
            "timeout": timeout,
        },
        mutate=mutating,
        idempotency_key=(
            _core._take_control_plane_action_id() if mutating else None
        ),
    )
    if routed is not None:
        response = routed.get("response")
        if isinstance(response, dict):
            return response
        return {
            "ok": False,
            "error": routed.get("error") or "Codex worker returned no response",
            "fallback": "exec",
        }
    thread_id = _codex_app_server_request_thread_id(method, params)
    if method == "turn/start" and thread_id:
        with _core._CODEX_APP_SERVER_LOCK:
            state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(thread_id, {})
            state["ccc_turn_start_pending"] = True
            state["ccc_turn_start_pending_at"] = time.time()
    transport = _core._ensure_codex_app_server()
    if transport is None:
        if method == "turn/start" and thread_id:
            with _core._CODEX_APP_SERVER_LOCK:
                state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(thread_id, {})
                state.pop("ccc_turn_start_pending", None)
                state.pop("ccc_turn_start_pending_at", None)
        error = "Codex app-server is unavailable"
        conflict = _core._codex_shared_state_conflict()
        if conflict:
            error = conflict["message"]
        return {
            "ok": False,
            "error": error,
            "fallback": "exec",
        }
    response = _core._codex_app_server_request_to_transport(
        transport, method, params=params, timeout=timeout,
    )
    try:
        _codex_app_server_track_thread_health(method, thread_id, response)
    except Exception:
        pass  # health tracking must never break the request path
    if method == "turn/start" and thread_id:
        with _core._CODEX_APP_SERVER_LOCK:
            state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(thread_id, {})
            state.pop("ccc_turn_start_pending", None)
            state.pop("ccc_turn_start_pending_at", None)
            if _core._codex_response_succeeded(response):
                turn = ((response.get("result") or {}).get("turn") or {})
                turn_id = str(turn.get("id") or "").strip()
                if turn_id:
                    state["active_turn_id"] = turn_id
                    state["active_writer"] = "ccc"
                    _core._CODEX_APP_SERVER_TURN_THREAD[turn_id] = thread_id
    return response


def _codex_app_server_refresh_thread_status(session_id, *, max_age=2.0):
    """Refresh app-server thread status flags from `thread/list`.

    Some approval blockers only appear as thread-level `activeFlags` such as
    `waitingOnApproval`; no item/started notification is emitted. Refreshing the
    compact thread list lets CCC match Codex mobile/desktop status without
    loading the full rollout or starting a turn.
    """
    if not session_id:
        return False
    # Decide whether *this* caller owns the next call. One reply updates every
    # thread, so a refresh another caller just made (or is making right now)
    # is exactly as good as one we would issue ourselves -- and a duplicate
    # would only lengthen the shared channel's queue.
    with _CODEX_THREAD_LIST_COND:
        while True:
            if max_age > 0 and (time.time() - _core._CODEX_THREAD_LIST_LAST_AT) < max_age:
                # Someone refreshed inside our freshness window; reuse it.
                return _codex_app_server_thread_is_known(session_id)
            if _core._CODEX_THREAD_LIST_INFLIGHT:
                # A call is already on the wire. Wait for it rather than
                # queueing a second one behind it. Forced refreshes (max_age=0)
                # re-evaluate afterwards and then issue their own, so
                # grab-back still observes post-interrupt state.
                if not _CODEX_THREAD_LIST_COND.wait(_CODEX_THREAD_LIST_WAIT_TIMEOUT):
                    # Waited out the in-flight call without being notified --
                    # treat it as stuck and fall through to our own attempt.
                    break
                continue
            break
        _core._CODEX_THREAD_LIST_INFLIGHT = True
    try:
        response = _core._codex_app_server_request("thread/list", {}, timeout=3)
    except Exception:
        return False
    finally:
        with _CODEX_THREAD_LIST_COND:
            _core._CODEX_THREAD_LIST_INFLIGHT = False
            # Stamp on completion, not on entry: the throttle window should
            # start when data actually landed.
            _core._CODEX_THREAD_LIST_LAST_AT = time.time()
            _CODEX_THREAD_LIST_COND.notify_all()
    data = ((response or {}).get("result") or {}).get("data")
    if not isinstance(data, list):
        return False
    changed = False
    with _core._CODEX_APP_SERVER_LOCK:
        for thread in data:
            if not isinstance(thread, dict) or not thread.get("id"):
                continue
            _core._codex_app_server_record_thread(str(thread["id"]), thread)
            if str(thread["id"]) == str(session_id):
                changed = True
        if changed:
            _core._save_codex_app_server_state_unlocked()
    return changed


def _codex_app_server_thread_is_known(session_id):
    """Did the last `thread/list` refresh carry this thread?

    Mirrors the return contract of _codex_app_server_refresh_thread_status for
    callers served out of the shared refresh instead of their own RPC.
    """
    with _core._CODEX_APP_SERVER_LOCK:
        return str(session_id) in _core._CODEX_APP_SERVER_THREAD_STATE


def _codex_app_server_transport_responsive(transport, timeout=_CODEX_APP_SERVER_LIVENESS_TIMEOUT):
    """Round-trip probe distinguishing "process alive" from "process answers".

    A wedged app-server (e.g. leaking pipe fds until it can no longer service
    requests) still passes transport.alive() forever since that only polls the
    OS process, so callers would keep reusing a transport that never replies.
    A real reply carries "result" (or a protocol-level "error"); the synthetic
    timeout/broken-pipe dicts from _codex_app_server_request_to_transport
    always carry "fallback" instead. NOTE: do NOT test for the "jsonrpc"
    envelope key here -- this app-server omits it from responses, and checking
    for it made every probe "fail" on a healthy server (the root cause of the
    long liveness-miss investigation, docs/HANDOFF_codex_appserver_liveness.md).
    """
    probe_started = time.time()
    _core._app_server_trace(
        "probe-begin", timeout=timeout,
        child_pid=getattr(transport.proc, "pid", None),
    )
    response = _core._codex_app_server_request_to_transport(
        transport, "thread/list", {}, timeout=timeout, count_as_inflight=False,
    )
    ok = isinstance(response, dict) and (
        "result" in response
        or ("error" in response and "fallback" not in response)
    )
    _core._app_server_trace(
        "probe-end", ok=ok, elapsed=round(time.time() - probe_started, 3),
        response=None if ok else str(response)[:140],
    )
    if not ok:
        # MISS has been observed in activity.log with NEITHER a TIMEOUT nor a
        # SENDFAIL line preceding it, which the request_to_transport code says
        # is impossible -- so log the raw probe return value and let evidence
        # resolve the contradiction.
        _core._log_activity(
            "app-server", "PROBEFAIL",
            f"probe returned: {str(response)[:140]}",
        )
    return ok


def _codex_app_server_reap_stray_children():
    """Kill any stdio app-server child not currently tracked as ours.

    _CodexAppServerTransport.close() asks (SIGTERM), waits, escalates
    (SIGKILL), and waits again -- but if that final wait still times out
    (e.g. the child is stuck in uninterruptible sleep under memory
    pressure), close() gives up silently and the caller spawns a
    replacement on top of it. Python's only handle to the old process is
    gone at that point, but the OS-level child is still alive and still
    parented to us. Left unchecked this compounds: each stuck stray adds
    to memory pressure, making the next replacement more likely to stall
    too. Call this right before spawning a new stdio app-server so at
    most one ever survives as our child, regardless of why a prior
    close() failed to reap its predecessor.
    """
    tracked_pid = _core._CODEX_APP_SERVER_PROC.pid if _core._CODEX_APP_SERVER_PROC is not None else None
    my_pid = os.getpid()
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,ppid,command"], text=True)
    except (OSError, subprocess.CalledProcessError):
        return
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if ppid != my_pid or pid == tracked_pid:
            continue
        cmd = parts[2]
        if "app-server" not in cmd or "--listen" not in cmd or "stdio://" not in cmd:
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        else:
            _core._log_activity(
                "app-server", "REAP",
                f"pid={pid} stray app-server child not tracked as ours; SIGTERM sent",
            )


def _codex_app_server_dump_stacks_on_liveness_miss(reason):
    """Capture every thread's current frame when a liveness check misses.

    Reuses the existing SIGUSR2 stack-dump handler (see
    _install_python_stack_dump_handler) instead of duplicating dump logic --
    this just writes a locating marker into the same python-stacks.log and
    then raises the signal against our own process so faulthandler produces
    an all-thread traceback right at the moment of the miss. That's the
    evidence needed to identify which thread was actually holding the GIL
    (or otherwise busy) when the app-server reader thread should have been
    running, rather than guessing at a cause.
    """
    sigusr2 = getattr(signal, "SIGUSR2", None)
    if sigusr2 is None or _core._PYTHON_STACK_DUMP_FILE is None:
        return
    try:
        _core._PYTHON_STACK_DUMP_FILE.write(
            f"\n=== app-server liveness miss ({reason}) pid={os.getpid()} "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC ===\n"
        )
        _core._PYTHON_STACK_DUMP_FILE.flush()
        os.kill(os.getpid(), sigusr2)
    except OSError:
        pass


def _codex_app_server_active_turn_fresh(now):
    """True if a tracked thread has a turn that recently showed activity.

    A running turn keeps the app-server busy SERVER-side with zero client-side
    in-flight requests (turn/start returns as soon as the turn is accepted),
    so the in-flight counter alone cannot distinguish "healthy but mid-turn"
    from "wedged". Live turns emit a steady stream of notifications, which
    the reader folds into last_activity_at -- so a turn whose activity has
    gone stale does NOT count here, which keeps a genuinely wedged server
    replaceable. Caller must hold _CODEX_APP_SERVER_LOCK.
    """
    for state in _core._CODEX_APP_SERVER_THREAD_STATE.values():
        if not isinstance(state, dict):
            continue
        if not (
            state.get("active_turn_id")
            or str(state.get("status") or "").lower() == "active"
        ):
            continue
        try:
            last = float(
                state.get("last_activity_at") or state.get("last_event_at") or 0.0
            )
        except (TypeError, ValueError):
            last = 0.0
        if now - last < _CODEX_APP_SERVER_TURN_BUSY_GRACE_S:
            return True
    return False


def _ensure_codex_app_server(*, allow_stdio=True):
    """Start and initialize a persistent Codex app-server if needed."""
    global _CODEX_APP_SERVER_READER
    # _log_activity and the stack-dump marker both do file I/O, and any
    # syscall can stall for seconds under memory pressure. Holding
    # _CODEX_APP_SERVER_LOCK across that I/O starves the reader thread that
    # records app-server replies -- which itself causes liveness misses
    # (see docs/HANDOFF_codex_appserver_liveness.md). So inside the lock we
    # only *decide* what to log; the actual logging happens right after the
    # lock is released.
    pending_log = None
    pending_dump_reason = None
    keep_transport = None
    with _core._CODEX_APP_SERVER_LOCK:
        while _core._CODEX_APP_SERVER_INITIALIZING:
            _core._CODEX_APP_SERVER_LOCK.wait(0.5)
            transport = _core._CODEX_APP_SERVER_TRANSPORT
            if transport is not None and transport.alive() and _core._CODEX_APP_SERVER_INITIALIZED:
                return transport
            if not _core._CODEX_APP_SERVER_INITIALIZING:
                break
        transport = _core._CODEX_APP_SERVER_TRANSPORT
        if transport is not None and transport.alive() and _core._CODEX_APP_SERVER_INITIALIZED:
            now = time.time()
            if now - _core._CODEX_APP_SERVER_LAST_LIVE_CHECK < _CODEX_APP_SERVER_LIVENESS_INTERVAL:
                return transport
            # Any recent traffic (a notification or a response) already proves
            # the connection works in both directions -- skip the probe. During
            # an active turn this is what keeps the probe from queueing behind
            # turn work on the in-order channel and "missing" on a healthy
            # server, which used to tear down live turns every WEDGED cycle.
            if now - _CODEX_APP_SERVER_LAST_MSG_AT < _CODEX_APP_SERVER_LIVENESS_INTERVAL:
                _core._CODEX_APP_SERVER_LAST_LIVE_CHECK = now
                transport.consecutive_liveness_misses = 0
                _core._app_server_trace(
                    "ensure-decision", decision="skip-traffic",
                    child_pid=getattr(transport.proc, "pid", None),
                    last_msg_age=round(now - _CODEX_APP_SERVER_LAST_MSG_AT, 2),
                )
                return transport
            if _core._codex_app_server_transport_responsive(transport):
                _core._CODEX_APP_SERVER_LAST_LIVE_CHECK = now
                transport.consecutive_liveness_misses = 0
                return transport
            with _core._CODEX_APP_SERVER_INFLIGHT_LOCK:
                inflight = _core._CODEX_APP_SERVER_INFLIGHT
            if inflight > 0:
                # A real request (e.g. an active turn) is already in flight on
                # this transport, so a slow reply to our liveness probe means
                # "busy", not "wedged" -- Codex app-server services requests on
                # one stdio connection largely in order, so a long-running
                # turn naturally delays an unrelated thread/list probe.
                # Replacing the process here would kill a perfectly healthy,
                # working session for no reason, and would repeat every
                # _CODEX_APP_SERVER_LIVENESS_INTERVAL seconds until the turn
                # finishes. Defer instead of tearing it down.
                _core._CODEX_APP_SERVER_LAST_LIVE_CHECK = now
                pending_log = (
                    "app-server", "BUSY",
                    f"pid={getattr(transport.proc, 'pid', '-')} "
                    f"age={round(now - transport.started_at)}s no reply within "
                    f"{_CODEX_APP_SERVER_LIVENESS_TIMEOUT}s but {inflight} request(s) "
                    f"in flight; not replacing",
                )
                keep_transport = transport
            elif _codex_app_server_active_turn_fresh(now):
                # No client-side request is outstanding, but a tracked turn
                # is still running server-side (turn/start returns as soon as
                # the turn is accepted, so a running turn shows ZERO in-flight
                # requests). The probe just queued behind that turn's work on
                # the in-order channel. Tearing down here kills the live turn
                # -- which is exactly what the pre-fix WEDGED churn did every
                # ~45s. A turn whose activity goes stale (see
                # _CODEX_APP_SERVER_TURN_BUSY_GRACE_S) no longer shields the
                # server, so a genuinely wedged process is still replaced.
                _core._CODEX_APP_SERVER_LAST_LIVE_CHECK = now
                pending_log = (
                    "app-server", "BUSY",
                    f"pid={getattr(transport.proc, 'pid', '-')} "
                    f"age={round(now - transport.started_at)}s no reply within "
                    f"{_CODEX_APP_SERVER_LIVENESS_TIMEOUT}s but turn(s) active "
                    f"server-side; not replacing",
                )
                keep_transport = transport
            else:
                transport.consecutive_liveness_misses += 1
                # A miss here means CCC didn't observe a reply within the
                # timeout on an idle connection (no in-flight requests, no
                # active turn, no recent traffic). A freshly-spawned app-server
                # answers thread/list in well under half a second, so this is
                # genuine evidence of trouble -- but capture what every thread
                # was doing at the moment of the miss anyway, so the actual
                # cause can be read from evidence instead of guessed.
                pending_dump_reason = (
                    "wedge-threshold"
                    if transport.consecutive_liveness_misses >= _CODEX_APP_SERVER_LIVENESS_MISS_THRESHOLD
                    else "miss"
                )
                if transport.consecutive_liveness_misses < _CODEX_APP_SERVER_LIVENESS_MISS_THRESHOLD:
                    # One missed reply on an otherwise-idle transport isn't proof
                    # the process is dead -- tearing down + respawning a healthy
                    # process is itself expensive (subprocess spawn, ps scan,
                    # handshake), so require a couple of consecutive misses
                    # before concluding that. See the dumped stacks above for
                    # what was actually happening in this process at the time.
                    _core._CODEX_APP_SERVER_LAST_LIVE_CHECK = now
                    pending_log = (
                        "app-server", "MISS",
                        f"pid={getattr(transport.proc, 'pid', '-')} "
                        f"age={round(now - transport.started_at)}s no reply observed within "
                        f"{_CODEX_APP_SERVER_LIVENESS_TIMEOUT}s "
                        f"({transport.consecutive_liveness_misses}/"
                        f"{_CODEX_APP_SERVER_LIVENESS_MISS_THRESHOLD}); giving it another cycle "
                        f"(stacks -> python-stacks.log)",
                    )
                    keep_transport = transport
                else:
                    # No reply observed after repeated consecutive checks — fall
                    # through to close it below so the candidates loop starts a
                    # fresh process instead of every caller queuing against a
                    # dead-end transport.
                    pending_log = (
                        "app-server", "WEDGED",
                        f"pid={getattr(transport.proc, 'pid', '-')} "
                        f"age={round(now - transport.started_at)}s no reply observed within "
                        f"{_CODEX_APP_SERVER_LIVENESS_TIMEOUT}s after "
                        f"{transport.consecutive_liveness_misses} consecutive misses; replacing "
                        f"(stacks -> python-stacks.log)",
                    )
        elif transport is not None and not transport.alive():
            pending_log = (
                "app-server", "DEAD",
                f"pid={getattr(transport.proc, 'pid', '-')} "
                f"age={round(time.time() - transport.started_at)}s exited on its own; replacing",
            )
        if keep_transport is None:
            if transport is not None:
                transport.close()
            _core._CODEX_APP_SERVER_PROC = None
            _core._CODEX_APP_SERVER_TRANSPORT = None
            _core._CODEX_APP_SERVER_INITIALIZED = False
            _core._CODEX_APP_SERVER_INITIALIZING = True
            _core._CODEX_APP_SERVER_LAST_LIVE_CHECK = 0.0

    if pending_dump_reason is not None:
        _core._codex_app_server_dump_stacks_on_liveness_miss(pending_dump_reason)
    if pending_log is not None:
        _core._app_server_trace(
            "ensure-decision", decision=pending_log[1],
            child_pid=getattr(transport.proc, "pid", None) if transport else None,
            detail=str(pending_log[2])[:160],
        )
        _core._log_activity(*pending_log)
    if keep_transport is not None:
        return keep_transport

    candidates = []
    managed_path = _core._codex_managed_app_server_socket_path()
    if _core._codex_managed_app_server_enabled() and managed_path.exists():
        candidates.append(("managed-unix", managed_path))
    if allow_stdio:
        conflict = _core._codex_shared_state_conflict()
        if conflict is None:
            candidates.append(("stdio", None))
        else:
            _core._app_server_trace(
                "shared-state-block",
                reason="foreign codex process holds shared state db",
                conflict=conflict["summary"],
            )
            _core._log_activity(
                "codex",
                "SHARED_STATE_BLOCK",
                f"private stdio app-server blocked: {conflict['summary']}",
            )

    for kind, arg in candidates:
        transport = None
        proc = None
        if kind == "managed-unix":
            try:
                transport = _core._connect_codex_managed_app_server(arg)
            except (OSError, socket.timeout):
                transport = None
        else:
            resolved = _core._resolve_codex_bin()
            if not resolved.get("available"):
                continue
            _core._codex_app_server_reap_stray_children()
            # Capture the child's stderr instead of devnull: if the Rust
            # side's stdio listener panics or closes, the only evidence is
            # on stderr (liveness-miss investigation -- the probe currently
            # fails while the child process stays alive).
            try:
                stderr_log = open(
                    _core.ACTIVITY_LOG_FILE.with_name("codex-app-server-stderr.log"),
                    "a", encoding="utf-8",
                )
            except OSError:
                stderr_log = subprocess.DEVNULL
            try:
                proc = subprocess.Popen(
                    [resolved["bin"], *_codex_context_window_args(), "app-server", "--listen", "stdio://"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr_log,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
                transport = _core._CodexAppServerTransport("stdio", proc=proc)
            except (FileNotFoundError, OSError):
                transport = None
            finally:
                # Popen dup'd the fd into the child; the parent's copy is
                # no longer needed and would leak one fd per respawn.
                if stderr_log is not subprocess.DEVNULL:
                    stderr_log.close()
        if transport is None:
            _core._app_server_trace("spawn-fail", kind=kind)
            continue
        _core._app_server_trace("spawn-ok", kind=kind, child_pid=getattr(proc, "pid", None))
        with _core._CODEX_APP_SERVER_LOCK:
            _core._CODEX_APP_SERVER_PROC = proc
            _core._CODEX_APP_SERVER_TRANSPORT = transport
            _core._CODEX_APP_SERVER_INITIALIZING = True
            _CODEX_APP_SERVER_READER = threading.Thread(
                target=_codex_app_server_reader,
                args=(transport,),
                daemon=True,
                name=f"codex-app-server-reader-{kind}",
            )
            _CODEX_APP_SERVER_READER.start()

        init = _core._codex_app_server_request_to_transport(
            transport,
            "initialize",
            {
                "clientInfo": {
                    "name": "claude-command-center",
                    "title": "Claude Command Center",
                    "version": _core.__version__,
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout=10,
        )
        if init.get("result") is not None:
            try:
                transport.send_json({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            except (BrokenPipeError, OSError):
                pass
            with _core._CODEX_APP_SERVER_LOCK:
                if _core._CODEX_APP_SERVER_TRANSPORT is transport and transport.alive():
                    _core._CODEX_APP_SERVER_INITIALIZED = True
                    _core._CODEX_APP_SERVER_INITIALIZING = False
                    _core._CODEX_APP_SERVER_LAST_LIVE_CHECK = time.time()
                    _core._CODEX_APP_SERVER_LOCK.notify_all()
                    return transport
        transport.close()
        with _core._CODEX_APP_SERVER_LOCK:
            if _core._CODEX_APP_SERVER_TRANSPORT is transport:
                _core._CODEX_APP_SERVER_TRANSPORT = None
                _core._CODEX_APP_SERVER_PROC = None
                _core._CODEX_APP_SERVER_INITIALIZED = False
            _core._CODEX_APP_SERVER_LOCK.notify_all()

    with _core._CODEX_APP_SERVER_LOCK:
        _core._CODEX_APP_SERVER_INITIALIZING = False
        _core._CODEX_APP_SERVER_LOCK.notify_all()
    return None


def _codex_app_server_shutdown():
    """Close CCC's Codex app-server transport on server exit."""
    # Pytest can remove ``server`` before its process-wide atexit callbacks
    # run. At that point the state belongs to an unloaded module and there is
    # no transport this callback can safely recover or close.
    if "server" not in sys.modules:
        return
    lock = _core._CODEX_APP_SERVER_LOCK
    with lock:
        transport = getattr(_core, "_CODEX_APP_SERVER_TRANSPORT", None)
        _core._CODEX_APP_SERVER_PROC = None
        _core._CODEX_APP_SERVER_TRANSPORT = None
        _core._CODEX_APP_SERVER_INITIALIZED = False
        _core._CODEX_APP_SERVER_INITIALIZING = False
        _core._CODEX_APP_SERVER_LOCK.notify_all()
    with _CODEX_SHARED_STATE_HOLDER_LOCK:
        _CODEX_SHARED_STATE_HOLDER_CACHE["ts"] = 0.0
        _CODEX_SHARED_STATE_HOLDER_CACHE["holders"] = None
    if transport is not None:
        transport.close()


def _codex_app_server_is_live():
    """True iff CCC has a live initialized Codex app-server transport.

    This may be the managed Unix-socket app-server or CCC's fallback stdio
    subprocess. Read-only: it does NOT start the server, so polling stays cheap.
    """
    transport = _core._CODEX_APP_SERVER_TRANSPORT
    return bool(
        transport is not None
        and transport.alive()
        and _core._CODEX_APP_SERVER_INITIALIZED
    )


def _codex_app_server_transport_kind():
    """Return the active app-server transport label for UI/status surfaces."""
    transport = _core._CODEX_APP_SERVER_TRANSPORT
    if not (
        transport is not None
        and transport.alive()
        and _core._CODEX_APP_SERVER_INITIALIZED
    ):
        return None
    if transport.kind == "managed-unix":
        return "managed"
    if transport.kind == "stdio":
        return "stdio"
    return transport.kind


def _system_app_server_status():
    """Read-only Codex app-server liveness snapshot for the System status
    modal (GET /api/system/app-server). All fields best-effort; the transport
    is read without the lock, so values may be a beat stale."""
    transport = _core._CODEX_APP_SERVER_TRANSPORT
    now = time.time()
    live = bool(
        transport is not None
        and transport.alive()
        and _core._CODEX_APP_SERVER_INITIALIZED
    )
    return {
        "live": live,
        "kind": _core._codex_app_server_transport_kind(),
        "pid": (
            getattr(getattr(transport, "proc", None), "pid", None)
            if live else None
        ),
        "age_s": round(now - transport.started_at) if live else None,
        "last_msg_age_s": (
            round(now - _CODEX_APP_SERVER_LAST_MSG_AT)
            if _CODEX_APP_SERVER_LAST_MSG_AT else None
        ),
        "last_check_age_s": (
            round(now - _core._CODEX_APP_SERVER_LAST_LIVE_CHECK)
            if _core._CODEX_APP_SERVER_LAST_LIVE_CHECK else None
        ),
        "consecutive_liveness_misses": (
            getattr(transport, "consecutive_liveness_misses", 0)
            if transport is not None else 0
        ),
        "miss_threshold": _CODEX_APP_SERVER_LIVENESS_MISS_THRESHOLD,
        "probe_timeout_s": _CODEX_APP_SERVER_LIVENESS_TIMEOUT,
    }


def _app_server_status_preferring_worker():
    """Codex app-server liveness, asked of the process that actually owns it.

    _control_plane_routes_engines() defaults on, so the transport lives in the
    worker's lazily-imported copy of `server`. Reading the dashboard's own
    module global returned live:false on a healthy system and the System status
    panel rendered "idle" while Codex sessions were mid-turn. One Unix-socket
    round trip, no fork; falls back to the local view when no worker answers
    (compatibility mode, where the dashboard really does own the transport).
    """
    try:
        remote = _core._control_plane_request("system.app_server")
    except Exception:
        remote = {}
    status = remote.get("status") if isinstance(remote, dict) and remote.get("ok") else None
    return status if isinstance(status, dict) else _core._system_app_server_status()


# ── /api/system/services ─────────────────────────────────────────────────────
# One coalesced snapshot for the SERVER STATUS chip and the System status
# panel. The chip polls every 20s and the open panel every 5s, so this must be
# zero-fork on the warm path: no _ccc_last_updated_iso() (git log), no
# build_system_health() (lsof/ps sweep), no per-row subprocess. Same
# double-checked-lock shape as _system_health_snapshot.
_SYSTEM_SERVICES_TTL = 3.0
_system_services_lock = threading.Lock()

# A single transient 500 should not paint the header yellow. The health bar
# already treats 5 errors in the 15-minute window as critical; reuse that line
# rather than inventing a second definition of "the dashboard is unhappy".
_DASHBOARD_DEGRADED_ERRORS = 5

# Crash-loop blindness: all three services are under launchd KeepAlive, so a
# service that dies every 90s reads "online, started just now" forever and a
# naive rollup calls that green. Remember the distinct start timestamps we have
# observed and count how many landed in the last 10 minutes. Purely in-memory
# and dashboard-side, which is honest about its own limit: it can see the
# worker and WatchTower flap, and it cannot see itself flap, because its own
# restart wipes this dict. Never claims a restart it did not observe.
_SERVICE_START_WINDOW_S = 600.0
_SERVICE_START_HISTORY_MAX = 24
_SERVICE_START_HISTORY = {}
_SERVICE_START_LOCK = threading.Lock()


def _record_service_start(service_id, started_at):
    """Log a distinct start timestamp; return how many landed in 10 minutes."""
    try:
        started_at = float(started_at)
    except (TypeError, ValueError):
        return 0
    if started_at <= 0:
        return 0
    now = time.time()
    with _SERVICE_START_LOCK:
        seen = _SERVICE_START_HISTORY.setdefault(str(service_id), [])
        # Second-level rounding: the worker reports float start times and the
        # WatchTower pidfile mtime can wobble in the sub-second digits.
        stamp = round(started_at)
        if not seen or seen[-1] != stamp:
            if stamp not in seen:
                seen.append(stamp)
                del seen[:-_SERVICE_START_HISTORY_MAX]
        return sum(1 for s in seen if now - s <= _SERVICE_START_WINDOW_S)


def _service_start_flap(service_id, started_at):
    """`starts_10m` plus the threshold the UI paints yellow at."""
    count = _record_service_start(service_id, started_at)
    return {"starts_10m": count, "flap_threshold": 3}


def _system_services_dashboard_entry():
    now = time.time()
    # O(live spawns) with Popen.poll(), never a fork and never O(all sessions).
    busy = _core._dashboard_owned_active_executions()
    # A Kimi turn owned by this process makes /api/restart answer 409, so say
    # so up front instead of letting the user click into that wall.
    blocking = any(
        str((row or {}).get("engine") or "").lower() == "kimi" for row in busy
    )
    degraded = _core._recent_error_count() >= _DASHBOARD_DEGRADED_ERRORS
    return {
        "id": "dashboard",
        "label": "Dashboard",
        # If this handler ran at all, the dashboard is up. "offline" is not a
        # verdict this row can ever honestly report about itself.
        "state": "degraded" if degraded else "online",
        "pid": os.getpid(),
        "started_at": _core._SERVER_START_TS,
        "started_at_approx": False,
        "uptime_s": round(now - _core._SERVER_START_TS),
        "version": _core.__version__,
        "recent_errors": _core._recent_error_count(),
        "busy_count": len(busy),
        "busy": busy,
        "restart_endpoint": "/api/restart",
        "restart_body": None,
        "restart_blocking": blocking,
    }


def _system_services_worker_entry():
    health = _core._control_plane_request("health")
    health = health if isinstance(health, dict) else {}
    worker = health.get("worker") if isinstance(health.get("worker"), dict) else {}
    ok = bool(health.get("ok"))
    available = bool(health.get("available", ok))
    if ok:
        state = "online"
    elif available:
        # Socket answered but health did not: the wedged shape that
        # _retire_wedged_control_plane_worker exists for. A restart clears it.
        state = "degraded"
    else:
        state = "offline"
    started_at = worker.get("started_at")
    try:
        started_at = float(started_at) if started_at else None
    except (TypeError, ValueError):
        started_at = None
    worker_version = worker.get("server_version")
    active = int(health.get("active") or 0)
    queued = int(health.get("queued") or 0)
    drain = health.get("drain") if isinstance(health.get("drain"), dict) else {}
    return {
        "id": "worker",
        "label": "Execution worker",
        "state": state,
        "pid": worker.get("pid"),
        "started_at": started_at,
        "started_at_approx": False,
        "uptime_s": (
            max(0, round(time.time() - started_at)) if started_at else None
        ),
        "version": worker_version,
        # The classic "the fix did not work": server.py loaded in the worker is
        # older than the one serving this page, so the worker still runs the
        # bug. None means it never imported server, which is not stale.
        "version_stale": bool(worker_version and worker_version != _core.__version__),
        "busy_count": active + queued,
        "active": active,
        "queued": queued,
        "uncertain": int(health.get("uncertain") or 0),
        # Distinct from "uncertain": every worker restart marks in-flight
        # work uncertain, and that alone self-clears within a couple of
        # sweep cycles (see ccc_worker.RETIRE_UNCERTAIN_AFTER_S). Only a
        # count that has survived a full sweep past retirement is actually
        # stuck -- that's what should page the status chip.
        "uncertain_stale": bool(health.get("uncertain_stale")),
        "drain_enabled": bool(drain.get("enabled")),
        "capabilities": worker.get("capabilities") or [],
        "restart_endpoint": "/api/restart/worker",
        "restart_body": None,
        "restart_blocking": False,
        **_service_start_flap("worker", started_at),
    }


def _wt_claimed_workers():
    """Every live WatchTower worker, each annotated with the ticket it's
    currently executing (or None if it's alive but idle/warm between
    claims). Distinct from workers_live, which just counts "alive" and
    conflates a worker mid-ticket with one sitting idle 23m with nothing
    claimed (see the menu-bar busy pulse, which used workers_live for that
    and lit up wrong)."""
    try:
        items = _core._q.list_items() or []
    except Exception:
        items = []
    claim_to_item = {}
    for item in items:
        if not isinstance(item, dict) or item.get("status") != "in_progress":
            continue
        for key in ("claimed_by", "claimed_session_id"):
            val = item.get(key)
            if val:
                claim_to_item[str(val)] = item

    rows = []
    for worker in _core._wt_read_workers():
        worker_id = str(worker.get("worker_id") or "")
        session_id = str(worker.get("session_id") or "")
        item = claim_to_item.get(worker_id) or claim_to_item.get(session_id)
        rows.append({
            "queue": worker.get("queue"),
            "engine": worker.get("engine"),
            "session_id": session_id or None,
            "idle_seconds": worker.get("idle_seconds"),
            "ticket_ref": item.get("ref") if item else None,
            "ticket_title": item.get("title") if item else None,
        })
    return rows


def _system_services_watchtower_entry():
    status = _core._watchtower_service_status(include_queues=True)
    running = bool(status.get("running"))
    state = status.get("state") or "stopped"
    if state == "stopped":
        state = "offline"
    claimed_workers = _wt_claimed_workers()
    return {
        "id": "watchtower",
        "label": "WatchTower server",
        "state": state,
        "pid": status.get("pid"),
        "started_at": status.get("started_at"),
        "started_at_approx": bool(status.get("started_at_approx")),
        "uptime_s": status.get("uptime_s"),
        "port": status.get("port"),
        "url": status.get("url"),
        "installed": bool(status.get("installed")),
        "api_ok": bool(status.get("api_ok")),
        "command_verified": bool(status.get("command_verified")),
        "pid_reused": bool(status.get("pid_reused")),
        "queues_total": int(status.get("queues_total") or 0),
        "open_total": int(status.get("open_total") or 0),
        "stuck_total": int(status.get("stuck_total") or 0),
        "workers_live": int(status.get("workers_live") or 0),
        "busy_count": int(status.get("workers_live") or 0),
        "claimed_worker_count": sum(1 for w in claimed_workers if w.get("ticket_ref")),
        "live_workers": claimed_workers,
        "released_workers_count": int(status.get("workers_released") or 0),
        "api_probe_age_s": status.get("api_probe_age_s"),
        "restart_endpoint": "/api/watchtower/service",
        "restart_body": {"action": "restart" if running else "start"},
        # This POST holds the HTTP thread under a global lock for up to ~33s,
        # and it refuses outright when the pid could not be identity-checked.
        "restart_blocking": bool(status.get("pid_reused")),
        **_service_start_flap("watchtower", status.get("started_at")),
    }


def _system_services_app_server_entry(worker_entry):
    status = _core._app_server_status_preferring_worker()
    status = status if isinstance(status, dict) else {}
    misses = int(status.get("consecutive_liveness_misses") or 0)
    live = bool(status.get("live"))
    if live:
        state = "degraded" if misses > 0 else "online"
    elif (worker_entry or {}).get("state") == "offline":
        # No worker, no app-server. Saying "idle" there would imply a healthy
        # lazy subprocess that simply has not been asked for anything yet.
        state = "offline"
    else:
        # Lazily started on the first Codex request. Not a failure.
        state = "idle"
    return {
        "id": "app_server",
        "label": "Codex app-server",
        "state": state,
        "pid": status.get("pid"),
        "started_at": None,
        "started_at_approx": False,
        "uptime_s": status.get("age_s"),
        "kind": status.get("kind"),
        "consecutive_liveness_misses": misses,
        "miss_threshold": status.get("miss_threshold"),
        "busy_count": 0,
        # A subprocess of the worker, not a service: restarting it means
        # restarting the worker, which the row above already offers.
        "restart_endpoint": None,
        "restart_body": None,
        "restart_blocking": False,
    }


def _system_services_spawned_processes():
    """Every currently-running claude/codex/kimi process CCC has spawned, for
    the System status panel's process list. Prewarm entries carry
    expires_at_epoch so the panel can show a live countdown to their
    auto-kill; regular spawns have no TTL today, so it's left as None and
    the panel shows them as having no scheduled kill."""
    rows = []
    for entry in _core.list_spawned_sessions():
        if not entry.get("running"):
            continue
        rows.append({
            "pid": entry.get("pid"),
            "name": entry.get("name") or "",
            "engine": entry.get("engine") or "claude",
            "cwd": entry.get("cwd") or "",
            "repo_path": entry.get("repo_path") or "",
            "model": entry.get("model") or "",
            "started": entry.get("started") or entry.get("spawned_at") or "",
            "prewarm": bool(entry.get("prewarm")),
            "created_at_epoch": entry.get("created_at_epoch"),
            "expires_at_epoch": entry.get("expires_at_epoch"),
        })
    return rows


_NEXT_SERVERS_CACHE = []
_NEXT_SERVERS_LOCK = threading.Lock()
_CLAUDE_PROCESSES_CACHE = []
_CLAUDE_PROCESSES_LOCK = threading.Lock()


def _parse_ps_etime_s(raw):
    """Parse `ps -o etime` output ('[[dd-]hh:]mm:ss') into elapsed seconds."""
    raw = (raw or "").strip()
    m = re.match(r'^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$', raw)
    if not m:
        return None
    days, hours, minutes, seconds = m.groups()
    return (
        int(days or 0) * 86400
        + int(hours or 0) * 3600
        + int(minutes) * 60
        + int(seconds)
    )


def _scan_system_processes():
    global _NEXT_SERVERS_CACHE, _CLAUDE_PROCESSES_CACHE
    cmd = ["ps", "-A", "-o", "pid,ppid,rss,etime,command"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        lines = res.stdout.strip().split('\n')
    except Exception:
        return

    procs = []
    for line in lines[1:]:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            rss = int(parts[2])
            etime_s = _parse_ps_etime_s(parts[3])
            command = parts[4]
            procs.append({
                "pid": pid,
                "ppid": ppid,
                "rss": rss,
                "etime_s": etime_s,
                "command": command
            })
        except ValueError:
            continue

    proc_map = {p['pid']: p for p in procs}

    def get_ancestors(pid):
        ancestors = []
        curr = pid
        while curr in proc_map:
            ppid = proc_map[curr]['ppid']
            if ppid <= 0 or ppid == 1 or ppid in ancestors:
                break
            ancestors.append(ppid)
            curr = ppid
        return ancestors

    # 1. NEXT.JS DEV SERVERS DISCOVERY
    next_processes = []
    for p in procs:
        cmd_lower = p['command'].lower()
        if "next-server" in cmd_lower or "next dev" in cmd_lower or "next/dev" in cmd_lower or "node_modules/.bin/next" in cmd_lower or "turbo dev" in cmd_lower:
            next_processes.append(p)

    next_results = []
    if next_processes:
        trees = {}
        for p in next_processes:
            ancestors = get_ancestors(p['pid'])
            root_pid = p['pid']
            for anc in reversed(ancestors):
                anc_cmd = proc_map[anc]['command'].lower()
                if any(k in anc_cmd for k in ["npm", "yarn", "node", "turbo", "next", "pnpm", "bun"]):
                    root_pid = anc
                    break
            if root_pid not in trees:
                trees[root_pid] = []
            trees[root_pid].append(p)

        for root_pid, member_procs in trees.items():
            root_proc = proc_map.get(root_pid)
            if not root_proc:
                continue

            all_tree_pids = {root_pid}
            for p in procs:
                if root_pid in get_ancestors(p['pid']):
                    all_tree_pids.add(p['pid'])

            total_rss = sum(proc_map[pid]['rss'] for pid in all_tree_pids if pid in proc_map)

            port = None
            for pid in all_tree_pids:
                cmd = proc_map[pid]['command']
                m = re.search(r'(?:-p|--port)\s+(\d+)', cmd)
                if m:
                    port = int(m.group(1))
                    break
            if not port:
                port = 3000

            project_path = ""
            for pid in all_tree_pids:
                cmd = proc_map[pid]['command']
                m = re.search(r'(/Users/[^/]+/Apps/[^/\s]+|/Users/[^/]+/[^/\s]+)', cmd)
                if m:
                    p_candidate = m.group(1)
                    if "node_modules" not in p_candidate and "Library" not in p_candidate and ".claude" not in p_candidate:
                        project_path = p_candidate
                        break
                node_idx = cmd.find("node_modules")
                if node_idx != -1:
                    parts = cmd[:node_idx].split()
                    if parts:
                        project_path = parts[-1].rstrip("/")
                        break

            if project_path:
                project_name = project_path.rstrip("/").split("/")[-1]
            else:
                project_name = "Next.js App"
                project_path = "Unknown Path"

            next_results.append({
                "pid": root_pid,
                "port": port,
                "project": project_name,
                "path": project_path,
                "memory_mb": round(total_rss / 1024.0, 1),
                "command": root_proc['command'],
                "all_pids": list(all_tree_pids)
            })

    # 2. CLAUDE & AGY AGENT PROCESSES DISCOVERY
    claude_results = []
    my_pid = os.getpid()
    my_ancestry = set(get_ancestors(my_pid))
    my_ancestry.add(my_pid)

    # Propagate active descendants tree
    changed = True
    while changed:
        changed = False
        for p in procs:
            if p['pid'] not in my_ancestry and p['ppid'] in my_ancestry:
                my_ancestry.add(p['pid'])
                changed = True

    for p in procs:
        cmd_lower = p['command'].lower()
        if ("bin/claude" in cmd_lower or "bin/agy" in cmd_lower) and not "server.py" in cmd_lower:
            session_id = ""
            m_res = re.search(r'--resume\s+([a-f0-9\-]+)', p['command'])
            if m_res:
                session_id = m_res.group(1)
            else:
                m_conv = re.search(r'--conversation\s+([a-f0-9\-]+)', p['command'])
                if m_conv:
                    session_id = m_conv.group(1)

            project_path = ""
            m_dir = re.search(r'--add-dir\s+([^\s]+)', p['command'])
            if m_dir:
                project_path = m_dir.group(1)

            if project_path:
                project_name = project_path.rstrip("/").split("/")[-1]
            else:
                project_name = "Unknown Project"
                project_path = "Unknown Path"

            is_orphaned = p['ppid'] == 1 or (p['ppid'] not in proc_map) or (p['ppid'] not in my_ancestry and proc_map[p['ppid']]['command'].lower() == "init")
            killable = p['pid'] not in my_ancestry

            label = f"resume-{session_id[:8]}" if session_id else f"claude-{p['pid']}"
            started = ""
            if p.get('etime_s') is not None:
                started = time.strftime('%Y%m%dT%H%M%S', time.localtime(time.time() - p['etime_s']))
            claude_results.append({
                "pid": p['pid'],
                "ppid": p['ppid'],
                "name": label,
                "engine": "agy" if "bin/agy" in cmd_lower else "claude",
                "cwd": project_path,
                "repo_path": project_path,
                "model": "",
                "started": started,
                "prewarm": False,
                "expires_at_epoch": None,
                "memory_mb": round(p['rss'] / 1024.0, 1),
                "is_orphaned": is_orphaned,
                "killable": killable,
            })

    with _NEXT_SERVERS_LOCK:
        _NEXT_SERVERS_CACHE = next_results
    with _CLAUDE_PROCESSES_LOCK:
        _CLAUDE_PROCESSES_CACHE = claude_results


def _system_processes_scanner_loop():
    _scan_system_processes()
    while True:
        try:
            _scan_system_processes()
        except Exception:
            pass
        time.sleep(10.0)


def _system_services_next_servers():
    with _NEXT_SERVERS_LOCK:
        return list(_NEXT_SERVERS_CACHE)


def _system_services_spawned_processes():
    with _CLAUDE_PROCESSES_LOCK:
        rows = list(_CLAUDE_PROCESSES_CACHE)
    
    seen_pids = set(r["pid"] for r in rows if r["pid"])
    for entry in _core.list_spawned_sessions():
        if not entry.get("running") or entry.get("pid") in seen_pids:
            continue
        rows.append({
            "pid": entry.get("pid"),
            "name": entry.get("name") or "",
            "engine": entry.get("engine") or "claude",
            "cwd": entry.get("cwd") or "",
            "repo_path": entry.get("repo_path") or "",
            "model": entry.get("model") or "",
            "started": entry.get("started") or entry.get("spawned_at") or "",
            "prewarm": bool(entry.get("prewarm")),
            "created_at_epoch": entry.get("created_at_epoch"),
            "expires_at_epoch": entry.get("expires_at_epoch"),
            "memory_mb": 0.0,
            "is_orphaned": False,
            "killable": True,
        })
    return rows


def kill_next_server(pid):
    pids_to_kill = [pid]
    in_next_cache = False
    with _NEXT_SERVERS_LOCK:
        for item in _NEXT_SERVERS_CACHE:
            if item["pid"] == pid:
                pids_to_kill = item["all_pids"]
                in_next_cache = True
                break

    if not in_next_cache:
        try:
            cmd = ["ps", "-A", "-o", "pid,ppid"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            lines = res.stdout.strip().split('\n')
            proc_map = {}
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    proc_map[int(parts[0])] = int(parts[1])

            descendants = {pid}
            for p in list(proc_map.keys()):
                curr = p
                visited = set()
                while curr in proc_map:
                    pp = proc_map[curr]
                    if pp == pid:
                        descendants.add(p)
                        break
                    if pp <= 1 or pp in visited:
                        break
                    visited.add(pp)
                    curr = pp
            pids_to_kill = list(descendants)
        except Exception:
            pids_to_kill = [pid]

    killed = []
    for p in pids_to_kill:
        try:
            os.kill(p, signal.SIGKILL)
            killed.append(p)
        except Exception:
            pass
    threading.Thread(target=_scan_system_processes, daemon=True).start()
    return {"ok": True, "killed": killed}


def _build_system_services_uncached():
    worker = _system_services_worker_entry()
    return {
        "ok": True,
        "generated_at": time.time(),
        "dashboard_version": _core.__version__,
        "services": [
            _system_services_dashboard_entry(),
            worker,
            _core._system_services_watchtower_entry(),
            _system_services_app_server_entry(worker),
        ],
        "spawned_processes": _system_services_spawned_processes(),
        "next_servers": _system_services_next_servers(),
    }




def build_system_services(force=False):
    """Every service the System status panel renders, in one round trip."""
    snap = _core._system_services_cache
    now = time.time()
    if (
        not force and snap["payload"] is not None
        and now - snap["ts"] < _SYSTEM_SERVICES_TTL
    ):
        return snap["payload"]
    with _system_services_lock:
        now = time.time()
        if (
            not force and snap["payload"] is not None
            and now - snap["ts"] < _SYSTEM_SERVICES_TTL
        ):
            return snap["payload"]
        payload = _core._build_system_services_uncached()
        snap["payload"] = payload
        snap["ts"] = time.time()
        return payload


def _schedule_codex_managed_app_server_warmup():
    """Kick managed app-server attach in the background, never on a poll path."""
    global _CODEX_APP_SERVER_WARMUP_LAST
    if _core._codex_app_server_is_live():
        return False
    if not _core._codex_managed_app_server_enabled():
        return False
    try:
        if not _core._codex_managed_app_server_socket_path().exists():
            return False
    except Exception:
        return False
    with _core._CODEX_APP_SERVER_LOCK:
        if _core._CODEX_APP_SERVER_INITIALIZING:
            return False
    now = time.time()
    with _CODEX_APP_SERVER_WARMUP_LOCK:
        if now - _CODEX_APP_SERVER_WARMUP_LAST < 2.0:
            return False
        _CODEX_APP_SERVER_WARMUP_LAST = now

    def _warm():
        try:
            _core._ensure_codex_app_server(allow_stdio=False)
        except Exception:
            pass

    threading.Thread(
        target=_warm,
        daemon=True,
        name="codex-managed-app-server-warmup",
    ).start()
    return True


def _codex_user_input(text, image_paths=None):
    items = [{"type": "text", "text": str(text or "")}]
    for image_path in image_paths or []:
        if image_path:
            items.append({"type": "localImage", "path": str(image_path)})
    return items


def _codex_latest_active_turn(thread):
    turns = (thread or {}).get("turns") or []
    for turn in reversed(turns):
        if isinstance(turn, dict) and turn.get("status") == "inProgress" and turn.get("id"):
            return turn
    return None


def _codex_error_text(response):
    if not isinstance(response, dict):
        return "Codex app-server returned no response"
    err = response.get("error")
    if not isinstance(err, dict):
        return ""
    message = str(err.get("message") or "Codex app-server request failed")
    data = err.get("data")
    if data is not None:
        return f"{message}: {data}"
    return message


def _codex_error_is_not_steerable(response):
    text = _codex_error_text(response)
    lowered = text.lower()
    if "activeTurnNotSteerable" in text or "not steerable" in lowered:
        return True
    # Busy-thread rejection ("Invalid request: Cannot launch a new turn while
    # another turn (ID 7) is active"): the message must take the durable-queue
    # fallback, NOT the exec fallback — the exec spawn dies with turn.failed
    # and litters the transcript with duplicate user bubbles + error banners
    # on every queue-pump retry.
    return "cannot launch a new turn" in lowered or "another turn" in lowered


def _codex_response_succeeded(response):
    return isinstance(response, dict) and "result" in response and not response.get("error")


def _codex_rollout_stat(session_id):
    try:
        path = _core._resolve_codex_rollout_path(session_id)
        if not path:
            return None
        st = Path(path).stat()
        return {"path": str(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    except OSError:
        return None


def _codex_rollout_grew(before, session_id):
    if not before:
        return False
    after = _core._codex_rollout_stat(session_id)
    if not after or after.get("path") != before.get("path"):
        return False
    return (
        int(after.get("size") or 0) > int(before.get("size") or 0)
        or int(after.get("mtime_ns") or 0) > int(before.get("mtime_ns") or 0)
    )


def _codex_rollout_contains_user_text_since(baseline, session_id, text):
    """True when an authoritative user-message row landed after `baseline`."""
    expected = _core._strip_ccc_session_state_instruction(str(text or "")).strip()
    if not expected:
        return True
    current = _core._codex_rollout_stat(session_id)
    if not current:
        return False
    path = current.get("path")
    offset = 0
    if baseline and baseline.get("path") == path:
        offset = max(0, int(baseline.get("size") or 0))
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if event.get("type") != "event_msg" or payload.get("type") != "user_message":
                    continue
                actual = _core._strip_ccc_session_state_instruction(
                    str(payload.get("message") or "")
                ).strip()
                if actual == expected:
                    return True
    except OSError:
        return False
    return False


def _codex_wait_for_turn_activity(session_id, turn_id=None, *, baseline_state=None,
                                  baseline_rollout=None, expected_text=None,
                                  timeout=5.0):
    """Confirm that an accepted turn durably recorded its exact user input."""
    baseline_state = baseline_state or {}
    baseline_seq = int(baseline_state.get("event_seq") or 0)
    expected = _core._strip_ccc_session_state_instruction(str(expected_text or "")).strip()
    deadline = time.time() + max(0.0, float(timeout))
    while True:
        with _core._CODEX_APP_SERVER_LOCK:
            state = dict(_core._CODEX_APP_SERVER_THREAD_STATE.get(session_id) or {})
            seq = int(state.get("event_seq") or 0)
            state_turn = state.get("active_turn_id") or state.get("last_turn_id") or state.get("last_completed_turn_id")
            delivered_text = str(state.get("last_delivered_user_text") or "").strip()
            delivered_turn = state.get("last_delivered_user_turn_id")
            if expected and seq > baseline_seq and delivered_text == expected and (
                not turn_id or str(delivered_turn or "") == str(turn_id)
            ):
                return {"confirmed": True, "source": "app-server-notification", "state": state}
            if not expected and seq > baseline_seq and (not turn_id or state_turn == turn_id):
                return {"confirmed": True, "source": "app-server-notification", "state": state}
            if not expected and turn_id and state.get("last_completed_turn_id") == turn_id:
                return {"confirmed": True, "source": "app-server-notification", "state": state}
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            _core._CODEX_APP_SERVER_LOCK.wait(min(0.25, remaining))
        if expected and _codex_rollout_contains_user_text_since(
            baseline_rollout, session_id, expected
        ):
            return {"confirmed": True, "source": "rollout-user-message", "state": _core._codex_app_server_thread_state(session_id)}
        if not expected and _codex_rollout_grew(baseline_rollout, session_id):
            return {"confirmed": True, "source": "rollout-growth", "state": _core._codex_app_server_thread_state(session_id)}
    if expected and _codex_rollout_contains_user_text_since(
        baseline_rollout, session_id, expected
    ):
        return {"confirmed": True, "source": "rollout-user-message", "state": _core._codex_app_server_thread_state(session_id)}
    if not expected and _codex_rollout_grew(baseline_rollout, session_id):
        return {"confirmed": True, "source": "rollout-growth", "state": _core._codex_app_server_thread_state(session_id)}
    return {
        "confirmed": False,
        "source": None,
        "state": _core._codex_app_server_thread_state(session_id),
        "warning": "turn accepted but no app-server events observed",
    }


def _wt_register_codex_agent(thread_id, cwd=""):
    """Best-effort: tell WatchTower's agents registry that this thread is a
    Codex session, right when CCC spawns it.

    Without this, `wt`'s own `resolve_target` has no way to learn the engine
    for a codex-app-spawn thread -- CCC never goes through `wt spawn`, so
    WT's codex_registry (which only gets populated by a WT-initiated
    delivery that already reached the thread as Codex) stays empty for it.
    A later WT-side send to the bare thread UUID then falls back to WT's
    documented "claude" assumption, searches for a Claude transcript that
    will never exist, and the receipt sits "lost, unverified" forever even
    when the message actually landed via the delegate adapter. Registering
    the name here closes that gap from the CCC side, which is exactly what
    `resolve_target`'s own docstring asks callers holding a non-claude UUID
    to do. Silent no-op if `wt` isn't installed or the call fails -- this is
    a delivery-reliability nicety, not something a spawn should ever block
    or fail on.
    """
    wt_cli = _core._wt_cli_path()
    if not wt_cli:
        return
    # `_` (not `-`) before the id: WT rejects any name ending in
    # "-<8 lowercase hex chars>" as colliding with its worker-id shape
    # ("<queue>-<8 hex>"), and an 8-char thread-id prefix hits that exactly.
    name = "codex_" + str(thread_id)
    try:
        subprocess.run(
            [wt_cli, "agents", "register", name, "--session", str(thread_id),
             "--engine", "codex", "--cwd", str(cwd or "")],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass


def _codex_finalize_spawn_async(thread_id, turn_id, *, session_name, prompt,
                                log_path, baseline_state, baseline_rollout,
                                cwd=""):
    """Do the post-acceptance spawn work off the caller's critical path.

    Once turn/start is accepted the turn is already running inside Codex, so
    nothing below needs to block the spawn response:

      * `thread/name/set` — a cosmetic label, measured at ~1.7s, and turn/start
        never depended on it.
      * the durability confirmation — measured p50 148ms but p75 ~5.0s, and no
        caller branches on the result (every unconfirmed spawn in telemetry
        still returned ok=True).

    Results land in the spawn log and telemetry a few seconds later instead of
    holding the user's session open.

    Order matters: the confirmation runs FIRST and the rename second. Codex
    resolves thread/name/set against the thread's rollout file, so naming
    before that file has content fails outright with "rollout ... is empty" --
    observed when this ran the other way round. Waiting for the durability
    confirmation is exactly the signal that the rollout now exists, which is
    also why the rename used to appear to "cost" ~1.7s: it was Codex blocking
    on the same write.
    """
    def worker():
        _core._wt_register_codex_agent(thread_id, cwd=cwd)
        # Watch the rollout appear/fill alongside the confirmation. These are
        # the marks that explain a "slow" session: CCC renders the transcript
        # FROM this file, so nothing can reach the screen before it exists and
        # carries the user's message.
        def watch_rollout():
            deadline = time.time() + 180
            seen_file = False
            while time.time() < deadline:
                stat = _core._codex_rollout_stat(thread_id)
                if stat and not seen_file:
                    seen_file = True
                    _core._spawn_timeline_mark(thread_id, "rollout_file_created")
                if stat and (stat.get("size") or 0) > 0:
                    _core._spawn_timeline_mark(thread_id, "rollout_first_bytes")
                    return
                time.sleep(0.25)
        threading.Thread(
            target=watch_rollout, daemon=True,
            name=f"ccc-rollout-watch-{str(thread_id)[:8]}",
        ).start()

        # Nobody waits on this any more, so give it a window that can actually
        # observe the answer. Codex writes ~85KB of preamble into a fresh
        # rollout before the user_message row lands, which the old 5s budget
        # regularly lost to -- a confirmation that is almost always "no" cannot
        # flag a genuinely dropped prompt. The inject path keeps its own 5s
        # budget, because a human is waiting on that one.
        try:
            confirm_timeout = float(
                os.environ.get("CCC_CODEX_SPAWN_CONFIRM_TIMEOUT", "30")
            )
        except ValueError:
            confirm_timeout = 30.0
        confirm_at = time.monotonic()
        confirmation = _core._codex_wait_for_turn_activity(
            thread_id,
            turn_id,
            baseline_state=baseline_state,
            baseline_rollout=baseline_rollout,
            expected_text=prompt,
            timeout=confirm_timeout,
        )
        confirm_ms = _codex_elapsed_ms(confirm_at)
        if confirmation.get("confirmed"):
            # The prompt is now durably on disk -- this is the earliest moment
            # CCC could possibly render the user's own message.
            _core._spawn_timeline_mark(thread_id, "rollout_has_user_message")
        _core._spawn_timeline_mark(thread_id, "confirm_finished")

        name_set_ms = None
        rename_warning = ""
        if session_name:
            name_set_at = time.monotonic()
            # A confirmation timeout does not prove the rollout is missing, so
            # still try -- but retry briefly, since the only known failure here
            # is losing a race with Codex's first rollout write.
            for attempt in range(_CODEX_NAME_SET_ATTEMPTS):
                renamed = _core._codex_app_server_request(
                    "thread/name/set",
                    {"threadId": thread_id, "name": session_name},
                    timeout=10,
                )
                if _core._codex_response_succeeded(renamed):
                    rename_warning = ""
                    break
                rename_warning = _codex_app_server_response_error(
                    renamed,
                    "Codex app-server accepted the thread but did not name it",
                )
                if attempt + 1 < _CODEX_NAME_SET_ATTEMPTS:
                    time.sleep(_core._CODEX_NAME_SET_RETRY_DELAY)
            name_set_ms = _codex_elapsed_ms(name_set_at)
            _core._spawn_timeline_mark(thread_id, "name_set_finished")
        # Append, don't reopen for write: the spawn path already closed its
        # handle on this file by the time we get here.
        try:
            with open(log_path, "a") as fh:
                if rename_warning:
                    fh.write(json.dumps({
                        "event": "codex_app_server_name_warning",
                        "warning": rename_warning,
                    }, sort_keys=True) + "\n")
                if not confirmation.get("confirmed"):
                    fh.write(json.dumps({
                        "event": "codex_app_server_spawn_warning",
                        "warning": confirmation.get("warning"),
                        "turn_id": turn_id,
                    }, sort_keys=True) + "\n")
                fh.write(json.dumps({
                    "event": "codex_app_server_spawn_finalized",
                    "turn_id": turn_id,
                    "confirmed": bool(confirmation.get("confirmed")),
                    "confirmation_source": confirmation.get("source"),
                    "name_set_ms": name_set_ms,
                    "confirm_ms": confirm_ms,
                }, sort_keys=True) + "\n")
        except OSError:
            pass
        _core._spawn_timeline_mark(thread_id, "finalize_done")
        _core._spawn_timeline_save()
        _core._codex_telemetry_append(
            "codex_spawn_finalize",
            ok=True,
            via="codex-app-spawn",
            session_id=thread_id,
            turn_id=turn_id,
            name_set_ms=name_set_ms,
            confirm_ms=confirm_ms,
            confirmed=bool(confirmation.get("confirmed")),
            confirmation_source=confirmation.get("source"),
            warning=confirmation.get("warning") or rename_warning or None,
        )

    thread = threading.Thread(
        target=worker,
        daemon=True,
        name=f"ccc-codex-finalize-{str(thread_id)[:8]}",
    )
    # Published so tests can join the finalizer instead of racing it; nothing
    # in production reads this.
    _core._CODEX_LAST_SPAWN_FINALIZER = thread
    thread.start()
    return thread


def _codex_reconcile_thread_idle(session_id):
    """Clear volatile ownership after Codex authoritatively reports idle."""
    cleared_phantom = False
    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(str(session_id), {})
        previous_status = str(state.get("status") or "").lower()
        previous_turn_id = str(state.get("active_turn_id") or "").strip()
        previous_writer = str(state.get("active_writer") or "").strip()
        previous_item = (
            state.get("active_item")
            if isinstance(state.get("active_item"), dict)
            else {}
        )
        previous_tool = str(
            previous_item.get("tool") or previous_item.get("type") or ""
        ).strip()
        cleared_phantom = bool(
            previous_turn_id
            or previous_writer
            or previous_status == "active"
        )
        changed = bool(
            cleared_phantom
            or previous_status != "idle"
            or state.get("active_item")
            or state.get("active_items")
        )
        state["status"] = "idle"
        state.pop("active_turn_id", None)
        state.pop("active_writer", None)
        state.pop("active_item", None)
        state.pop("active_items", None)
        if cleared_phantom:
            evidence = []
            if previous_writer:
                evidence.append(f"writer={previous_writer}")
            if previous_turn_id:
                evidence.append(f"turn={previous_turn_id}")
            if previous_tool:
                evidence.append(f"stale item={previous_tool}")
            evidence_text = f"; cleared {', '.join(evidence)}" if evidence else ""
            _core._codex_coordination_event_unlocked(
                state,
                "external_turn_ended",
                detail=(
                    "Codex re-read found no live turn"
                    f"{evidence_text}; queued input may proceed"
                ),
            )
        if changed:
            _core._save_codex_app_server_state_unlocked()
    if cleared_phantom:
        _core._schedule_codex_queue_pump(session_id)
    return cleared_phantom


def _codex_app_server_thread_is_active(session_id, *, start_if_needed=False):
    """Best-effort read of whether CCC's live Codex app-server has an active turn.

    This deliberately does not start the app-server; it is used by the durable
    pending-input watcher to avoid popping and requeueing Codex messages while a
    volatile app-server turn is still in progress.
    """
    routed = _core._control_plane_engine_call(
        "codex", "active", {
            "session_id": session_id,
            "start_if_needed": bool(start_if_needed),
        },
        mutate=False,
    )
    if routed is not None:
        return bool(routed.get("active"))
    if not session_id:
        return False
    if not _core._codex_app_server_is_live():
        if not start_if_needed or _core._ensure_codex_app_server() is None:
            return False
    try:
        resumed = _core._codex_app_server_request(
            "thread/resume",
            {"threadId": session_id, "excludeTurns": False},
            timeout=5,
        )
    except Exception:
        return False
    if not _core._codex_response_succeeded(resumed):
        state = _core._codex_app_server_thread_state(session_id)
        return bool(state.get("active_turn_id") or str(state.get("status") or "").lower() == "active")
    thread = ((resumed.get("result") or {}).get("thread") or {})
    status = ((thread.get("status") or {}).get("type") or "").lower()
    if status == "active" or bool(_codex_latest_active_turn(thread)):
        return True

    # `thread/resume` is an authoritative re-read from Codex. If it says the
    # thread is idle, discard any volatile active/unknown writer left behind by
    # a lost notification, server restart, or ended app-server client. Keeping
    # the stale local marker here made FIFO input wait forever until somebody
    # opened the thread in a CLI and caused a fresh status transition.
    _codex_reconcile_thread_idle(session_id)
    return False


# ── Codex desktop ↔ CCC single-writer coordination ──────────────────────────
# Codex desktop (ChatGPT.app / Codex.app) runs its OWN `codex app-server`
# process; turns it starts are invisible to CCC's app-server notifications.
# Both processes append to the same rollout JSONL, so the rollout file is the
# shared ground truth. The coordination model:
#   * attachment  — the desktop app-server holds an open write fd on the
#     rollout of every thread it has loaded (verified via lsof). Attachment
#     alone does NOT mean activity: desktop keeps day-old threads open.
#   * activity    — rollout mtime advanced within _CODEX_EXTERNAL_WRITER_WINDOW_S
#     and the growth is not attributable to CCC's own app-server turn or a
#     CCC-spawned `codex exec resume` child ⇒ an external writer (the desktop
#     when attached, otherwise a CLI/TUI) is mid-turn.
#   * write-gate  — CCC never issues turn/start while an external writer is
#     active; the message falls into the existing durable pending-input queue
#     (fallback:"queue") and the resume-queue watcher drains it once the
#     thread goes quiet. A per-thread mutex serializes concurrent CCC sends.
# Residual gap (documented, not fixable from CCC): the desktop's in-memory
# copy of a thread does not reload CCC-originated turns until the desktop
# itself refreshes the thread.
_CODEX_DESKTOP_ATTACH_TTL_S = 10.0
_CODEX_EXTERNAL_WRITER_WINDOW_S = 20.0
# An authoritative "active" thread status is volatile in-memory state (see
# _codex_load_coordination_state's docstring) with no automatic idle
# transition if the writer that started the turn (desktop/mobile app-server
# client) dies or disconnects without ever sending turn/completed. Without a
# staleness cap, that leaves the thread permanently mis-attributed to an
# "external active" writer — CCC shows "Active Codex turn detected" and
# queues sends forever even though nothing is running (CCC-998).
_CODEX_ACTIVE_STATUS_STALE_S = 300.0
_CODEX_COORD_EVENTS_MAX = 40
_CODEX_COORD_EVENTS_TAIL = 8
_codex_desktop_attach_cache = {"ts": 0.0, "rollouts": {}}
_codex_desktop_attach_lock = threading.Lock()
_codex_thread_turn_locks = {}
_codex_thread_turn_locks_lock = threading.Lock()
_codex_external_writer_last = {}
_codex_external_writer_last_lock = threading.Lock()


def _codex_thread_turn_lock(thread_id):
    """Per-thread mutex so two CCC callers can't race resume→turn/start."""
    with _codex_thread_turn_locks_lock:
        lock = _codex_thread_turn_locks.get(thread_id)
        if lock is None:
            lock = threading.Lock()
            _codex_thread_turn_locks[thread_id] = lock
        return lock


def _codex_desktop_app_server_procs():
    """`codex app-server` processes owned by a desktop app bundle.

    The desktop app's bundled binary lives inside `.app/Contents/…`, which
    cleanly separates it from CCC's own stdio child (spawned from a bare CLI
    path) and from wt/CLI processes. CCC's own child pid is excluded
    explicitly as belt-and-braces.
    """
    procs = []
    try:
        own_pid = None
        proc = _core._CODEX_APP_SERVER_PROC
        if proc is not None:
            own_pid = proc.pid
        for pid_s, cmd in _core._raw_engine_process_commands("codex"):
            if "app-server" not in cmd:
                continue
            head = cmd.split()[0] if cmd.split() else ""
            if ".app/Contents/" not in head:
                continue
            try:
                pid = int(pid_s)
            except (TypeError, ValueError):
                continue
            if own_pid and pid == own_pid:
                continue
            procs.append({"pid": pid, "command": cmd})
    except Exception:
        return []
    return procs


def _parse_lsof_open_rollouts(output):
    """Map rollout-jsonl path → pid from `lsof -Fpn` field output. Pure."""
    rollouts = {}
    pid = None
    for line in (output or "").splitlines():
        if not line:
            continue
        tag, rest = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(rest)
            except ValueError:
                pid = None
        elif tag == "n" and pid is not None:
            if "/sessions/" in rest and rest.endswith(".jsonl"):
                rollouts[rest] = pid
    return rollouts


def _codex_desktop_attached_rollouts(now=None):
    """rollout abs path → desktop app-server pid. One batched lsof, TTL-cached.

    Perf gate compliance: at most one `lsof` subprocess per TTL window for the
    whole server, never per session row.
    """
    now = time.time() if now is None else float(now)
    with _codex_desktop_attach_lock:
        if now - _codex_desktop_attach_cache["ts"] < _CODEX_DESKTOP_ATTACH_TTL_S:
            return dict(_codex_desktop_attach_cache["rollouts"])
        # Claim the window before the subprocess so concurrent callers reuse
        # the (possibly slightly stale) map instead of piling up lsof forks.
        _codex_desktop_attach_cache["ts"] = now
    rollouts = {}
    procs = _core._codex_desktop_app_server_procs()
    if procs:
        pid_list = ",".join(str(p.get("pid")) for p in procs if p.get("pid"))
        # lsof lives in /usr/sbin, which the LaunchAgent's PATH does not
        # include — a bare "lsof" raises FileNotFoundError inside the service
        # and the attachment map silently stays empty.
        lsof_bin = shutil.which("lsof") or "/usr/sbin/lsof"
        if pid_list and os.path.isfile(lsof_bin):
            try:
                out = subprocess.run(
                    [lsof_bin, "-w", "-p", pid_list, "-Fpn"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=3.0,
                    check=False,
                ).stdout
                rollouts = _core._parse_lsof_open_rollouts(out)
            except (OSError, subprocess.SubprocessError):
                rollouts = {}
    with _codex_desktop_attach_lock:
        _codex_desktop_attach_cache["rollouts"] = dict(rollouts)
    return rollouts


def _codex_ccc_exec_child_running(session_id):
    """True when a CCC-spawned `codex exec resume` child owns this sid."""
    try:
        return any(
            s.get("resumed_sid") == session_id and _core._poll_spawn_entry(s) is None
            for s in _core._spawned_sessions
            if s.get("engine") == "codex"
        )
    except Exception:
        return False


def _codex_thread_writer_snapshot(session_id, now=None, *, rollout=None,
                                  app_state=None, attached=None,
                                  exec_child=None):
    """Attribute the current writer of one Codex thread.

    Returns {writer, desktop_attached, external_active, mtime_age_s}:
      writer ∈ "ccc" | "desktop" | "external" | "unknown" | None (quiet).
    Keyword args exist for unit tests; production callers pass none of them.
    """
    now = time.time() if now is None else float(now)
    snap = {"writer": None, "desktop_attached": False, "external_active": False}
    if not session_id:
        return snap
    state = app_state if app_state is not None else _core._codex_app_server_thread_state(session_id)
    state = state or {}
    active_turn_id = state.get("active_turn_id")
    active_status = str(state.get("status") or "").strip().lower() == "active"
    active_writer = str(state.get("active_writer") or "").strip().lower()
    ccc_turn_active = bool(
        state.get("ccc_turn_start_pending")
        or (active_turn_id and active_writer == "ccc")
    )
    ccc_recent = ccc_turn_active
    if not ccc_recent:
        try:
            last_seen = float(state.get("last_activity_at") or state.get("last_event_at") or 0)
            ccc_recent = (now - last_seen) < _CODEX_EXTERNAL_WRITER_WINDOW_S
        except (TypeError, ValueError):
            ccc_recent = False
    if exec_child is None:
        exec_child = _codex_ccc_exec_child_running(session_id)
    if rollout is None:
        rollout = _core._codex_rollout_stat(session_id)
    path = (rollout or {}).get("path")
    mtime_recent = False
    if rollout:
        try:
            age = now - (float(rollout.get("mtime_ns") or 0) / 1e9)
            snap["mtime_age_s"] = round(age, 1)
            mtime_recent = age < _CODEX_EXTERNAL_WRITER_WINDOW_S
        except (TypeError, ValueError):
            pass
    if path:
        if attached is None:
            attached = _codex_desktop_attached_rollouts(now)
        snap["desktop_attached"] = str(path) in (attached or {})
    if ccc_turn_active or exec_child:
        snap["writer"] = "ccc"
        return snap
    # Trust an authoritative "idle" thread status (delivered via
    # thread/status/changed and captured into state["status"]) over the
    # rollout-mtime heuristic. CCC shares the managed app-server daemon with the
    # Codex mobile/desktop apps, so it receives their real active→idle
    # transitions; a thread the daemon reports idle is NOT being written, even
    # when the rollout mtime is fresh because an external surface merely OPENED
    # the thread (touching the file without starting a turn). Without this, an
    # opened-but-idle thread was mis-attributed to an active external writer and
    # every CCC send queued forever ("session is busy" that never clears). The
    # status is volatile (never restored across restarts), so its presence means
    # we heard it this process — trustworthy. When we have no status (a thread we
    # aren't attached to / haven't heard from), fall back to the mtime heuristic.
    if str(state.get("status") or "").strip().lower() == "idle":
        return snap
    # An authoritative active status without CCC ownership belongs to another
    # app-server client (mobile, desktop, or another integration). This is more
    # precise than rollout mtime and prevents CCC from becoming a second writer.
    if active_status and active_writer != "ccc":
        try:
            last_seen_active = float(state.get("last_activity_at") or state.get("last_event_at") or 0)
        except (TypeError, ValueError):
            last_seen_active = 0.0
        stale = bool(last_seen_active) and (now - last_seen_active) > _CODEX_ACTIVE_STATUS_STALE_S
        if not stale:
            snap["external_active"] = True
            snap["writer"] = "desktop" if snap["desktop_attached"] else "unknown"
            return snap
    if mtime_recent and not ccc_recent:
        snap["external_active"] = True
        snap["writer"] = "desktop" if snap["desktop_attached"] else "unknown"
    return snap


def _codex_load_coordination_state():
    """Restore durable coordination and recovery state from app-server state.

    Volatile fields (active_turn_id, status) must never survive a restart; they
    would report phantom turns. A recovery latch is durable by design so the
    singleton watcher can finish an episode after CCC restarts.
    """
    if _core._codex_coord_state_loaded:
        return
    _core._codex_coord_state_loaded = True
    try:
        with _core.CODEX_APP_SERVER_STATE_FILE.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    threads = data.get("threads") if isinstance(data, dict) else None
    if not isinstance(threads, dict):
        return
    with _core._CODEX_APP_SERVER_LOCK:
        for sid, saved in threads.items():
            if not isinstance(saved, dict):
                continue
            events = saved.get("coordination_events")
            recovery = saved.get("compaction_recovery")
            durable_activity = any(
                saved.get(key)
                for key in ("last_activity_at", "last_event_at", "last_turn_id")
            )
            if (
                not (isinstance(events, list) and events)
                and not isinstance(recovery, dict)
                and not durable_activity
            ):
                continue
            state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(str(sid), {})
            if isinstance(events, list) and events and not state.get("coordination_events"):
                state["coordination_events"] = [
                    e for e in events if isinstance(e, dict)
                ][-_CODEX_COORD_EVENTS_MAX:]
            if isinstance(recovery, dict) and not state.get("compaction_recovery"):
                state["compaction_recovery"] = dict(recovery)
            for key in ("last_activity_at", "last_event_at", "last_turn_id"):
                if saved.get(key) and not state.get(key):
                    state[key] = saved[key]


def _codex_coordination_event_unlocked(state, kind, writer=None, detail=None, now=None):
    """Append one coordination event to a thread state dict.

    Caller must hold _CODEX_APP_SERVER_LOCK (or own the dict exclusively) and
    is responsible for persisting.
    """
    event = {"ts": float(now if now is not None else time.time()), "kind": str(kind)}
    if writer:
        event["writer"] = str(writer)
    if detail:
        event["detail"] = str(detail)[:240]
    events = state.setdefault("coordination_events", [])
    events.append(event)
    del events[:-_CODEX_COORD_EVENTS_MAX]


def _codex_coordination_event(session_id, kind, writer=None, detail=None, now=None):
    """Append one durable coordination event to the thread's state + disk."""
    if not session_id or not kind:
        return
    _core._codex_load_coordination_state()
    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.setdefault(str(session_id), {})
        _core._codex_coordination_event_unlocked(state, kind, writer=writer, detail=detail, now=now)
        _core._save_codex_app_server_state_unlocked()


def _codex_note_external_writer_transition(session_id, snap):
    """Record external-writer start/stop transitions as durable events."""
    if not session_id or not isinstance(snap, dict):
        return
    cur = bool(snap.get("external_active"))
    with _codex_external_writer_last_lock:
        prev = _codex_external_writer_last.get(session_id)
        if prev == cur:
            return
        _codex_external_writer_last[session_id] = cur
        if prev is None and not cur:
            return  # first observation of a quiet thread — nothing to record
    writer = snap.get("writer") if cur else None
    if cur:
        detail = (
            "Turn detected from Codex desktop"
            if writer == "desktop"
            else "Active Codex turn detected"
        )
        _core._codex_coordination_event(
            session_id, "external_turn_started", writer=writer,
            detail=detail,
        )
    else:
        _core._codex_coordination_event(
            session_id, "external_turn_ended",
            detail="Active turn went quiet; CCC may send again",
        )


_CODEX_COORD_EVENT_TEXT = {
    "external_turn_started": "Active Codex turn detected",
    "external_turn_ended": "Active Codex turn finished - thread is free",
    "input_queued": "Message queued behind the active turn",
    "ccc_turn_started": "CCC started a turn via the app-server",
    "ccc_turn_completed": "CCC turn completed",
    "compaction_recovery_armed": "Watching for progress after compaction",
    "compaction_recovery_interrupting": "Interrupting a stalled post-compaction turn",
    "compaction_recovery_started": "Recovering after compaction",
    "compaction_recovery_recovered": "Codex recovered after compaction",
    "compaction_recovery_suppressed": "Compaction recovery suppressed",
    "compaction_recovery_exhausted": "Compaction recovery attempts exhausted",
    "turn_recovery_armed": "Watching a silent active goal turn",
    "turn_recovery_interrupting": "Interrupting a silent stalled Codex turn",
    "turn_recovery_started": "Recovering a stalled Codex turn",
    "turn_recovery_recovered": "Codex recovered from a silent turn",
    "turn_recovery_suppressed": "Silent-turn recovery handed off",
    "turn_recovery_exhausted": "Silent-turn recovery attempts exhausted",
}


def _codex_synthetic_event_ts_iso(ts):
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _get_codex_coordination_events_for_session(session_id):
    """Durable coordination events as synthetic conversation events.

    Each event carries a STABLE synthetic `line` id so the frontend's
    data-jsonl-line dedupe keeps re-polls idempotent.
    """
    if not session_id:
        return []
    _core._codex_load_coordination_state()
    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.get(session_id) or {}
        events = list(state.get("coordination_events") or [])
    out = []
    for ev in events[-_CODEX_COORD_EVENTS_TAIL:]:
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("kind") or "")
        ts = ev.get("ts")
        writer = ev.get("writer")
        text = _CODEX_COORD_EVENT_TEXT.get(kind) or kind.replace("_", " ")
        if kind == "external_turn_started" and writer == "desktop":
            text = "Codex desktop started a turn on this thread"
        # ts as ISO-8601: the frontend's tsSpan does `new Date(ts)`, which
        # reads a bare number as epoch MILLISECONDS — raw epoch seconds
        # would render as January 1970.
        ts_iso = _codex_synthetic_event_ts_iso(ts)
        out.append({
            "line": f"coord-{session_id[:8]}-{int(float(ts or 0) * 1000)}-{kind}",
            "ts": ts_iso,
            "type": "system",
            "subtype": "codex_coordination",
            "kind": kind,
            "writer": writer,
            "text": ev.get("detail") or text,
        })
    return out


def _codex_app_server_item_line(session_id, item, active=False):
    item_id = str((item or {}).get("id") or "").strip()
    if not item_id:
        try:
            item_id = str(int(float((item or {}).get("ts") or 0) * 1000))
        except (TypeError, ValueError, OverflowError):
            item_id = "unknown"
    suffix = "active" if active else "done"
    return f"appitem-{session_id[:8]}-{item_id}-{suffix}"


def _codex_app_server_item_event(session_id, item, *, active=False):
    if not session_id or not isinstance(item, dict):
        return None
    tool = str(item.get("tool") or item.get("type") or "").strip()
    detail = str(item.get("detail") or "").strip()
    output = str(item.get("output") or "").strip()
    item_type = str(item.get("type") or "").strip()
    needs_approval = bool(item.get("needs_approval"))
    overlay_item_types = _CODEX_APP_SERVER_APPROVAL_ITEM_TYPES | {
        "webSearch",
        "imageView",
        "imageGeneration",
        "subAgentActivity",
    }
    if not needs_approval and item_type not in overlay_item_types:
        return None
    if item_type == "reasoning" and tool == "Thinking" and not detail and not output:
        return None
    if not (tool or detail or output):
        return None
    ts = item.get("updated_at") or item.get("ts") or time.time()
    status = str(item.get("status") or ("inProgress" if active else "completed"))
    approval_message = str(item.get("approval_message") or "").strip()
    text_bits = []
    label = tool or item_type or "Codex"
    if needs_approval:
        text_bits.append(f"App-server live: {label} needs approval")
    elif active:
        text_bits.append(f"App-server live: {label} running")
    elif item.get("is_error"):
        text_bits.append(f"App-server live: {label} failed")
    else:
        text_bits.append(f"App-server live: {label} completed")
    if approval_message:
        text_bits.append(approval_message)
    elif detail:
        text_bits.append(detail)
    elif output:
        text_bits.append(output)
    return {
        "line": _codex_app_server_item_line(session_id, item, active=active),
        "ts": _codex_synthetic_event_ts_iso(ts),
        "type": "system",
        "subtype": "codex_app_server_item",
        "item_type": item_type,
        "tool": tool,
        "detail": detail,
        "output": output,
        "status": status,
        "in_flight": bool(active or item.get("in_flight")),
        "is_error": bool(item.get("is_error")),
        "needs_approval": needs_approval,
        "approval_message": approval_message,
        "text": " - ".join(text_bits),
    }


def _get_codex_app_server_item_events_for_session(session_id):
    """Recent app-server item notifications as synthetic conversation rows.

    These are intentionally marked as app-server live overlays, not durable
    transcript rows. The rollout JSONL remains authoritative history, but these
    rows expose tool/message notifications that can arrive before the rollout
    tail flushes.
    """
    if not session_id:
        return []
    state = _core._codex_app_server_thread_state(session_id)
    if not state:
        return []
    out = []
    seen = set()

    def add(item, *, active=False):
        ev = _codex_app_server_item_event(session_id, item, active=active)
        if not ev:
            return
        key = ev.get("line")
        if key in seen:
            return
        seen.add(key)
        out.append(ev)

    recent = state.get("recent_items")
    if isinstance(recent, list):
        for item in recent[-8:]:
            add(item, active=False)
    active_item = state.get("active_item") if isinstance(state.get("active_item"), dict) else None
    if active_item:
        add(active_item, active=True)
    try:
        out.sort(key=lambda ev: datetime.fromisoformat(str(ev.get("ts") or "").replace("Z", "+00:00")).timestamp())
    except Exception:
        pass
    return out


def _codex_writer_gate_response(session_id, snap, *, stage="writer-gate",
                                total_start=None, extra=None):
    """Uniform fallback:"queue" response for a blocked CCC send."""
    writer = snap.get("writer")
    if writer == "desktop":
        reason = (
            "Codex desktop is running a turn on this thread - the message is "
            "queued and CCC will send it when the desktop turn finishes"
        )
    elif writer == "ccc":
        reason = "Another CCC send is already running a turn on this thread - queued"
    else:
        reason = "An active Codex turn is writing this thread - queued until it finishes"
    approval_hint = _codex_pending_approval_hint(session_id)
    if approval_hint:
        reason += (
            f" — the turn is blocked on a Codex approval: {approval_hint}. "
            "Answer it (approve button or POST /api/codex/approval) to "
            "release the queue"
        )
    _core._resume_ledger_append(
        "codex_wake_queued", sid=session_id, stage=stage, reason=reason,
    )
    _core._codex_telemetry_append(
        "codex_wake",
        ok=False,
        via="codex-app-server",
        fallback="queue",
        fallback_reason=reason,
        stage=stage,
        writer=writer,
        desktop_attached=bool(snap.get("desktop_attached")),
        session_id=session_id,
        total_ms=_codex_elapsed_ms(total_start) if total_start is not None else None,
    )
    _core._codex_coordination_event(session_id, "input_queued", writer=writer)
    resp = {
        "ok": False,
        "via": "codex-app-server",
        "stage": stage,
        "fallback": "queue",
        "error": reason,
        "writer": writer,
        "desktop_attached": bool(snap.get("desktop_attached")),
    }
    if approval_hint:
        resp["pending_approval"] = approval_hint
    if extra:
        resp.update(extra)
    return resp


def _codex_turn_params(thread_id, text, cwd=None, model=None, image_paths=None, effort=None):
    params = {
        "threadId": thread_id,
        "input": _codex_user_input(text, image_paths=image_paths),
    }
    if cwd:
        params["cwd"] = cwd
        params["runtimeWorkspaceRoots"] = [cwd]
    if model:
        params["model"] = model
    if effort:
        params["effort"] = effort
    # Match the existing `codex exec --dangerously-bypass-approvals-and-sandbox`
    # behavior used by CCC-spawned Codex runs.
    params["approvalPolicy"] = "never"
    params["sandboxPolicy"] = {"type": "dangerFullAccess"}
    return params


def _codex_app_server_spawn_enabled():
    for key in ("CCC_CODEX_APP_SERVER", "CCC_CODEX_SPAWN_APP_SERVER"):
        if os.environ.get(key, "1").lower() in ("0", "false", "no"):
            return False
    return True


def _codex_app_server_thread_start_params(cwd=None, model=None):
    params = {
        "approvalPolicy": "never",
        "sandbox": "danger-full-access",
        "threadSource": "ccc",
        "sessionStartSource": "startup",
        "ephemeral": False,
    }
    if cwd:
        params["cwd"] = cwd
        params["runtimeWorkspaceRoots"] = [cwd]
    if model:
        params["model"] = model
    if _core._codex_context_1m_enabled():
        params["config"] = {"model_context_window": 1000000}
    return params


def _codex_app_server_response_error(response, default="Codex app-server request failed"):
    if not isinstance(response, dict):
        return default
    return _codex_error_text(response) or str(response.get("error") or default)


def _codex_app_server_normalize_approval_decision(decision):
    raw = str(decision or "").strip()
    token = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    mapping = {
        "accept": "accept",
        "approve": "accept",
        "approved": "accept",
        "yes": "accept",
        "acceptforsession": "acceptForSession",
        "accept_for_session": "acceptForSession",
        "approve_session": "acceptForSession",
        "approved_for_session": "acceptForSession",
        "session": "acceptForSession",
        "decline": "decline",
        "deny": "decline",
        "denied": "decline",
        "no": "decline",
        "cancel": "cancel",
        "abort": "cancel",
    }
    return mapping.get(token) or mapping.get(raw) or ""


def _codex_app_server_approval_result_payload(method, decision, pending):
    canonical = _codex_app_server_normalize_approval_decision(decision)
    if not canonical:
        return None, "invalid approval decision"
    if method in _CODEX_APP_SERVER_COMMAND_APPROVAL_METHODS or method in _CODEX_APP_SERVER_FILE_APPROVAL_METHODS:
        if method in ("execCommandApproval", "applyPatchApproval"):
            legacy = {
                "accept": "approved",
                "acceptForSession": "approved_for_session",
                "decline": "denied",
                "cancel": "abort",
            }
            return {"decision": legacy[canonical]}, ""
        return {"decision": canonical}, ""
    if method in _CODEX_APP_SERVER_PERMISSION_APPROVAL_METHODS:
        scope = "session" if canonical == "acceptForSession" else "turn"
        permissions = pending.get("requested_permissions") if isinstance(pending, dict) else None
        if canonical in ("decline", "cancel"):
            permissions = {}
        if not isinstance(permissions, dict):
            return None, "permission approval request did not include a grantable permissions profile"
        return {"permissions": permissions, "scope": scope}, ""
    return None, f"unsupported approval method: {method}"


def _codex_app_server_resolve_approval(session_id, decision):
    routed = _core._control_plane_engine_call(
        "codex", "approval", {
            "session_id": session_id,
            "decision": decision,
        },
    )
    if routed is not None:
        return routed
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "via": "codex-approval", "error": "missing session_id"}
    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) if sid else None
        pending = _codex_app_server_pending_approval_item(state or {})
        if not pending:
            return {
                "ok": False,
                "via": "codex-approval",
                "code": "codex_no_pending_approval",
                "error": "No pending Codex approval request for this session",
            }
        request_id = pending.get("request_id_raw")
        if request_id is None:
            request_id = pending.get("request_id")
        method = pending.get("approval_method") or ""
        if request_id is None or (isinstance(request_id, str) and not request_id.strip()):
            return {
                "ok": False,
                "via": "codex-approval",
                "code": "codex_approval_not_actionable",
                "error": "Codex approval is visible but does not include an app-server request id",
            }
        result, error = _codex_app_server_approval_result_payload(method, decision, pending)
        if error:
            return {
                "ok": False,
                "via": "codex-approval",
                "code": "codex_approval_not_actionable",
                "error": error,
            }

    transport = _core._ensure_codex_app_server()
    if transport is None:
        return {
            "ok": False,
            "via": "codex-approval",
            "code": "codex_app_server_unavailable",
            "error": "Codex app-server transport unavailable",
        }

    response = {"jsonrpc": "2.0", "id": request_id, "result": result}
    try:
        transport.send_json(response)
    except (BrokenPipeError, OSError) as e:
        return {
            "ok": False,
            "via": "codex-approval",
            "code": "codex_approval_send_failed",
            "error": str(e),
        }

    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) if sid else None
        if isinstance(state, dict):
            current = state.get("pending_approval_request")
            if isinstance(current, dict) and (
                current.get("request_id_raw") == request_id
                or current.get("request_id") == request_id
                or str(current.get("request_id") or "") == str(request_id)
            ):
                state.pop("pending_approval_request", None)
            flags = [
                flag for flag in (state.get("active_flags") or [])
                if not _codex_app_server_flags_need_approval([flag])
            ]
            state["active_flags"] = flags
            state["thread_needs_approval"] = False
            now = time.time()
            for key in ("active_item", "last_item"):
                item = state.get(key)
                if isinstance(item, dict) and (
                    item.get("request_id_raw") == request_id
                    or item.get("request_id") == request_id
                    or str(item.get("request_id") or "") == str(request_id)
                ):
                    item["needs_approval"] = False
                    item["can_approve"] = False
                    item["status"] = "approval_response_sent"
                    item["in_flight"] = True
                    item["updated_at"] = now
            active = state.get("active_items")
            if isinstance(active, dict):
                for item in active.values():
                    if isinstance(item, dict) and (
                        item.get("request_id_raw") == request_id
                        or item.get("request_id") == request_id
                        or str(item.get("request_id") or "") == str(request_id)
                    ):
                        item["needs_approval"] = False
                        item["can_approve"] = False
                        item["status"] = "approval_response_sent"
                        item["in_flight"] = True
                        item["updated_at"] = now
            state["last_activity_at"] = now
            _core._save_codex_app_server_state_unlocked()
        _core._CODEX_APP_SERVER_LOCK.notify_all()
    return {
        "ok": True,
        "via": "codex-approval",
        "session_id": sid,
        "request_id": request_id,
        "approval_method": method,
        "decision": _codex_app_server_normalize_approval_decision(decision),
    }


def _codex_spawn_id_for_thread(thread_id):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(thread_id or "")).strip("-")
    cleaned = cleaned[:32] or str(int(time.time() * 1000))
    return f"codex-app-{cleaned}"


def _codex_spawn_via_app_server(
    prompt,
    *,
    session_name,
    spawn_cwd,
    repo_for_logs,
    model_to_use,
    reasoning_effort="",
    image_paths=None,
    parent_session_id=None,
    timestamp=None,
    worktree_path=None,
    worktree_branch=None,
    parent_repo=None,
):
    """Start a fresh Codex thread through the app-server.

    Returns None when callers should fall back to the legacy `codex exec` path.
    Once a durable thread has been created, errors are returned to the caller
    instead of launching a second session for the same requested task — except
    "thread not found" from turn/start, which proves the turn was never
    accepted (even after reattach + recreate recovery), so the exec fallback
    is safe and preferred over rejecting the user's submission.
    """
    if not _codex_app_server_spawn_enabled():
        _core._codex_telemetry_append(
            "codex_spawn",
            ok=False,
            via="codex-app-spawn",
            fallback="codex-exec",
            fallback_reason="app-server-disabled",
            cwd=spawn_cwd,
            model=model_to_use,
        )
        return None
    timestamp = timestamp or time.strftime("%Y%m%dT%H%M%S")
    total_start = time.monotonic()
    app_server_warm = _core._codex_app_server_is_live()
    thread_start_params = _codex_app_server_thread_start_params(
        cwd=spawn_cwd,
        model=model_to_use,
    )
    thread_start_at = time.monotonic()
    start = _core._codex_app_server_request(
        "thread/start",
        thread_start_params,
        timeout=20,
    )
    thread_start_ms = _codex_elapsed_ms(thread_start_at)
    if not _core._codex_response_succeeded(start):
        _core._codex_telemetry_append(
            "codex_spawn",
            ok=False,
            via="codex-app-spawn",
            fallback="codex-exec",
            fallback_reason="thread/start failed",
            stage="thread/start",
            error=_codex_app_server_response_error(start),
            app_server_warm=app_server_warm,
            thread_start_ms=thread_start_ms,
            total_ms=_codex_elapsed_ms(total_start),
            transport=_core._codex_app_server_transport_kind(),
            cwd=spawn_cwd,
            model=model_to_use,
        )
        return None
    thread = ((start.get("result") or {}).get("thread") or {})
    thread_id = thread.get("id")
    if not thread_id:
        return None
    thread_id = str(thread_id)
    _core._codex_app_server_record_thread(thread_id, thread)
    # Backdate t0 to the start of the spawn so thread_start is measured from
    # when the user actually asked, not from when Codex answered.
    _core._spawn_timeline_start(
        thread_id,
        engine="codex",
        name=session_name,
        cwd=spawn_cwd,
        model=model_to_use or "",
        app_server_warm=app_server_warm,
    )
    with _SPAWN_TIMELINE_LOCK:
        _entry = _core._SPAWN_TIMELINE.get(thread_id)
        if _entry:
            _entry["t0"] = time.time() - (time.monotonic() - total_start)
    _core._spawn_timeline_mark(thread_id, "thread_start_done", thread_start_ms)

    # thread/name/set costs a measured ~1.7s and turn/start does not depend on
    # it (turn/start takes only threadId and returns in 1-2ms). Running it here
    # delayed the user's prompt by that much for a cosmetic label, so it now
    # runs in _codex_finalize_spawn_async after the turn is already going.
    # Stays None on every synchronous path, including the exec-fallback
    # telemetry below, which reads it before the finalizer exists.
    name_set_ms = None

    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"spawn-codex-app-{session_name}-{timestamp}.log"
    log_fh = open(log_path, "w")
    try:
        log_fh.write(json.dumps({
            "event": "codex_app_server_spawn",
            "thread_id": thread_id,
            "name": session_name,
            "cwd": spawn_cwd,
            "model": model_to_use or "",
            "reasoning_effort": reasoning_effort or "",
            "transport": _core._codex_app_server_transport_kind(),
        }, sort_keys=True) + "\n")
        log_fh.flush()
        if worktree_path:
            _core._run_worktree_init_hook(worktree_path, parent_repo or repo_for_logs, session_name, log_fh)

        baseline_state = _core._codex_app_server_thread_state(thread_id)
        baseline_rollout = _core._codex_rollout_stat(thread_id)
        turn_params = _codex_turn_params(
            thread_id,
            prompt,
            cwd=spawn_cwd,
            model=model_to_use,
            image_paths=image_paths,
            effort=reasoning_effort or None,
        )
        turn_start_at = time.monotonic()
        started = _core._codex_app_server_request("turn/start", turn_params, timeout=20)
        thread_reattached = False
        thread_recreated = False
        recovery_thread_start_ms = None
        # A newly-created thread can be reported before its runtime has made it
        # available to the first turn/start request. This error is definitive
        # (the turn was not accepted), so one resume + retry is safe and avoids
        # rejecting an otherwise valid new-session submission.
        if (not _core._codex_response_succeeded(started)
                and "thread not found" in _codex_error_text(started).lower()):
            resume_params = {"threadId": thread_id, "excludeTurns": False}
            if spawn_cwd:
                resume_params["cwd"] = spawn_cwd
            if model_to_use:
                resume_params["model"] = model_to_use
            resumed = _core._codex_app_server_request("thread/resume", resume_params, timeout=20)
            if _core._codex_response_succeeded(resumed):
                recovered_thread = ((resumed.get("result") or {}).get("thread") or {})
                _core._codex_app_server_record_thread(thread_id, recovered_thread)
                started = _core._codex_app_server_request("turn/start", turn_params, timeout=20)
                thread_reattached = _core._codex_response_succeeded(started)
            # A failed resume (or a resume whose retry still cannot find the
            # thread) means the long-lived app-server accepted thread/start
            # without creating durable runtime state. Recycle CCC's child and
            # recreate exactly once; retrying forever could duplicate a turn.
            if (not _core._codex_response_succeeded(started)
                    and "thread not found" in _codex_error_text(started).lower()):
                stale_thread_id = thread_id
                _core._codex_app_server_shutdown()
                recovery_start_at = time.monotonic()
                restarted = _core._codex_app_server_request(
                    "thread/start",
                    thread_start_params,
                    timeout=20,
                )
                recovery_thread_start_ms = _codex_elapsed_ms(recovery_start_at)
                replacement = ((restarted.get("result") or {}).get("thread") or {})
                replacement_id = replacement.get("id")
                if _core._codex_response_succeeded(restarted) and replacement_id:
                    thread_id = str(replacement_id)
                    _core._codex_app_server_record_thread(thread_id, replacement)
                    baseline_state = _core._codex_app_server_thread_state(thread_id)
                    baseline_rollout = _core._codex_rollout_stat(thread_id)
                    turn_params = _codex_turn_params(
                        thread_id,
                        prompt,
                        cwd=spawn_cwd,
                        model=model_to_use,
                        image_paths=image_paths,
                        effort=reasoning_effort or None,
                    )
                    # Naming is deferred here too — the recreated thread is
                    # named by _codex_finalize_spawn_async once its turn is
                    # accepted, so recovery does not pay the rename twice.
                    started = _core._codex_app_server_request(
                        "turn/start",
                        turn_params,
                        timeout=20,
                    )
                    thread_recreated = _core._codex_response_succeeded(started)
                    log_fh.write(json.dumps({
                        "event": "codex_app_server_recreated",
                        "stale_thread_id": stale_thread_id,
                        "thread_id": thread_id,
                        "turn_started": thread_recreated,
                    }, sort_keys=True) + "\n")
                    log_fh.flush()
                else:
                    started = (
                        restarted
                        if not _core._codex_response_succeeded(restarted)
                        else {"error": {
                            "message": "Codex app-server recreated a thread without an id",
                        }}
                    )
        turn_start_ms = _codex_elapsed_ms(turn_start_at)
        if not _core._codex_response_succeeded(started):
            error = _codex_app_server_response_error(started)
            # "thread not found" is definitive: the app-server never accepted
            # the turn, so no work ran on this thread. After the reattach +
            # recreate recovery above has also failed, the app-server is
            # persistently unable to run turns (wedged child, broken shared
            # state) — falling back to the one-shot `codex exec` path cannot
            # duplicate the task, unlike other errors (e.g. a lost response
            # after the turn was accepted) where a second session could.
            thread_lost = "thread not found" in error.lower()
            log_fh.write(json.dumps({
                "event": "codex_app_server_turn_failed",
                "error": error,
                "fallback": "codex-exec" if thread_lost else "none",
            }, sort_keys=True) + "\n")
            log_fh.flush()
            if thread_lost:
                _core._codex_telemetry_append(
                    "codex_spawn",
                    ok=False,
                    via="codex-app-spawn",
                    fallback="codex-exec",
                    fallback_reason="turn/start thread-not-found after recovery",
                    stage="turn/start",
                    error=error,
                    app_server_warm=app_server_warm,
                    thread_start_ms=thread_start_ms,
                    name_set_ms=name_set_ms,
                    turn_start_ms=turn_start_ms,
                    thread_reattached=thread_reattached,
                    thread_recreated=thread_recreated,
                    recovery_thread_start_ms=recovery_thread_start_ms,
                    total_ms=_codex_elapsed_ms(total_start),
                    transport=_core._codex_app_server_transport_kind(),
                    session_id=thread_id,
                    cwd=spawn_cwd,
                    model=model_to_use,
                )
                return None
            _core._codex_telemetry_append(
                "codex_spawn",
                ok=False,
                via="codex-app-spawn",
                fallback="none-durable-thread-created",
                fallback_reason="turn/start failed after thread/start",
                stage="turn/start",
                error=error,
                app_server_warm=app_server_warm,
                thread_start_ms=thread_start_ms,
                name_set_ms=name_set_ms,
                turn_start_ms=turn_start_ms,
                thread_reattached=thread_reattached,
                thread_recreated=thread_recreated,
                recovery_thread_start_ms=recovery_thread_start_ms,
                total_ms=_codex_elapsed_ms(total_start),
                transport=_core._codex_app_server_transport_kind(),
                session_id=thread_id,
                cwd=spawn_cwd,
                model=model_to_use,
            )
            return {
                "ok": False,
                "error": error,
                "code": "codex_app_spawn_failed",
                "via": "codex-app-spawn",
                "session_id": thread_id,
                "log": str(log_path),
            }

        turn = ((started.get("result") or {}).get("turn") or {})
        turn_id = turn.get("id")
        _core._codex_telemetry_register_turn(
            thread_id,
            turn_id,
            path="spawn",
            started_at_monotonic=turn_start_at,
            transport=_core._codex_app_server_transport_kind(),
            cwd=spawn_cwd,
            model=model_to_use,
        )
        # The turn is accepted and already running. Naming the thread and
        # proving the prompt landed are both diagnostics that no caller branches
        # on, and together they cost up to ~6.6s (measured: rename ~1.7s,
        # confirmation p75 ~5.0s). Hand them to a background thread so the
        # spawn response returns as soon as Codex has the work.
        log_fh.write(json.dumps({
            "event": "codex_app_server_turn_started",
            "turn_id": turn_id,
            "confirmed": None,
            "confirmation_source": "pending",
            "thread_reattached": thread_reattached,
            "thread_recreated": thread_recreated,
        }, sort_keys=True) + "\n")
        log_fh.flush()
        _core._spawn_timeline_mark(thread_id, "turn_accepted")
        confirm_ms = None
        confirmation = {"confirmed": None, "source": "pending", "warning": None}
        _core._codex_finalize_spawn_async(
            thread_id,
            turn_id,
            session_name=session_name,
            prompt=prompt,
            log_path=log_path,
            baseline_state=baseline_state,
            baseline_rollout=baseline_rollout,
            cwd=spawn_cwd,
        )
        total_ms = _codex_elapsed_ms(total_start)
        _core._codex_telemetry_append(
            "codex_spawn",
            ok=True,
            via="codex-app-spawn",
            app_server_warm=app_server_warm,
            thread_start_ms=thread_start_ms,
            name_set_ms=name_set_ms,
            turn_start_ms=turn_start_ms,
            confirm_ms=confirm_ms,
            total_ms=total_ms,
            thread_reattached=thread_reattached,
            thread_recreated=thread_recreated,
            recovery_thread_start_ms=recovery_thread_start_ms,
            confirmed=None,
            confirmation_source="pending",
            warning=None,
            transport=_core._codex_app_server_transport_kind(),
            session_id=thread_id,
            turn_id=turn_id,
            cwd=spawn_cwd,
            model=model_to_use,
        )
    finally:
        log_fh.close()

    spawn_id = _codex_spawn_id_for_thread(thread_id)
    entry = {
        "pid": spawn_id,
        "spawn_id": spawn_id,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": None,
        "log_fh": None,
        "fifo": None,
        "stdin_fd": None,
        "engine": "codex",
        "cwd": spawn_cwd,
        "repo_path": repo_for_logs,
        "model": model_to_use or "",
        "parent_session_id": parent_session_id or "",
        "session_id": thread_id,
        "app_server_spawn": True,
        "turn_id": turn_id,
        "confirmed": confirmation.get("confirmed"),
        "confirmation_source": confirmation.get("source"),
        "app_server_warm": app_server_warm,
        "thread_start_ms": thread_start_ms,
        "name_set_ms": name_set_ms,
        "turn_start_ms": turn_start_ms,
        "confirm_ms": confirm_ms,
        "latency_ms": total_ms,
        "thread_recreated": thread_recreated,
    }
    _core._spawned_sessions.append(entry)
    _core._codex_thread_registry_upsert(
        thread_id,
        source="ccc-spawn",
        visibility="user-visible",
        transport_owner="ccc-managed-app-server",
        transport=_core._codex_app_server_transport_kind(),
        cwd=spawn_cwd,
        repo_path=repo_for_logs,
        title=session_name,
        name=session_name,
        parent_session_id=parent_session_id or "",
        model=model_to_use or "",
        ccc={
            "spawn_id": spawn_id,
            "log": str(log_path),
            "spawned_at": timestamp,
            "prompt": prompt[:200],
            "app_server_spawn": True,
            "worktree_path": worktree_path or "",
            "worktree_branch": worktree_branch or "",
        },
    )
    _core._set_session_override(thread_id, model_to_use, False, "codex", reasoning_effort)
    resp = {
        "ok": True,
        "pid": spawn_id,
        "spawn_id": spawn_id,
        "name": session_name,
        "log": str(log_path),
        "via": "codex-app-spawn",
        "accepted": True,
        # None (not False) until _codex_finalize_spawn_async lands: the turn
        # was accepted, the durability check just has not reported yet.
        "confirmed": confirmation.get("confirmed"),
        "confirmation_source": confirmation.get("source"),
        "warning": confirmation.get("warning") or None,
        "turn_id": turn_id,
        "session_id": thread_id,
        "app_server_transport": _core._codex_app_server_transport_kind(),
        "app_server_warm": app_server_warm,
        "thread_start_ms": thread_start_ms,
        "name_set_ms": name_set_ms,
        "turn_start_ms": turn_start_ms,
        "confirm_ms": confirm_ms,
        "latency_ms": total_ms,
        "thread_recreated": thread_recreated,
    }
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    _core._spawn_timeline_mark(thread_id, "spawn_response_sent")
    _core._spawn_timeline_save()
    return _core._finalize_spawn_response(resp, entry, {"cwd": spawn_cwd, "repo_path": repo_for_logs}, wait_for_session_id=False)


def _codex_resume_or_steer_via_app_server(
    session_id,
    text,
    cwd=None,
    model=None,
    image_paths=None,
    allow_start=True,
    reasoning_effort=None,
):
    """Write-gated wrapper: single-writer protocol for shared Codex threads.

    Blocks turn/start while an external writer (Codex desktop, CLI) is
    mid-turn on the thread, and serializes concurrent CCC sends with a
    per-thread mutex. Blocked sends return fallback:"queue" — the caller
    parks the text in the durable pending-input queue and the resume-queue
    watcher retries once the thread goes quiet.
    """
    total_start = time.monotonic()
    try:
        snap = _core._codex_thread_writer_snapshot(session_id)
    except Exception:
        snap = {}
    if snap.get("external_active"):
        _core._codex_note_external_writer_transition(session_id, snap)
        return _codex_writer_gate_response(session_id, snap, total_start=total_start)
    lock = _core._codex_thread_turn_lock(session_id)
    if not lock.acquire(blocking=False):
        return _codex_writer_gate_response(
            session_id, {"writer": "ccc", "desktop_attached": snap.get("desktop_attached")},
            total_start=total_start,
        )
    try:
        return _codex_resume_or_steer_via_app_server_locked(
            session_id,
            text,
            cwd=cwd,
            model=model,
            image_paths=image_paths,
            allow_start=allow_start,
            reasoning_effort=reasoning_effort,
        )
    finally:
        lock.release()


def _codex_resume_or_steer_via_app_server_locked(
    session_id,
    text,
    cwd=None,
    model=None,
    image_paths=None,
    allow_start=True,
    reasoning_effort=None,
):
    if os.environ.get("CCC_CODEX_APP_SERVER", "1").lower() in ("0", "false", "no"):
        _core._codex_telemetry_append(
            "codex_wake",
            ok=False,
            via="codex-app-server",
            fallback="exec",
            fallback_reason="app-server-disabled",
            stage="disabled",
            session_id=session_id,
            cwd=cwd,
            model=model,
        )
        return {"ok": False, "fallback": "exec", "error": "Codex app-server disabled"}
    total_start = time.monotonic()
    app_server_warm = _core._codex_app_server_is_live()
    resume_params = {
        "threadId": session_id,
        "excludeTurns": False,
    }
    if cwd:
        resume_params["cwd"] = cwd
    if model:
        resume_params["model"] = model
    if reasoning_effort:
        resume_params["effort"] = reasoning_effort
    # Finer stage breadcrumb for the live wake-status breakdown (diagnostic
    # only; wrapped so it can never affect control flow).
    try:
        _core._resume_ledger_append("codex_wake_stage", sid=session_id, stage="connect")
    except Exception:
        pass
    resume_at = time.monotonic()
    resumed = _core._codex_app_server_request("thread/resume", resume_params, timeout=20)
    resume_ms = _codex_elapsed_ms(resume_at)
    if resumed.get("error"):
        _err = _codex_error_text(resumed)
        _core._resume_ledger_append(
            "codex_wake_fail", sid=session_id,
            stage="thread/resume", error=_err, fallback="exec",
        )
        _core._codex_telemetry_append(
            "codex_wake",
            ok=False,
            via="codex-app-server",
            fallback="exec",
            fallback_reason="thread/resume failed",
            stage="thread/resume",
            error=_err,
            app_server_warm=app_server_warm,
            resume_ms=resume_ms,
            total_ms=_codex_elapsed_ms(total_start),
            transport=_core._codex_app_server_transport_kind(),
            session_id=session_id,
            cwd=cwd,
            model=model,
        )
        return {
            "ok": False,
            "via": "codex-app-server",
            "stage": "thread/resume",
            "fallback": "exec",
            "error": _err,
            "app_server_warm": app_server_warm,
            "resume_ms": resume_ms,
            "latency_ms": _codex_elapsed_ms(total_start),
        }
    thread = ((resumed.get("result") or {}).get("thread") or {})
    active_turn = _codex_latest_active_turn(thread)
    status = (thread.get("status") or {}).get("type")
    if status == "active" and active_turn:
        _reason = "Codex thread is active; queue the next message durably in CCC"
        _core._resume_ledger_append(
            "codex_wake_queued", sid=session_id,
            stage="thread/resume", reason=_reason,
        )
        _core._codex_telemetry_append(
            "codex_wake",
            ok=False,
            via="codex-app-server",
            fallback="queue",
            fallback_reason=_reason,
            stage="thread/resume",
            app_server_warm=app_server_warm,
            resume_ms=resume_ms,
            total_ms=_codex_elapsed_ms(total_start),
            transport=_core._codex_app_server_transport_kind(),
            session_id=session_id,
            cwd=cwd,
            model=model,
        )
        return {
            "ok": False,
            "via": "codex-app-server",
            "stage": "thread/resume",
            "fallback": "queue",
            "error": _reason,
            "app_server_warm": app_server_warm,
            "resume_ms": resume_ms,
            "latency_ms": _codex_elapsed_ms(total_start),
        }
    if status == "active":
        _reason = "Codex app-server reports an active turn without a steerable turn id"
        _core._resume_ledger_append(
            "codex_wake_queued", sid=session_id,
            stage="thread/resume", reason=_reason,
        )
        _core._codex_telemetry_append(
            "codex_wake",
            ok=False,
            via="codex-app-server",
            fallback="queue",
            fallback_reason=_reason,
            stage="thread/resume",
            app_server_warm=app_server_warm,
            resume_ms=resume_ms,
            total_ms=_codex_elapsed_ms(total_start),
            transport=_core._codex_app_server_transport_kind(),
            session_id=session_id,
            cwd=cwd,
            model=model,
        )
        return {
            "ok": False,
            "via": "codex-app-server",
            "stage": "thread/resume",
            "fallback": "queue",
            "error": _reason,
            "app_server_warm": app_server_warm,
            "resume_ms": resume_ms,
            "latency_ms": _codex_elapsed_ms(total_start),
        }
    if not allow_start:
        _reason = "Codex CLI resume is still running and app-server did not expose a steerable turn"
        _core._resume_ledger_append(
            "codex_wake_queued", sid=session_id,
            stage="thread/resume", reason=_reason,
        )
        _core._codex_telemetry_append(
            "codex_wake",
            ok=False,
            via="codex-app-server",
            fallback="queue",
            fallback_reason=_reason,
            stage="thread/resume",
            app_server_warm=app_server_warm,
            resume_ms=resume_ms,
            total_ms=_codex_elapsed_ms(total_start),
            transport=_core._codex_app_server_transport_kind(),
            session_id=session_id,
            cwd=cwd,
            model=model,
        )
        return {
            "ok": False,
            "via": "codex-app-server",
            "stage": "thread/resume",
            "fallback": "queue",
            "error": _reason,
            "app_server_warm": app_server_warm,
            "resume_ms": resume_ms,
            "latency_ms": _codex_elapsed_ms(total_start),
        }

    try:
        _core._resume_ledger_append("codex_wake_stage", sid=session_id, stage="turn-start")
    except Exception:
        pass
    baseline_state = _core._codex_app_server_thread_state(session_id)
    baseline_rollout = _core._codex_rollout_stat(session_id)
    # Re-check the writer gate at the last possible moment: the thread/resume
    # above takes seconds, plenty of time for the desktop user to hit enter.
    # Attachment is TTL-cached; the ACTIVITY read (rollout stat) is fresh.
    try:
        snap = _core._codex_thread_writer_snapshot(
            session_id, rollout=baseline_rollout, app_state=baseline_state,
        )
    except Exception:
        snap = {}
    if snap.get("external_active"):
        _core._codex_note_external_writer_transition(session_id, snap)
        return _codex_writer_gate_response(
            session_id, snap, stage="turn/start-gate", total_start=total_start,
            extra={"app_server_warm": app_server_warm, "resume_ms": resume_ms},
        )
    turn_start_at = time.monotonic()
    started = _core._codex_app_server_request(
        "turn/start",
        _codex_turn_params(session_id, text, cwd=cwd, model=model, image_paths=image_paths, effort=reasoning_effort),
        timeout=20,
    )
    turn_start_ms = _codex_elapsed_ms(turn_start_at)
    if _core._codex_response_succeeded(started):
        turn = ((started.get("result") or {}).get("turn") or {})
        _turn_id = turn.get("id")
        _core._codex_telemetry_register_turn(
            session_id,
            _turn_id,
            path="wake",
            started_at_monotonic=turn_start_at,
            transport=_core._codex_app_server_transport_kind(),
            cwd=cwd,
            model=model,
        )
        _core._resume_ledger_append(
            "codex_wake_ok", sid=session_id,
            via="codex-app-turn", turn_id=_turn_id,
        )
        try:
            confirm_timeout = float(os.environ.get("CCC_CODEX_WAKE_CONFIRM_TIMEOUT", "5"))
        except ValueError:
            confirm_timeout = 5.0
        confirm_at = time.monotonic()
        confirmation = _core._codex_wait_for_turn_activity(
            session_id,
            _turn_id,
            baseline_state=baseline_state,
            baseline_rollout=baseline_rollout,
            expected_text=text,
            timeout=confirm_timeout,
        )
        confirm_ms = _codex_elapsed_ms(confirm_at)
        if not confirmation.get("confirmed"):
            _core._resume_ledger_append(
                "codex_wake_warn", sid=session_id,
                via="codex-app-turn", turn_id=_turn_id,
                warning=confirmation.get("warning"),
            )
        total_ms = _codex_elapsed_ms(total_start)
        _core._codex_telemetry_append(
            "codex_wake",
            ok=True,
            via="codex-app-turn",
            app_server_warm=app_server_warm,
            resume_ms=resume_ms,
            turn_start_ms=turn_start_ms,
            confirm_ms=confirm_ms,
            total_ms=total_ms,
            confirmed=bool(confirmation.get("confirmed")),
            confirmation_source=confirmation.get("source"),
            warning=confirmation.get("warning"),
            transport=_core._codex_app_server_transport_kind(),
            session_id=session_id,
            turn_id=_turn_id,
            cwd=cwd,
            model=model,
        )
        return {
            "ok": True,
            "via": "codex-app-turn",
            "accepted": True,
            "confirmed": bool(confirmation.get("confirmed")),
            "confirmation_source": confirmation.get("source"),
            "warning": confirmation.get("warning"),
            "turn_id": _turn_id,
            "session_id": session_id,
            "app_server_transport": _core._codex_app_server_transport_kind(),
            "app_server_warm": app_server_warm,
            "resume_ms": resume_ms,
            "turn_start_ms": turn_start_ms,
            "confirm_ms": confirm_ms,
            "latency_ms": total_ms,
        }
    if _core._codex_error_is_not_steerable(started):
        _err = _codex_error_text(started)
        _core._resume_ledger_append(
            "codex_wake_fail", sid=session_id,
            stage="turn/start", error=_err, fallback="queue",
        )
        _core._codex_telemetry_append(
            "codex_wake",
            ok=False,
            via="codex-app-server",
            fallback="queue",
            fallback_reason="turn/start not steerable",
            stage="turn/start",
            error=_err,
            app_server_warm=app_server_warm,
            resume_ms=resume_ms,
            turn_start_ms=turn_start_ms,
            total_ms=_codex_elapsed_ms(total_start),
            transport=_core._codex_app_server_transport_kind(),
            session_id=session_id,
            cwd=cwd,
            model=model,
        )
        return {
            "ok": False,
            "via": "codex-app-server",
            "stage": "turn/start",
            "fallback": "queue",
            "error": _err,
            "app_server_warm": app_server_warm,
            "resume_ms": resume_ms,
            "turn_start_ms": turn_start_ms,
            "latency_ms": _codex_elapsed_ms(total_start),
        }
    _err = _codex_error_text(started)
    _core._resume_ledger_append(
        "codex_wake_fail", sid=session_id,
        stage="turn/start", error=_err, fallback="exec",
    )
    _core._codex_telemetry_append(
        "codex_wake",
        ok=False,
        via="codex-app-server",
        fallback="exec",
        fallback_reason="turn/start failed",
        stage="turn/start",
        error=_err,
        app_server_warm=app_server_warm,
        resume_ms=resume_ms,
        turn_start_ms=turn_start_ms,
        total_ms=_codex_elapsed_ms(total_start),
        transport=_core._codex_app_server_transport_kind(),
        session_id=session_id,
        cwd=cwd,
        model=model,
    )
    return {
        "ok": False,
        "via": "codex-app-server",
        "stage": "turn/start",
        "fallback": "exec",
        "error": _err,
        "app_server_warm": app_server_warm,
        "resume_ms": resume_ms,
        "turn_start_ms": turn_start_ms,
        "latency_ms": _codex_elapsed_ms(total_start),
    }


def _codex_steer_via_app_server(session_id, text, cwd=None, model=None, image_paths=None):
    if os.environ.get("CCC_CODEX_APP_SERVER", "1").lower() in ("0", "false", "no"):
        return {
            "ok": False,
            "via": "codex-steer",
            "code": "codex_steer_unavailable",
            "error": "Codex app-server disabled",
        }
    # Writer attribution is useful for status display and queue coordination,
    # but it is not authoritative for steering. An active thread can be marked
    # external/unknown merely because CCC has not observed its owning client.
    # Ask Codex to resume the thread and use its actual turn id below; the
    # native RPC is the authority on whether the active turn can be steered.
    resume_params = {
        "threadId": session_id,
        "excludeTurns": False,
    }
    if cwd:
        resume_params["cwd"] = cwd
    if model:
        resume_params["model"] = model
    resumed = _core._codex_app_server_request("thread/resume", resume_params, timeout=20)
    if resumed.get("error"):
        return {
            "ok": False,
            "via": "codex-steer",
            "code": "codex_steer_unavailable",
            "error": _codex_error_text(resumed),
        }
    thread = ((resumed.get("result") or {}).get("thread") or {})
    active_turn = _codex_latest_active_turn(thread)
    status = (thread.get("status") or {}).get("type")
    if status != "active" or not active_turn:
        return {
            "ok": False,
            "via": "codex-steer",
            "code": "codex_no_active_turn",
            "error": "No running Codex turn to steer",
        }
    steered = _core._codex_app_server_request(
        "turn/steer",
        {
            "threadId": session_id,
            "expectedTurnId": active_turn["id"],
            "input": _codex_user_input(text, image_paths=image_paths),
        },
        timeout=20,
    )
    if _core._codex_response_succeeded(steered):
        return {
            "ok": True,
            "via": "codex-steer",
            "turn_id": (steered["result"] or {}).get("turnId") or active_turn["id"],
            "session_id": session_id,
        }
    return {
        "ok": False,
        "via": "codex-steer",
        "code": "codex_steer_failed",
        "error": _codex_error_text(steered) or "Codex steer failed",
    }


def _backup_codex_rollout_before_compact(session_id):
    """Copy the Codex thread's rollout JSONL to
    ~/.claude/command-center/compact-backups/ before `thread/compact/start`
    rewrites it. Returns the backup path or None on failure. Best-effort.

    Codex compaction is lossy — the app-server replaces the loaded thread's
    history with a compacted summary and rewrites the rollout on disk. Without
    a snapshot the pre-compact transcript is gone permanently. Mirrors the
    Claude `_backup_jsonl_before_compact` pattern and reuses its backup dir.
    """
    try:
        src = _core._resolve_codex_rollout_path(session_id)
        if not src:
            return None
        src = Path(src)
        if not src.is_file():
            return None
        backup_dir = Path.home() / ".claude" / "command-center" / "compact-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        dest = backup_dir / f"codex-{session_id}-{stamp}.jsonl"
        shutil.copy2(str(src), str(dest))
        # Keep at most 10 backups per thread — older ones rotate out.
        backups = sorted(backup_dir.glob(f"codex-{session_id}-*.jsonl"))
        for stale in backups[:-10]:
            try:
                stale.unlink()
            except OSError:
                pass
        return str(dest)
    except Exception as e:
        print(f"  [compact-backup] codex backup failed for {session_id}: {e}", file=sys.stderr)
        return None


# A compaction of a big Codex context is a full model turn: the app-server
# ACKs `thread/compact/start` in well under a second and then works for one to
# three minutes. 180s matches the Claude live-spawn deadline.
_CODEX_COMPACT_WAIT_TIMEOUT_S = 180.0
_CODEX_COMPACT_POLL_S = 0.4
# Once the compaction marker lands, wait a moment longer for the token_count
# that reports the rebuilt context size so the UI can print the real number
# instead of "reading the new size...".
_CODEX_COMPACT_POST_GRACE_S = 6.0
_CODEX_COMPACT_MARKER = b'"context_compacted"'


def _codex_compaction_wait_timeout():
    try:
        value = float(os.environ.get("CCC_CODEX_COMPACT_WAIT_S") or 0)
    except (TypeError, ValueError):
        value = 0.0
    return value if value > 0 else _CODEX_COMPACT_WAIT_TIMEOUT_S


def _codex_compaction_post_tokens(payload):
    """Rebuilt-context size off one post-compaction `token_count` payload."""
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict) or not usage:
        usage = info.get("total_token_usage")
    if not isinstance(usage, dict):
        return 0
    # Codex zeroes the per-turn fields on the compaction turn and reports the
    # new context size in total_tokens (see `_extract_codex_usage`).
    return _core._codex_int(usage.get("input_tokens")) or _core._codex_int(usage.get("total_tokens"))


def _codex_scan_compaction_tail(path, offset, seen_marker=False):
    """Scan rollout bytes appended since `offset` for a finished compaction.

    Rollout JSONL is append-only, so reading only the NEW bytes is exact and
    cheap even on a multi-MB thread - no whole-file parse, no subprocess. A
    compaction writes a top-level `compacted` record, then a `token_count`
    carrying the rebuilt size, then an `event_msg`/`context_compacted`.

    Returns ``(seen_marker, post_tokens, next_offset)``. `post_tokens` is 0
    until the token_count at or after the marker has been read.
    """
    post_tokens = 0
    try:
        size = os.path.getsize(path)
    except OSError:
        return seen_marker, 0, offset
    if size < offset:
        # Rewritten or rotated under us; restart from the top of the new file.
        offset = 0
    if size <= offset:
        return seen_marker, 0, offset
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read(size - offset)
    except OSError:
        return seen_marker, 0, offset
    consumed = 0
    for raw in chunk.splitlines(keepends=True):
        if not raw.endswith(b"\n"):
            # Partial trailing line - the writer is mid-append. Leave the
            # offset before it so the next poll re-reads it whole.
            break
        consumed += len(raw)
        if b"compacted" not in raw and b"token_count" not in raw:
            continue
        try:
            ev = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict):
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        ptype = str(payload.get("type") or "")
        if ev.get("type") == "compacted" or ptype in ("compacted", "context_compacted"):
            seen_marker = True
            continue
        if seen_marker and not post_tokens and ptype == "token_count":
            post_tokens = _codex_compaction_post_tokens(payload)
    return seen_marker, post_tokens, offset + consumed


def _codex_compaction_finished_in_state(session_id, since):
    """True once the app-server reported the compaction ITEM completed.

    `_codex_compaction_recovery_note_notification_unlocked` arms
    `compaction_recovery` on `item/started` for a `contextCompaction` item and
    drops `compaction_in_flight` on `item/completed`. A latch armed at or after
    `since` with the flag down is the app-server's own "the compaction turn is
    over" signal; the rollout marker is the independent one.
    """
    state = _core._codex_app_server_thread_state(session_id) or {}
    recovery = state.get("compaction_recovery")
    if not isinstance(recovery, dict):
        return False
    try:
        armed_at = float(recovery.get("compacted_at") or 0.0)
    except (TypeError, ValueError):
        return False
    if armed_at < float(since):
        return False
    return not recovery.get("compaction_in_flight")


def _codex_compact_via_app_server(session_id, cwd=None, model=None):
    """Compact a Codex thread via the app-server `thread/compact/start` RPC.

    Resumes the thread (loading it into the CCC-owned app-server) and then calls
    `thread/compact/start` with the schema-correct params (`{threadId}`).
    Compaction is lossy, so the caller is expected to have already backed up the
    rollout. Returns a result dict mirroring the other Codex app-server helpers
    (`{ok, via, ...}` / `{ok: False, error, ...}`). Handles "app-server
    unavailable" gracefully.

    `thread/compact/start` is ACCEPTED in under a second and the compaction
    turn then runs for one to three minutes. Returning on the ACK made the UI
    card flip to "CONTEXT COMPACTED / took 0:00" while Codex was still working,
    and the composer immediately queued the next message behind the very turn
    the card said had finished. So this now WAITS for the compaction to land,
    mirroring `_compact_via_live_spawn_stdin`: `{status: "compacted",
    compact_result: "success"}` on success, `code: "compact_timeout"` on the
    deadline (which the frontend keeps in its working state rather than
    reporting a failure).
    """
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "via": "codex-compact", "error": "missing session_id"}
    if os.environ.get("CCC_CODEX_APP_SERVER", "1").lower() in ("0", "false", "no"):
        return {
            "ok": False,
            "via": "codex-compact",
            "code": "codex_compact_unavailable",
            "error": "Codex app-server disabled",
        }
    # The thread must be loaded into this app-server before it can be compacted.
    resume_params = {"threadId": sid, "excludeTurns": False}
    if cwd:
        resume_params["cwd"] = cwd
    if model:
        resume_params["model"] = model
    resumed = _core._codex_app_server_request("thread/resume", resume_params, timeout=20)
    if resumed.get("error"):
        return {
            "ok": False,
            "via": "codex-compact",
            "code": "codex_compact_unavailable",
            "error": _codex_error_text(resumed),
        }
    if resumed.get("ok") is False and "result" not in resumed:
        # `_codex_app_server_request` short-circuit (unavailable / timeout).
        return {
            "ok": False,
            "via": "codex-compact",
            "code": "codex_compact_unavailable",
            "error": resumed.get("error") or "Codex app-server unavailable",
        }
    # Watermark the rollout BEFORE the RPC so a compaction from an earlier turn
    # can never be mistaken for this one.
    try:
        rollout_path = _core._resolve_codex_rollout_path(sid)
    except Exception:
        rollout_path = None
    scan_offset = 0
    marker_baseline = 0
    pre_tokens = 0
    if rollout_path:
        try:
            scan_offset = os.path.getsize(rollout_path)
        except OSError:
            scan_offset = 0
        marker_baseline = _core._tail_count(rollout_path, _CODEX_COMPACT_MARKER)
        try:
            meta = _core._extract_codex_tail_meta(Path(rollout_path)) or {}
            pre_tokens = _core._codex_int(meta.get("latest_input_tokens"))
        except Exception:
            pre_tokens = 0
    armed_since = time.time() - 2.0  # clock slop against the app-server latch

    compacted = _core._codex_app_server_request(
        "thread/compact/start", {"threadId": sid}, timeout=60
    )
    if _core._codex_response_succeeded(compacted):
        started = time.time()
        deadline = started + _codex_compaction_wait_timeout()
        seen_marker = False
        state_done = False
        post_tokens = 0
        post_grace_until = 0.0
        while True:
            now = time.time()
            if now >= deadline:
                break
            if rollout_path:
                seen_marker, found_post, scan_offset = _codex_scan_compaction_tail(
                    rollout_path, scan_offset, seen_marker
                )
                if found_post:
                    post_tokens = found_post
                if not seen_marker and _core._tail_count(
                    rollout_path, _CODEX_COMPACT_MARKER
                ) > marker_baseline:
                    seen_marker = True
            if not state_done and _codex_compaction_finished_in_state(sid, armed_since):
                state_done = True
            if seen_marker or state_done:
                if post_tokens or not rollout_path:
                    break
                # The marker is down but the size rollup may be one line
                # behind it. Give it a moment rather than reporting 0.
                if not post_grace_until:
                    post_grace_until = now + _CODEX_COMPACT_POST_GRACE_S
                elif now >= post_grace_until:
                    break
            time.sleep(_CODEX_COMPACT_POLL_S)
        duration_ms = int(max(0.0, time.time() - started) * 1000)
        if seen_marker or state_done:
            return {
                "ok": True,
                "via": "codex-compact",
                "status": "compacted",
                "compact_result": "success",
                "session_id": sid,
                "pre_tokens": pre_tokens,
                "post_tokens": post_tokens,
                "duration_ms": duration_ms,
            }
        return {
            "ok": False,
            "via": "codex-compact",
            "code": "compact_timeout",
            "status": "compacting",
            "session_id": sid,
            "pre_tokens": pre_tokens,
            "duration_ms": duration_ms,
            "error": "Codex is still compacting; CCC will keep watching.",
        }
    if compacted.get("ok") is False and "result" not in compacted:
        return {
            "ok": False,
            "via": "codex-compact",
            "code": "codex_compact_unavailable",
            "error": compacted.get("error") or "Codex app-server unavailable",
        }
    return {
        "ok": False,
        "via": "codex-compact",
        "code": "codex_compact_failed",
        "error": _codex_error_text(compacted) or "Codex compact failed",
    }


def _codex_goal_via_app_server(session_id, action, objective=None, cwd=None):
    """Set, clear, pause, or resume a Codex thread goal.

    `action` is "clear" (-> `thread/goal/clear`, params `{threadId}`) or "set"
    (-> `thread/goal/set`, params `{threadId, objective}`). This is the non-live
    fallback for `/goal clear` / `/goal <objective>`: the codex `goals` feature
    is native, but its slash command only runs in a live TUI. The RPCs let CCC
    drive the same native goal store for a dormant thread — mirrors
    `_codex_compact_via_app_server` (`thread/compact/start`).

    The app-server RPC currently reports success without always updating the
    local goals sqlite used by Codex/CCC, so CCC verifies the same native store
    directly under a file lock. Pause/resume have no app-server RPC; they are
    direct status updates.
    """
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "via": "codex-goal", "error": "missing session_id"}
    action = (action or "").strip().lower()
    if action not in ("clear", "set", "pause", "resume"):
        return {
            "ok": False,
            "via": "codex-goal",
            "code": "codex_goal_unsupported_action",
            "error": f"unsupported goal action: {action!r}",
        }
    if action == "set" and not (objective or "").strip():
        return {
            "ok": False,
            "via": "codex-goal",
            "code": "codex_goal_empty_objective",
            "error": "Goal objective must not be empty.",
        }
    app_server_ok = False
    if action in ("clear", "set") and os.environ.get("CCC_CODEX_APP_SERVER", "1").lower() not in ("0", "false", "no"):
        # Best-effort: let Codex handle any side effects it knows about, then
        # enforce the durable store CCC actually reads below.
        resume_params = {"threadId": sid, "excludeTurns": False}
        if cwd:
            resume_params["cwd"] = cwd
        resumed = _core._codex_app_server_request("thread/resume", resume_params, timeout=20)
        if not (resumed.get("error") or (resumed.get("ok") is False and "result" not in resumed)):
            if action == "clear":
                rpc_method = "thread/goal/clear"
                rpc_params = {"threadId": sid}
            else:
                rpc_method = "thread/goal/set"
                rpc_params = {"threadId": sid, "objective": objective.strip()}
            result = _core._codex_app_server_request(rpc_method, rpc_params, timeout=30)
            app_server_ok = _core._codex_response_succeeded(result)

    store = _core._codex_goal_store_update(sid, action, objective=objective)
    if store.get("ok"):
        store.setdefault("engine", "codex")
        store["app_server_ok"] = app_server_ok
        return store
    return {
        "ok": False,
        "via": "codex-goal",
        "code": store.get("code") or "codex_goal_failed",
        "engine": "codex",
        "error": store.get("error") or f"Codex goal {action} failed",
        "app_server_ok": app_server_ok,
    }


def _codex_grab_back_settings_params(session_id, cwd=None, model=None, reasoning_effort=None):
    params = {
        "threadId": session_id,
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "sandboxPolicy": {"type": "dangerFullAccess"},
    }
    if cwd:
        params["cwd"] = cwd
    if model:
        params["model"] = model
    if reasoning_effort:
        params["effort"] = reasoning_effort
    return params


def _codex_grab_back_via_app_server(
    session_id, cwd=None, model=None, reasoning_effort=None, deny_pending_approval=False
):
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "via": "codex-grab-back", "error": "missing session_id"}
    if os.environ.get("CCC_CODEX_APP_SERVER", "1").lower() in ("0", "false", "no"):
        return {
            "ok": False,
            "via": "codex-grab-back",
            "code": "codex_grab_back_unavailable",
            "error": "Codex app-server disabled",
        }
    if not cwd:
        cwd = _core.find_session_cwd(sid)
    # If the thread is dead-ended on an in-flight approval that this reclaim is
    # meant to escape, decline it first — otherwise the stuck "Needs approval"
    # prompt can survive the interrupt + settings-reapply below. Only attempt
    # when a pending approval actually exists; best-effort (an externally-owned
    # approval with no app-server request id simply can't be answered here).
    approval_denied = False
    approval_decline = None
    if deny_pending_approval:
        with _core._CODEX_APP_SERVER_LOCK:
            state = _core._CODEX_APP_SERVER_THREAD_STATE.get(sid) if sid else None
            has_pending = _codex_app_server_pending_approval_item(state or {}) is not None
        if has_pending:
            approval_decline = _core._codex_app_server_resolve_approval(sid, "decline")
            approval_denied = bool(approval_decline.get("ok"))
    settings_params = _codex_grab_back_settings_params(
        sid,
        cwd=cwd,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    _core._codex_app_server_refresh_thread_status(sid, max_age=0)
    settings_response = _core._codex_app_server_request(
        "thread/settings/update",
        settings_params,
        timeout=10,
    )
    settings_ok = _core._codex_response_succeeded(settings_response)
    interrupt = _core._codex_interrupt_via_app_server(sid, cwd=cwd)
    no_active_turn = (interrupt.get("code") == "codex_no_active_turn")
    _core._codex_app_server_refresh_thread_status(sid, max_age=0)
    public = _core._codex_app_server_thread_public_status(sid)
    ok = bool(settings_ok or interrupt.get("ok") or no_active_turn or approval_denied)
    result = {
        "ok": ok,
        "via": "codex-grab-back",
        "session_id": sid,
        "settings_applied": bool(settings_ok),
        "interrupted": bool(interrupt.get("ok")),
        "no_active_turn": bool(no_active_turn),
        "approval_denied": bool(approval_denied),
        "codex_app_server": _core._codex_app_server_is_live(),
        "codex_app_server_transport": _core._codex_app_server_transport_kind(),
        "codex_managed_app_server": _core._codex_app_server_transport_kind() == "managed",
        "codex_app_server_active_item": public.get("active_item"),
        "codex_app_server_token_usage": public.get("token_usage"),
    }
    if cwd:
        result["cwd"] = cwd
    if approval_decline is not None and not approval_denied:
        result["approval_decline_error"] = approval_decline.get("error") or "approval decline failed"
    if not settings_ok:
        result["settings_error"] = _codex_app_server_response_error(
            settings_response,
            "Codex app-server did not apply thread settings",
        )
    if interrupt:
        result["interrupt"] = interrupt
        if interrupt.get("error") and not interrupt.get("ok") and not no_active_turn:
            result["interrupt_error"] = interrupt.get("error")
    if not ok:
        result["error"] = (
            result.get("interrupt_error")
            or result.get("settings_error")
            or "Codex grab-back failed"
        )
        result.setdefault("code", "codex_grab_back_failed")
    return result


def _codex_interrupt_via_app_server(session_id, cwd=None):
    if os.environ.get("CCC_CODEX_APP_SERVER", "1").lower() in ("0", "false", "no"):
        return {
            "ok": False,
            "via": "codex-app-interrupt",
            "code": "codex_interrupt_unavailable",
            "error": "Codex app-server disabled",
        }
    if not _core._codex_app_server_is_live() and not (
        _core._codex_managed_app_server_enabled() and _core._codex_managed_app_server_socket_path().exists()
    ):
        return {
            "ok": False,
            "via": "codex-app-interrupt",
            "code": "codex_interrupt_unavailable",
            "error": "Codex app-server unavailable",
        }
    resume_params = {"threadId": session_id, "excludeTurns": False}
    if cwd:
        resume_params["cwd"] = cwd
    resumed = _core._codex_app_server_request("thread/resume", resume_params, timeout=10)
    thread = ((resumed.get("result") or {}).get("thread") or {})
    active_turn = _codex_latest_active_turn(thread)
    turn_id = (active_turn or {}).get("id") or _core._codex_app_server_thread_state(session_id).get("active_turn_id")
    status = ((thread.get("status") or {}).get("type") or "").lower()
    authoritative_idle = bool(
        _core._codex_response_succeeded(resumed)
        and status
        and status != "active"
        and not active_turn
    )
    if authoritative_idle:
        _codex_reconcile_thread_idle(session_id)
        turn_id = None
    if not turn_id or authoritative_idle:
        return {
            "ok": False,
            "via": "codex-app-interrupt",
            "code": "codex_no_active_turn",
            "error": "No running Codex turn to interrupt",
        }
    interrupted = _core._codex_app_server_request(
        "turn/interrupt",
        {"threadId": session_id, "turnId": turn_id},
        timeout=10,
    )
    if _core._codex_response_succeeded(interrupted):
        return {
            "ok": True,
            "via": "codex-app-interrupt",
            "turn_id": turn_id,
            "session_id": session_id,
        }
    return {
        "ok": False,
        "via": "codex-app-interrupt",
        "code": "codex_interrupt_failed",
        "error": _codex_error_text(interrupted) or interrupted.get("error") or "Codex interrupt failed",
    }


def _codex_state_db_candidates():
    """Existing Codex state DB paths, newest known schema first."""
    base = Path.home() / ".codex"
    candidates = [_core.CODEX_STATE_DB]
    try:
        if base.is_dir():
            for p in sorted(base.glob("state*.sqlite"), key=lambda x: x.name, reverse=True):
                if p not in candidates:
                    candidates.append(p)
    except OSError:
        pass
    return [p for p in candidates if p.is_file()]


def _jsonl_line_ending(line):
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _mark_codex_rollout_user_visible(thread_id, rollout_path):
    sid = str(thread_id or "").strip()
    if not sid or not rollout_path:
        return False
    try:
        path = Path(rollout_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if not path.is_file():
        return False
    try:
        original_stat = path.stat()
    except OSError:
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return False

    changed = False
    for idx, line in enumerate(lines):
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "session_meta":
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            return False
        meta_id = str(payload.get("id") or "").strip()
        if meta_id and meta_id != sid:
            return False
        source = payload.get("source")
        thread_source = payload.get("thread_source")
        if source and source not in ("exec", "cli", "vscode"):
            return False
        if thread_source and thread_source != "user":
            return False
        if source != "vscode":
            payload["source"] = "vscode"
            changed = True
        if thread_source != "user":
            payload["thread_source"] = "user"
            changed = True
        if not changed:
            return True
        ending = _jsonl_line_ending(line)
        lines[idx] = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ending
        break
    else:
        return False

    try:
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, path)
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    except OSError:
        return False
    return True


def _codex_row_updated_epoch(row):
    if not isinstance(row, dict):
        return None
    for key, scale in (("updated_at_ms", 1000.0), ("updated_at", 1.0)):
        try:
            raw = row.get(key)
            if raw is None:
                continue
            value = float(raw) / scale
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return None


def _restore_codex_rollout_mtime_from_row(row):
    path = _core._codex_rollout_path_from_row(row)
    ts = _codex_row_updated_epoch(row)
    if not path or ts is None:
        return False
    try:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return False
        os.utime(p, (ts, ts))
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _mark_codex_thread_user_visible(thread_id, update_rollout=True):
    """Mark a CCC-created Codex exec thread as visible in Codex.app.

    Codex Desktop starts sidebar-visible threads with `thread_source='user'`.
    Its sidebar also treats IDE-originated rows as `source='vscode'`; plain
    `codex exec` rows can otherwise stay hidden. Codex can rebuild SQLite from
    the rollout JSONL, so we patch the rollout metadata when it is safe too.
    """
    sid = str(thread_id or "").strip()
    if not sid:
        return False
    for db in _codex_state_db_candidates():
        con = None
        try:
            con = sqlite3.connect(f"file:{db}?mode=rw", uri=True, timeout=0.25)
            cols = {
                row[1]
                for row in con.execute("PRAGMA table_info(threads)").fetchall()
            }
            if "id" not in cols or "thread_source" not in cols:
                continue
            selected = ["thread_source"]
            if "source" in cols:
                selected.append("source")
            if "rollout_path" in cols:
                selected.append("rollout_path")
            row = con.execute(
                f"SELECT {', '.join(selected)} FROM threads WHERE id = ?",
                (sid,),
            ).fetchone()
            if not row:
                continue
            values_by_col = dict(zip(selected, row))
            current = values_by_col.get("thread_source")
            source = values_by_col.get("source", "vscode")
            if current and current != "user":
                return False
            if source and source not in ("exec", "cli", "vscode"):
                return False
            if update_rollout:
                _mark_codex_rollout_user_visible(
                    sid,
                    values_by_col.get("rollout_path"),
                )
            assignments = []
            values = []
            if current != "user":
                assignments.append("thread_source = ?")
                values.append("user")
            if "source" in cols and source != "vscode":
                assignments.append("source = ?")
                values.append("vscode")
            if assignments:
                values.append(sid)
                con.execute(
                    f"UPDATE threads SET {', '.join(assignments)} WHERE id = ?",
                    tuple(values),
                )
                con.commit()
            return True
        except sqlite3.Error:
            continue
        finally:
            if con is not None:
                try:
                    con.close()
                except sqlite3.Error:
                    pass
    return False


def _sync_codex_thread_title(thread_id, title):
    """Mirror a CCC user rename into Codex's user-facing thread title.

    Keep `first_user_message` intact as provenance; Codex mobile/sidebar list
    uses the shorter `title`/`preview` fields when present.
    """
    sid = str(thread_id or "").strip()
    name = _core._truncate_session_name(title)
    if not sid or not name:
        return False
    for db in _codex_state_db_candidates():
        con = None
        try:
            con = sqlite3.connect(f"file:{db}?mode=rw", uri=True, timeout=0.25)
            cols = {
                row[1]
                for row in con.execute("PRAGMA table_info(threads)").fetchall()
            }
            if "id" not in cols or "title" not in cols:
                continue
            assignments = ["title = ?"]
            values = [name]
            if "preview" in cols:
                assignments.append("preview = ?")
                values.append(name)
            values.append(sid)
            cur = con.execute(
                f"UPDATE threads SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
            con.commit()
            if cur.rowcount:
                return True
        except sqlite3.Error:
            continue
        finally:
            if con is not None:
                try:
                    con.close()
                except sqlite3.Error:
                    pass
    return False


_codex_visibility_retry_sids = set()
_codex_visibility_retry_lock = threading.Lock()


def _schedule_codex_visibility_retry(session_id, spawn_entry=None, attempts=60, delay=1.0):
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _codex_visibility_retry_lock:
        if sid in _codex_visibility_retry_sids:
            return False
        _codex_visibility_retry_sids.add(sid)

    entry = dict(spawn_entry or {})

    def worker():
        try:
            for _idx in range(max(1, int(attempts or 1))):
                if _core._mark_codex_thread_user_visible(sid, update_rollout=True):
                    _core._register_codex_sidebar_project_for_spawn_entry(entry, sid)
                    return
                time.sleep(max(0.05, float(delay or 0.5)))
        except Exception:
            pass
        finally:
            with _codex_visibility_retry_lock:
                _codex_visibility_retry_sids.discard(sid)

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"ccc-codex-visible-{sid[:8]}",
    ).start()
    return True


def _codex_fetch_threads(where="", params=(), limit=None):
    """Read rows from Codex's local thread index without creating files.

    Codex stores durable conversation metadata in ~/.codex/state_*.sqlite,
    with each row pointing at a rollout JSONL. We open SQLite in read-only URI
    mode so a dashboard scan cannot create a missing DB or mutate state.
    """
    for db in _codex_state_db_candidates():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.25)
            con.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            cols = {
                row["name"]
                for row in con.execute("PRAGMA table_info(threads)").fetchall()
            }
            if not cols:
                continue
            wanted = [
                "id", "rollout_path", "created_at", "updated_at",
                "created_at_ms", "updated_at_ms", "source", "model_provider",
                "cwd", "title", "tokens_used", "has_user_event", "archived",
                "archived_at", "git_sha", "git_branch", "git_origin_url",
                "cli_version", "first_user_message", "agent_nickname",
                "agent_path", "agent_role", "memory_mode", "model", "reasoning_effort",
                "thread_source",
            ]
            selected = [c for c in wanted if c in cols]
            if "id" not in selected:
                return []
            order_terms = []
            if "updated_at_ms" in cols:
                order_terms.append("updated_at_ms")
            if "updated_at" in cols:
                order_terms.append("updated_at * 1000")
            if "created_at_ms" in cols:
                order_terms.append("created_at_ms")
            if "created_at" in cols:
                order_terms.append("created_at * 1000")
            order = f"COALESCE({', '.join(order_terms)}) DESC" if order_terms else "id DESC"
            sql = f"SELECT {', '.join(selected)} FROM threads"
            if where:
                sql += f" WHERE {where}"
            sql += f" ORDER BY {order}"
            if limit:
                sql += " LIMIT ?"
                params = tuple(params) + (int(limit),)
            rows = [dict(r) for r in con.execute(sql, tuple(params)).fetchall()]
            return rows
        except sqlite3.Error:
            continue
        finally:
            try:
                con.close()
            except sqlite3.Error:
                pass
    return []


def _codex_spawn_edge_name(child_thread_id):
    """Human label for a codex-native spawn edge, e.g. "PR113 Independent Review".

    Bounded to spawned children only (never a full-thread scan) — one indexed
    `id = ?` lookup per child in `_codex_spawn_parent_by_child()`'s output.
    """
    try:
        return _core._codex_agent_task_label(_core._codex_thread_row(child_thread_id))
    except Exception:
        return ""


def _codex_spawn_parent_by_child():
    """Map each spawned Codex thread to the thread that spawned it.

    Codex records sub-agent spawns in ~/.codex/state_*.sqlite's
    `thread_spawn_edges` (parent_thread_id, child_thread_id). CCC uses this to
    set parent_session_id on a spawned agent's row so it nests under its parent
    in the Current-sessions tree — a "one job, many agents" fan-out (a fixer
    plus reviewers) then reads as one cluster instead of a pile of loose rows.
    Read-only; tolerant of the table being absent on older Codex builds.
    Returns {child_thread_id: parent_thread_id}. (CCC-298)
    """
    out = {}
    for db in _codex_state_db_candidates():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.25)
            con.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            has_table = con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='thread_spawn_edges'"
            ).fetchone()
            if not has_table:
                continue
            for r in con.execute(
                "SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"
            ).fetchall():
                child = (r["child_thread_id"] or "").strip()
                parent = (r["parent_thread_id"] or "").strip()
                # First edge wins (DBs scanned newest-first); never self-parent.
                if child and parent and child != parent:
                    out.setdefault(child, parent)
        except sqlite3.Error:
            continue
        finally:
            try:
                con.close()
            except sqlite3.Error:
                pass
    return out


_codex_goals_cache = {"key": None, "data": {}, "ts": 0.0}
_codex_goals_lock = threading.Lock()
_CODEX_GOALS_TTL = 2.0


def _codex_goals_db_path():
    """Newest existing goals sqlite among the known candidates, or None."""
    best = None
    best_mtime = -1.0
    for cand in _core.CODEX_GOALS_DB_CANDIDATES:
        try:
            st = cand.stat()
        except OSError:
            continue
        if st.st_mtime > best_mtime:
            best_mtime = st.st_mtime
            best = cand
    return best


def _invalidate_codex_goals_cache():
    with _codex_goals_lock:
        _codex_goals_cache["key"] = None
        _codex_goals_cache["data"] = {}
        _codex_goals_cache["ts"] = 0.0


def _codex_goal_store_update(session_id, action, objective=None):
    """Mutate Codex's native goals sqlite under a file lock.

    The Codex app-server goal RPC is not a reliable postcondition on every
    transport/version: it can return a JSON-RPC result while the `thread_goals`
    row CCC and Codex read remains unchanged. This helper writes the same store
    directly for dormant-thread `/goal` actions.
    """
    sid = str(session_id or "").strip()
    action = str(action or "").strip().lower()
    if not sid:
        return {"ok": False, "via": "codex-goal-store", "error": "missing session_id"}
    if action not in ("clear", "set", "pause", "resume"):
        return {
            "ok": False,
            "via": "codex-goal-store",
            "code": "codex_goal_unsupported_action",
            "error": f"unsupported goal action: {action!r}",
        }
    obj = str(objective or "").strip()
    if action == "set" and not obj:
        return {
            "ok": False,
            "via": "codex-goal-store",
            "code": "codex_goal_empty_objective",
            "error": "Goal objective must not be empty.",
        }
    db = _codex_goals_db_path() or _core.CODEX_GOALS_DB_CANDIDATES[0]
    lock_path = db.with_suffix(db.suffix + ".lock")
    con = None
    lock_fh = None
    try:
        db.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = lock_path.open("a+")
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        con = sqlite3.connect(f"file:{db}?mode=rwc", uri=True, timeout=5.0)
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS thread_goals (
              thread_id TEXT PRIMARY KEY NOT NULL,
              goal_id TEXT NOT NULL,
              objective TEXT NOT NULL,
              status TEXT NOT NULL,
              token_budget INTEGER,
              tokens_used INTEGER NOT NULL DEFAULT 0,
              time_used_seconds INTEGER NOT NULL DEFAULT 0,
              created_at_ms INTEGER NOT NULL,
              updated_at_ms INTEGER NOT NULL
            )
            """
        )
        cols = {r[1] for r in con.execute("PRAGMA table_info(thread_goals)").fetchall()}
        required = {"thread_id", "goal_id", "objective", "status", "created_at_ms", "updated_at_ms"}
        if not required.issubset(cols):
            return {
                "ok": False,
                "via": "codex-goal-store",
                "code": "codex_goal_schema_unsupported",
                "error": "Codex goals DB schema is not supported.",
            }
        now_ms = int(time.time() * 1000)
        if action == "clear":
            con.execute("DELETE FROM thread_goals WHERE thread_id = ?", (sid,))
            con.commit()
            _core._invalidate_codex_goals_cache()
            return {"ok": True, "via": "codex-goal-store", "action": "clear", "session_id": sid}

        if action == "set":
            existing = con.execute(
                "SELECT goal_id, created_at_ms FROM thread_goals WHERE thread_id = ?",
                (sid,),
            ).fetchone()
            goal_id = existing[0] if existing and existing[0] else str(uuid.uuid4())
            created_at_ms = int(existing[1]) if existing and existing[1] else now_ms
            con.execute(
                """
                INSERT INTO thread_goals (
                  thread_id, goal_id, objective, status, token_budget,
                  tokens_used, time_used_seconds, created_at_ms, updated_at_ms
                )
                VALUES (?, ?, ?, 'active', NULL, 0, 0, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                  goal_id = excluded.goal_id,
                  objective = excluded.objective,
                  status = 'active',
                  token_budget = excluded.token_budget,
                  tokens_used = 0,
                  time_used_seconds = 0,
                  updated_at_ms = excluded.updated_at_ms
                """,
                (sid, goal_id, obj, created_at_ms, now_ms),
            )
            con.commit()
            _core._invalidate_codex_goals_cache()
            return {"ok": True, "via": "codex-goal-store", "action": "set", "session_id": sid}

        status = "active" if action == "resume" else "paused"
        cur = con.execute(
            "UPDATE thread_goals SET status = ?, updated_at_ms = ? WHERE thread_id = ?",
            (status, now_ms, sid),
        )
        con.commit()
        if cur.rowcount <= 0:
            return {
                "ok": False,
                "via": "codex-goal-store",
                "code": "codex_goal_missing",
                "error": "No Codex goal exists for this thread.",
            }
        _core._invalidate_codex_goals_cache()
        return {"ok": True, "via": "codex-goal-store", "action": action, "session_id": sid}
    except (OSError, sqlite3.Error, ValueError) as e:
        return {
            "ok": False,
            "via": "codex-goal-store",
            "code": "codex_goal_store_failed",
            "error": str(e) or "Codex goal store update failed.",
        }
    finally:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
        if lock_fh is not None:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                lock_fh.close()
            except OSError:
                pass


def _codex_goals_snapshot():
    """Map thread_id -> {objective, status, token_budget, tokens_used} for every
    codex thread that currently has a goal.

    ONE batched read of ~/.codex/goals_1.sqlite, cached by (path, mtime, size)
    with a short TTL so a full conversation build does at most one stat+read
    regardless of row count (perf gate: never per-row). Returns {} when no
    goals DB exists or it's empty — codex clears the row on `/goal clear`.
    """
    now = time.monotonic()
    with _codex_goals_lock:
        cached_ts = _codex_goals_cache["ts"]
        cached_key = _codex_goals_cache["key"]
        cached_data = _codex_goals_cache["data"]
    db = _codex_goals_db_path()
    if db is None:
        with _codex_goals_lock:
            _codex_goals_cache["key"] = None
            _codex_goals_cache["data"] = {}
            _codex_goals_cache["ts"] = now
        return {}
    try:
        st = db.stat()
        key = (str(db), st.st_mtime, st.st_size)
    except OSError:
        return cached_data if cached_key else {}
    if cached_key == key and (now - cached_ts) < _CODEX_GOALS_TTL:
        return cached_data
    data = {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.25)
        con.row_factory = sqlite3.Row
    except sqlite3.Error:
        return cached_data if cached_key else {}
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(thread_goals)").fetchall()}
        if "thread_id" in cols and "objective" in cols:
            sel = [c for c in ("thread_id", "objective", "status",
                               "token_budget", "tokens_used") if c in cols]
            for r in con.execute(f"SELECT {', '.join(sel)} FROM thread_goals").fetchall():
                tid = r["thread_id"]
                obj = (r["objective"] or "").strip() if "objective" in sel else ""
                if not tid or not obj:
                    continue
                data[tid] = {
                    "objective": obj,
                    "status": (r["status"] or "").strip() if "status" in sel else "",
                    "token_budget": r["token_budget"] if "token_budget" in sel else None,
                    "tokens_used": r["tokens_used"] if "tokens_used" in sel else None,
                }
    except sqlite3.Error:
        data = cached_data if cached_key else {}
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass
    with _codex_goals_lock:
        _codex_goals_cache["key"] = key
        _codex_goals_cache["data"] = data
        _codex_goals_cache["ts"] = now
    return data


_codex_titles_cache = {"key": None, "data": {}, "ts": 0.0}
_codex_titles_lock = threading.Lock()
_CODEX_TITLES_TTL = 2.0


def _codex_titles_snapshot():
    """Map thread_id -> the fields a Codex row's display name is derived from.

    Codex names a thread asynchronously (`thread/name/set` lands seconds after
    the spawn returns) and stores the result in ~/.codex/state_*.sqlite. That
    DB is deliberately NOT part of the archive corpus signature: it is WAL and
    its mtime flips on every write from any live Codex session, so gating the
    corpus cache on it would bust an O(all-rows) rebuild constantly. The cost
    of leaving it out was that a renamed thread kept rendering as "(untitled)"
    until some unrelated change happened to invalidate the cache.

    So the title is treated as live state and re-layered at serve time instead
    (see _rehydrate_archive_cached_rows). ONE batched read for the whole list,
    cached by (mtime, size) of both the DB and its -wal sidecar -- the main
    file's mtime alone does not move in WAL mode -- so the common case is a
    couple of stat() calls and no query at all. Never per-row.
    """
    now = time.monotonic()
    with _codex_titles_lock:
        cached_ts = _codex_titles_cache["ts"]
        cached_key = _codex_titles_cache["key"]
        cached_data = _codex_titles_cache["data"]

    key_parts = []
    for db in _codex_state_db_candidates():
        for path in (db, db.with_name(db.name + "-wal")):
            try:
                st = path.stat()
                key_parts.append((str(path), st.st_mtime_ns, st.st_size))
            except OSError:
                continue
    key = tuple(key_parts)
    if not key:
        return {}
    if cached_key == key and (now - cached_ts) < _CODEX_TITLES_TTL:
        return cached_data

    data = {}
    for row in _core._codex_fetch_threads():
        sid = row.get("id")
        if not sid:
            continue
        data[sid] = {
            "title": (row.get("title") or "").strip(),
            "first_user_message": row.get("first_user_message") or "",
            "agent_nickname": row.get("agent_nickname"),
            "agent_role": row.get("agent_role"),
            "agent_path": row.get("agent_path"),
        }
    if not data and cached_key:
        # A locked/partial read must not blank every row's name.
        return cached_data
    with _codex_titles_lock:
        _codex_titles_cache["key"] = key
        _codex_titles_cache["data"] = data
        _codex_titles_cache["ts"] = now
    return data


def _session_landed(session_id):
    """Cheap "is this just-spawned session visible to CCC yet?" probe.

    Deliberately does NOT build or touch the archive list: the whole point is
    to give the post-spawn chase something it can poll every second or so
    without paying for a full corpus serialization.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "landed": False, "known": False}
    # Codex writes the thread row into state_*.sqlite the moment the thread is
    # created, well before the rollout has any content. Cached snapshot, so in
    # the steady state this is a dict hit behind two stat() calls.
    try:
        fresh = _core._codex_titles_snapshot().get(sid)
    except Exception:
        fresh = None
    if fresh:
        return {
            "ok": True,
            "landed": True,
            "known": True,
            "engine": "codex",
            "display_name": _core._codex_display_name(
                fresh,
                title=fresh["title"],
                first_message=fresh["first_user_message"],
            ),
        }
    # Claude and everything else that logs a transcript under ~/.claude/projects.
    try:
        for path in _core.PROJECTS_ROOT.glob(f"*/{sid}.jsonl"):
            if path.is_file():
                if _core._spawn_timeline_mark(sid, "claude_transcript_created"):
                    _core._spawn_timeline_save()
                return {"ok": True, "landed": True, "known": True, "engine": "claude"}
    except OSError:
        pass
    # Kimi/GLM ACP sessions write their transcript under ~/.claude/command-center/acp/.
    try:
        for harness in _core._ACP_HARNESSES:
            if _core._acp_harness_enabled(harness) and _core._acp_transcript_path(harness, sid).is_file():
                return {"ok": True, "landed": True, "known": True, "engine": harness}
    except OSError:
        pass
    # Devin CLI: the CLI drops a lock file at session start and inserts the
    # sessions row at about the same time. Lock check is a stat(); the id-set
    # fallback is a cached 40-row query. Without this branch the post-spawn
    # chase never saw a Devin session "land", so it never forced the archive
    # refresh and the placeholder sat on "Thinking..." until the 5-minute
    # stale serve window rolled over.
    if _core._is_devin_cli_session(sid):
        raw_id = _core._devin_cli_raw_id(sid)
        try:
            if _core._devin_cli_lock_pid(raw_id) or raw_id in _core._devin_cli_session_ids():
                return {"ok": True, "landed": True, "known": True, "engine": "devin"}
        except Exception:
            pass
        return {"ok": True, "landed": False, "known": True, "engine": "devin"}
    # known=False means "this probe cannot answer for this id" (an engine that
    # stores sessions somewhere neither branch reads). The client keeps its
    # periodic full refresh in that case rather than trusting a false negative.
    return {"ok": True, "landed": False, "known": True}


def _codex_thread_row(thread_id):
    if not thread_id:
        return None
    rows = _core._codex_fetch_threads("id = ?", (thread_id,), limit=1)
    return rows[0] if rows else None


def _codex_agent_path(row):
    raw = str((row or {}).get("agent_path") or "").strip()
    if raw:
        return raw
    source = (row or {}).get("source")
    if not isinstance(source, str) or not source.strip().startswith("{"):
        return ""
    try:
        data = json.loads(source)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    subagent = data.get("subagent")
    if not isinstance(subagent, dict):
        return ""
    spawn = subagent.get("thread_spawn")
    if not isinstance(spawn, dict):
        return ""
    return str(spawn.get("agent_path") or "").strip()


def _codex_agent_task_label(row):
    path = _codex_agent_path(row).rstrip("/")
    leaf = path.rsplit("/", 1)[-1].strip() if path else ""
    if not leaf or leaf.lower() in ("root", "agent", "subagent"):
        return ""
    label = re.sub(r"[_-]+", " ", leaf).strip()
    label = re.sub(
        r"^([A-Za-z][A-Za-z0-9]*)\s+(\d+)(?=\s|$)",
        lambda m: f"{m.group(1).upper()}-{m.group(2)}",
        label,
    )
    if label and not re.match(r"^[A-Z]+-\d+", label):
        label = label[:1].upper() + label[1:]
    return _core._truncate_session_name(label) or ""


def _codex_display_name(row, override=None, title="", first_message=""):
    # Conductor prepends a machine-only system wrapper to its first Codex
    # prompt. It is useful transcript provenance but not a session name; keep
    # the actual user ask that follows the closing tag. Codex titles come
    # straight off the thread row rather than through
    # _extract_user_prompt_text, so the shared stripper is applied here.
    def visible_prompt(value):
        return _core._strip_host_system_instruction(value)

    return (
        _core._truncate_session_name(override)
        or _core._truncate_session_name(visible_prompt(title))
        or _core._truncate_session_name(visible_prompt(first_message))
        or _core._codex_agent_task_label(row)
        or _core._truncate_session_name((row or {}).get("agent_nickname"))
    )


def _codex_ts_seconds(row, prefix="updated"):
    ms = row.get(f"{prefix}_at_ms")
    if ms:
        try:
            return float(ms) / 1000.0
        except (TypeError, ValueError):
            pass
    val = row.get(f"{prefix}_at")
    if val:
        try:
            val = float(val)
            return val / 1000.0 if val > 100000000000 else val
        except (TypeError, ValueError):
            pass
    return 0.0


def _codex_rollout_path_from_row(row):
    if not row:
        return None
    raw = row.get("rollout_path") or ""
    if raw:
        p = Path(os.path.expanduser(raw))
        if p.is_file():
            return p
    tid = row.get("id") or ""
    if tid and _core.CODEX_SESSIONS_ROOT.is_dir():
        try:
            matches = list(_core.CODEX_SESSIONS_ROOT.glob(f"**/*{tid}*.jsonl"))
        except OSError:
            matches = []
        if matches:
            matches.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            return matches[0]
    return None


def _resolve_codex_rollout_path(thread_id):
    row = _core._codex_thread_row(thread_id)
    return _core._codex_rollout_path_from_row(row)


def _is_codex_session(session_id):
    return bool(_core._codex_thread_row(session_id))


_KIMI_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


def _kimi_session_dir(session_id):
    """~/.kimi-code/sessions/<wd_*>/session_<id> for a Kimi session id, or None.

    The CLI's own session_index.jsonl is authoritative when it knows the id;
    otherwise glob the sessions tree. (The ACP-level _is_kimi_session further
    down answers "is this a Kimi session at all" — this one resolves the
    on-disk transcript dir the throughput engine reads.)
    """
    sid = str(session_id or "").strip()
    if not sid or not _KIMI_SESSION_ID_RE.fullmatch(sid):
        return None
    try:
        indexed = (_core._kimi_session_index().get(sid) or {}).get("session_dir") or ""
        if indexed:
            path = Path(indexed)
            if path.is_dir():
                return path
    except Exception:
        pass
    root = _core.KIMI_SESSIONS_ROOT
    if not root.is_dir():
        return None
    try:
        matches = [
            p for p in root.glob(f"*/session_{sid}")
            if p.is_dir()
        ]
    except OSError:
        return None
    return matches[0] if matches else None


def _kimi_wd_folder_path(wd_name):
    """wd_<path-with-underscores>_<hash> → the working-dir portion, best effort."""
    text = str(wd_name or "")
    if text.startswith("wd_"):
        text = text[len("wd_"):]
    m = re.match(r"^(.*)_([0-9a-f]{8,16})$", text)
    if m:
        text = m.group(1)
    return text


def _throughput_kimi_wire_files(session_dir):
    """All agent wire transcripts under a Kimi session dir, main agent first."""
    if session_dir is None:
        return []
    try:
        files = [p for p in session_dir.glob("agents/*/wire.jsonl") if p.is_file()]
    except OSError:
        return []
    files.sort(key=lambda p: (p.parent.name != "main", p.parent.name))
    return files


def _codex_tool_name(name):
    return (name or "").rsplit(".", 1)[-1]


_CODEX_OPAQUE_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-+/=]+")


def _codex_opaque_token(value):
    """True for encrypted/opaque agent-runtime tokens (e.g. the `message` blob
    codex puts on spawn_agent/followup_task calls: a long whitespace-free
    base64url string). Those must never be used as human-readable summaries."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return len(text) > 60 and bool(_CODEX_OPAQUE_TOKEN_RE.fullmatch(text))


def _codex_js_string_literal_value(literal):
    if not isinstance(literal, str) or not literal:
        return ""
    try:
        value = json.loads(literal)
        return value if isinstance(value, str) else ""
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        value = ast.literal_eval(literal)
        return value if isinstance(value, str) else ""
    except (SyntaxError, ValueError, TypeError):
        return literal.strip("\"'")


def _codex_custom_tool_arg(source, key):
    if not isinstance(source, str) or not source or not key:
        return ""
    # Handles the current Codex custom-tool JS body:
    #   tools.exec_command({ cmd: "...", sandbox_permissions: "..." })
    # It is intentionally small and best-effort; rollout remains authoritative.
    # Per-quote alternatives with disjoint branches (`\\.` vs `[^"\\]`) and
    # possessive quantifiers: a backslash can only ever start an escape pair,
    # so the engine never backtracks. The old tempered-dot form
    # `(?:\\.|(?!(?P=quote)).)*` was ambiguous on backslashes and went
    # exponential on escape-heavy unclosed strings (real Codex rollouts
    # pinned a request thread at 100% CPU for minutes).
    pattern = (
        r"(?<![A-Za-z0-9_$])(?:" + re.escape(key)
        + r'|"' + re.escape(key) + r'"|\'' + re.escape(key) + r"')"
        + r"\s*:\s*(?:\"(?P<dbody>(?:\\.|[^\"\\])*+)\""
        + r"|'(?P<sbody>(?:\\.|[^'\\])*+)')"
    )
    match = re.search(pattern, source, re.DOTALL)
    if not match:
        return ""
    if match.group("dbody") is not None:
        quote, body = '"', match.group("dbody")
    else:
        quote, body = "'", match.group("sbody")
    return _codex_js_string_literal_value(quote + body + quote)


def _codex_custom_tool_kind(source, fallback_name=""):
    text = source if isinstance(source, str) else ""
    if "tools.exec_command" in text:
        return "Bash"
    if "tools.apply_patch" in text:
        return "apply_patch"
    if "tools.update_plan" in text:
        return "update_plan"
    if "tools.write_stdin" in text:
        return "write_stdin"
    if "tools.web__run" in text:
        return "web_search"
    if "tools.update_goal" in text:
        return "update_goal"
    return _codex_tool_name(fallback_name) or "tool"


_CODEX_JS_OBJ_KEY_RE = re.compile(r'([{,]\s*)([A-Za-z_$][A-Za-z0-9_$]*)\s*:')


def _codex_custom_tool_plan_entries(source):
    """Best-effort extraction of update_plan entries from a custom-tool JS body
    (`tools.update_plan({plan: [{step: "...", status: "..."}, ...]})`).

    The body is a JS object literal, not JSON, so this scans for the `plan`
    array, bracket-matches it (string-aware), then repairs unquoted keys per
    entry object before json.loads. Returns [] on any shape mismatch — the
    caller then falls back to rendering the call as a plain tool row.
    """
    text = source if isinstance(source, str) else ""
    match = re.search(r'(?<![A-Za-z0-9_$"])plan\s*:\s*\[', text)
    if not match:
        return []
    start = match.end() - 1
    depth = 0
    quote = None
    escaped = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("\"", "'", "`"):
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return []
    entries = []
    for obj_match in re.finditer(r"\{[^{}]*\}", text[start:end + 1]):
        try:
            obj = json.loads(_CODEX_JS_OBJ_KEY_RE.sub(r'\1"\2":', obj_match.group(0)))
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        step = str(obj.get("step") or obj.get("content") or "").strip()
        if step:
            entries.append({
                "content": step[:300],
                "status": str(obj.get("status") or "pending"),
            })
    return entries


_CODEX_PATCH_FILE_RE = re.compile(r"\*\*\* (?:Add|Update|Delete) File: ([^\"\\\n]+)")
_CODEX_WEB_QUERY_RE = re.compile(r'(?<![A-Za-z0-9_$"])q\s*:\s*"((?:\\.|[^"\\])*)"')


def _codex_custom_tool_detail(source, fallback_name=""):
    text = source if isinstance(source, str) else ""
    cmd = _core._codex_custom_tool_arg(text, "cmd") or _core._codex_custom_tool_arg(text, "command")
    if cmd:
        return _core._shell_command_activity_label(cmd)
    if "tools.apply_patch" in text:
        # The patch body is a JS string literal in the custom-tool body, so
        # the file markers survive as `*** Add File: <path>\n` text. Surface
        # the touched file(s) instead of the bare "apply_patch" fallback.
        files = [f.strip() for f in _CODEX_PATCH_FILE_RE.findall(text) if f.strip()]
        if files:
            first = files[0]
            return first if len(files) == 1 else f"{first} (+{len(files) - 1} more)"
    if "tools.web__run" in text:
        queries = [
            _codex_js_string_literal_value('"' + m + '"')
            for m in _CODEX_WEB_QUERY_RE.findall(text)
        ]
        queries = [q.strip() for q in queries if q.strip()]
        if queries:
            return queries[0] if len(queries) == 1 else f"{queries[0]} (+{len(queries) - 1} more)"
    if "tools.update_goal" in text:
        status = _core._codex_custom_tool_arg(text, "status")
        if status:
            return status
    if "tools.write_stdin" in text:
        chars = _core._codex_custom_tool_arg(text, "chars")
        if chars:
            return chars
    for key in ("path", "file_path", "filename", "query", "pattern", "title", "prompt", "message"):
        value = _core._codex_custom_tool_arg(text, key)
        if value and not _codex_opaque_token(value):
            return value
    return _codex_custom_tool_kind(text, fallback_name)


def _codex_custom_tool_command(source):
    text = source if isinstance(source, str) else ""
    return _core._codex_custom_tool_arg(text, "cmd") or _core._codex_custom_tool_arg(text, "command")


def _codex_custom_tool_workdir(source):
    return _core._codex_custom_tool_arg(source if isinstance(source, str) else "", "workdir")


def _codex_tool_requires_approval(name, args=None, raw_text=""):
    args = args if isinstance(args, dict) else {}
    sandbox = args.get("sandbox_permissions") or args.get("sandboxPermissions") or ""
    if str(sandbox) == "require_escalated":
        return True
    return _core._codex_custom_tool_arg(raw_text, "sandbox_permissions") == "require_escalated"


def _codex_tool_approval_message(args=None, raw_text=""):
    args = args if isinstance(args, dict) else {}
    for key in ("justification", "approval_prompt", "approvalPrompt"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _core._codex_custom_tool_arg(raw_text, "justification").strip()


def _codex_args(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _codex_tool_detail(name, args):
    lname = _codex_tool_name(name)
    if lname == "exec_command":
        return _core._shell_command_activity_label(args.get("cmd") or args.get("command") or "")
    if lname == "write_stdin":
        return args.get("chars") or args.get("session_id") or ""
    if lname == "spawn_agent":
        # `message` is an encrypted runtime token — the task name is the only
        # human-readable summary on the call.
        task_name = args.get("task_name")
        if isinstance(task_name, str) and task_name.strip():
            return task_name.strip()
    if lname == "followup_task":
        target = args.get("target")
        if isinstance(target, str) and target.strip():
            return target.strip().rsplit("/", 1)[-1]
    if lname in ("wait_agent", "wait"):
        cell_id = args.get("cell_id")
        if lname == "wait" and cell_id not in (None, ""):
            return f"cell {cell_id}"
        timeout_ms = args.get("timeout_ms") or args.get("yield_time_ms")
        if isinstance(timeout_ms, (int, float)) and timeout_ms > 0:
            return f"timeout {timeout_ms / 1000:g}s"
        if cell_id not in (None, ""):
            return f"cell {cell_id}"
        return ""
    for key in ("path", "file_path", "filename", "query", "pattern", "prompt", "message"):
        val = args.get(key)
        if isinstance(val, str) and val and not _codex_opaque_token(val):
            return val
    return ""


def _codex_tool_command(name, args):
    lname = _codex_tool_name(name)
    if lname == "exec_command":
        cmd = args.get("cmd") or args.get("command") or ""
        return cmd if isinstance(cmd, str) else ""
    return ""


def _codex_tool_workdir(name, args):
    lname = _codex_tool_name(name)
    if lname == "exec_command":
        workdir = args.get("workdir") or ""
        return workdir if isinstance(workdir, str) else ""
    return ""


def _codex_event_epoch(ev):
    ts = ev.get("timestamp") or ""
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    ts = ts or payload.get("timestamp") or payload.get("started_at") or payload.get("completed_at") or ""
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _codex_event_timestamp(ev):
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    return ev.get("timestamp") or payload.get("timestamp") or payload.get("started_at") or ""


_SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_WRAPPERS = {"command", "builtin", "exec", "noglob"}
_SHELLS = {"sh", "bash", "zsh"}
_GIT_OPTS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--config-env",
    "--exec-path",
    "--super-prefix",
}
_GIT_OPTS_WITH_VALUE_PREFIXES = (
    "-C",
    "-c",
    "--git-dir=",
    "--work-tree=",
    "--namespace=",
    "--config-env=",
    "--exec-path=",
    "--super-prefix=",
)
_ENV_OPTS_WITH_VALUE = {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}
_SUDO_OPTS_WITH_VALUE = {"-u", "-g", "-h", "-p", "-C", "-T"}


def _shell_words(cmd):
    """Tokenize shell-ish command text without treating quoted text as code."""
    src = cmd or ""
    if "<<" not in src:
        src = src.replace("\n", " ; ")
    try:
        lex = shlex.shlex(src, posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        return list(lex)
    except (TypeError, ValueError):
        try:
            return shlex.split(src)
        except ValueError:
            return src.split()


def _shell_segments(cmd):
    segment = []
    for tok in _shell_words(cmd):
        if tok and all(ch in ";&|" for ch in tok):
            if segment:
                yield segment
                segment = []
            continue
        segment.append(tok)
    if segment:
        yield segment


def _is_shell_assignment(tok):
    return bool(_SHELL_ASSIGNMENT_RE.match(tok or ""))


def _skip_sudo_options(tokens, i):
    while i < len(tokens) and tokens[i].startswith("-"):
        opt = tokens[i]
        if opt in _SUDO_OPTS_WITH_VALUE and i + 1 < len(tokens):
            i += 2
        else:
            i += 1
    return i


def _skip_env_options(tokens, i):
    while i < len(tokens):
        opt = tokens[i]
        if _is_shell_assignment(opt):
            i += 1
        elif opt in _ENV_OPTS_WITH_VALUE and i + 1 < len(tokens):
            i += 2
        elif opt.startswith("--unset=") or opt.startswith("--chdir="):
            i += 1
        elif opt.startswith("-"):
            i += 1
        else:
            break
    return i


def _shell_command_start(tokens):
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _is_shell_assignment(tok) or tok in _SHELL_WRAPPERS:
            i += 1
            continue
        base = os.path.basename(tok)
        if base == "sudo":
            i = _skip_sudo_options(tokens, i + 1)
            continue
        if base == "env":
            i = _skip_env_options(tokens, i + 1)
            continue
        break
    return i


def _shell_nested_command(tokens, start):
    if start >= len(tokens) or os.path.basename(tokens[start]) not in _SHELLS:
        return None
    i = start + 1
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            i += 1
            continue
        if tok.startswith("-") and "c" in tok[1:]:
            return tokens[i + 1] if i + 1 < len(tokens) else None
        i += 1
    return None


def _resolve_shell_path(path, base_cwd=None):
    if not path:
        return None
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    base = os.path.expanduser(base_cwd) if base_cwd else os.getcwd()
    return os.path.abspath(os.path.join(base, expanded))


def _git_invocation(tokens, start, base_cwd=None):
    if start >= len(tokens) or os.path.basename(tokens[start]) != "git":
        return None
    used_dash_c = False
    dash_c_path = None
    i = start + 1
    while i < len(tokens):
        opt = tokens[i]
        if opt == "--":
            i += 1
            break
        if opt in _GIT_OPTS_WITH_VALUE and i + 1 < len(tokens):
            used_dash_c = used_dash_c or opt == "-C"
            if opt == "-C":
                dash_c_path = _resolve_shell_path(tokens[i + 1], base_cwd)
            i += 2
            continue
        if any(opt.startswith(prefix) and opt != prefix for prefix in _GIT_OPTS_WITH_VALUE_PREFIXES):
            used_dash_c = used_dash_c or opt.startswith("-C")
            if opt.startswith("-C"):
                dash_c_path = _resolve_shell_path(opt[2:], base_cwd)
            i += 1
            continue
        if opt.startswith("-"):
            i += 1
            continue
        break
    subcmd = tokens[i] if i < len(tokens) else ""
    return {
        "subcmd": subcmd,
        "subcmd_index": i,
        "used_dash_c": used_dash_c,
        "dash_c_path": dash_c_path,
    }


def _gh_pr_create(tokens, start):
    if start >= len(tokens) or os.path.basename(tokens[start]) != "gh":
        return False
    i = start + 1
    while i < len(tokens) and tokens[i].startswith("-"):
        opt = tokens[i]
        if opt in ("-R", "--repo", "--hostname") and i + 1 < len(tokens):
            i += 2
        else:
            i += 1
    return tokens[i:i + 2] == ["pr", "create"]


# shlex tokenization is the single most expensive thing a cold archive scan
# does (~28% of a 147s walk: 3.4M read_token calls over ~180k Bash commands).
# The token walk below can only produce a signal from `cd`, a `git ...`
# invocation, or `gh pr create` — every other command tokenizes to nothing.
# This prefilter runs on the RAW string, so it is a strict superset of what
# the walk can see (it also matches inside quotes, keeping nested
# `bash -c "git push"` on the slow path). No hint word => skip tokenizing.
_SHELL_SIGNAL_HINT_RE = re.compile(r"(?:\A|[^A-Za-z0-9_])(?:cd|git|gh)(?:[^A-Za-z0-9_]|\Z)")


def _shell_command_signals(cmd, base_cwd=None, _depth=0):
    """Return edit/commit/push/pr/external-cd flags for a shell command."""
    cmd = cmd or ""
    worktree_path = None
    worktree_branch = None
    subcommands = set()
    pr_create = False
    external_cd = False
    _segments = _shell_segments(cmd) if _SHELL_SIGNAL_HINT_RE.search(cmd) else ()
    for toks in _segments:
        start = _shell_command_start(toks)
        if start >= len(toks):
            continue

        nested = _shell_nested_command(toks, start)
        if nested and _depth < 2:
            child = _core._shell_command_signals(nested, base_cwd=base_cwd, _depth=_depth + 1)
            subcommands.update(k for k in ("commit", "push") if child[k])
            pr_create = pr_create or child["pr"]
            external_cd = external_cd or child["external_cd"]
            worktree_path = child.get("worktree_path") or worktree_path
            worktree_branch = child.get("worktree_branch") or worktree_branch
            continue

        exe = os.path.basename(toks[start])
        if exe == "cd":
            target = toks[start + 1] if start + 1 < len(toks) else ""
            external_cd = external_cd or target.startswith("/") or target.startswith("~")
            continue

        git = _git_invocation(toks, start, base_cwd=base_cwd)
        if git:
            subcmd = git["subcmd"]
            if subcmd in ("commit", "push"):
                subcommands.add(subcmd)
            external_cd = external_cd or git["used_dash_c"]
            if subcmd == "worktree" and git["subcmd_index"] + 1 < len(toks):
                if toks[git["subcmd_index"] + 1] == "add":
                    branch = None
                    path = None
                    k = git["subcmd_index"] + 2
                    while k < len(toks):
                        part = toks[k]
                        if part in ("-b", "-B") and k + 1 < len(toks):
                            branch = toks[k + 1]
                            k += 2
                            continue
                        if part in ("--reason", "--lock") and k + 1 < len(toks):
                            k += 2
                            continue
                        if part == "--":
                            k += 1
                            continue
                        if part.startswith("-"):
                            k += 1
                            continue
                        path = part
                        break
                    if path:
                        path_base = git.get("dash_c_path") or base_cwd
                        worktree_path = _resolve_shell_path(path, path_base)
                        worktree_branch = branch or worktree_branch
            continue

        pr_create = pr_create or _gh_pr_create(toks, start)

    edit_like = bool(re.search(
        r"\b(apply_patch|tee|sed\s+-i|perl\s+-pi)\b|"
        r"(?:^|[\s;&|])cat\s+>|write_text\s*\(|"
        r"(?:^|[\s;&|])(?:printf|echo)\b[^;\n]*>\s*\S+",
        cmd,
    ))
    return {
        "edit": edit_like,
        "commit": "commit" in subcommands,
        "push": "push" in subcommands,
        "pr": pr_create,
        "external_cd": external_cd,
        "worktree_path": worktree_path,
        "worktree_branch": worktree_branch,
    }


def _codex_command_signals(cmd, base_cwd=None):
    return _core._shell_command_signals(cmd, base_cwd=base_cwd)


_CODEX_SUMMARY_BRANCH_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?Branch:\s*`?([^\r\n`]+?)`?\s*$")
_CODEX_SUMMARY_WORKTREE_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?Worktree:\s*`?([^\r\n`]+?)`?\s*$")


def _extract_codex_summary_signals(text, pr_url_re):
    """Extract final-summary PR/worktree fields from Codex prose."""
    out = {}
    if not isinstance(text, str) or not text:
        return out
    mp = pr_url_re.search(text)
    if mp:
        out["tail_pr_number"] = int(mp.group(2))
        out["tail_pr_url"] = (
            "https://github.com/" + mp.group(1) + "/pull/" + mp.group(2)
        )
    mb = _CODEX_SUMMARY_BRANCH_RE.search(text)
    if mb:
        branch = mb.group(1).strip().strip("`")
        if branch:
            out["tail_branch"] = branch
    mw = _CODEX_SUMMARY_WORKTREE_RE.search(text)
    if mw:
        worktree = mw.group(1).strip().strip("`")
        if worktree:
            out["tail_worktree_path"] = worktree
    return out


def _extract_codex_thread_id_from_log(log_path):
    if not log_path:
        return None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "thread.started" and ev.get("thread_id"):
                    return ev["thread_id"]
    except OSError:
        return None
    return None


def _codex_turn_failed_error_from_log(log_path):
    """Last `turn.failed` error message in a codex spawn/resume .log, or None.

    codex's own resumable rollout.jsonl never records turn.failed (only
    task_complete with last_agent_message:null) — the real reason a turn
    produced no visible text lives only in CCC's raw process capture. One
    spawn/resume invocation is one turn, so at most one turn.failed exists
    per log (CCC-424).
    """
    if not log_path:
        return None
    last_error = None
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "turn.failed":
                    err = ev.get("error")
                    msg = err.get("message") if isinstance(err, dict) else None
                    if msg:
                        last_error = str(msg).strip()
    except OSError:
        return None
    return last_error


def _codex_logs_for_session(session_id):
    """Every CCC spawn/resume .log whose thread_id matches session_id, as
    (mtime, path) pairs sorted oldest-first.

    Scope the log search to the thread's own repository. Falling back to all
    recent Codex repositories made opening a silent result scan hundreds of
    unrelated worktrees before its transcript could render.
    """
    if not session_id:
        return []
    repo_paths = []
    row = _core._codex_thread_row(session_id) or {}
    cwd = row.get("cwd") or ""
    if cwd:
        try:
            repo = (
                _core._git_toplevel_for_existing_dir(cwd)
                or _core._nearest_marked_repo_dir(cwd)
                or str(Path(cwd).expanduser().resolve())
            )
        except (OSError, RuntimeError, ValueError):
            repo = ""
        if repo:
            repo_paths.append(repo)
    out = []
    for log in _core._recent_codex_ccc_log_paths(repo_paths=repo_paths):
        if _core._extract_codex_thread_id_from_log(log) != session_id:
            continue
        try:
            mtime = Path(log).stat().st_mtime
        except OSError:
            continue
        out.append((mtime, str(log)))
    out.sort(key=lambda item: item[0])
    return out


def _enrich_codex_no_agent_output_events(conversation_id, events):
    """Attach the real turn.failed error to codex 'no visible response'
    result events by correlating with CCC's raw spawn/resume .log capture
    (CCC-424). No-op, and cheap, when nothing in `events` is silent."""
    silent = [e for e in events if e.get("type") == "result" and e.get("no_agent_output")]
    if not silent:
        return events
    logs = _core._codex_logs_for_session(conversation_id)
    if not logs:
        return events
    for ev in silent:
        target_epoch = _core._iso_to_epoch(ev.get("ts")) or 0.0
        best_log = min(logs, key=lambda item: abs(item[0] - target_epoch))[1] if target_epoch else logs[-1][1]
        error = _codex_turn_failed_error_from_log(best_log)
        if error:
            ev["turn_failed_error"] = error[:400]
    return events


def _codex_sidebar_backfill_window_days():
    raw = os.environ.get("CCC_CODEX_SIDEBAR_BACKFILL_DAYS", "7")
    try:
        days = float(raw)
    except (TypeError, ValueError):
        days = 7.0
    return days if days > 0 else 7.0


def _codex_sidebar_backfill_enabled():
    """Whether the non-critical Codex sidebar migration may run at startup."""
    raw = (os.environ.get("CCC_CODEX_SIDEBAR_BACKFILL") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _spawn_registry_entry_epoch(entry):
    raw = (entry or {}).get("spawned_at") or (entry or {}).get("started")
    if not raw:
        return 0.0
    text = str(raw).strip()
    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%S").timestamp()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _codex_registry_entry_epoch(entry):
    return _spawn_registry_entry_epoch(entry)


def _recent_codex_thread_repo_paths(days=None, now=None, limit=1000):
    if days is None:
        days = _codex_sidebar_backfill_window_days()
    try:
        now = float(now if now is not None else time.time())
    except (TypeError, ValueError):
        now = time.time()
    cutoff = now - (float(days) * 86400.0)
    out = []
    rows = _core._codex_fetch_threads(limit=limit)
    for row in rows:
        ts = _core._codex_ts_seconds(row, "updated") or _core._codex_ts_seconds(row, "created")
        if ts and ts < cutoff:
            break
        cwd = row.get("cwd") or ""
        if not cwd:
            continue
        try:
            repo = (
                _core._git_toplevel_for_existing_dir(cwd)
                or _core._nearest_marked_repo_dir(cwd)
                or str(Path(cwd).expanduser().resolve())
            )
        except (OSError, RuntimeError, ValueError):
            continue
        if repo:
            out.append(repo)
    return out


def _recent_codex_ccc_log_paths(repo_paths=None, days=None, now=None, max_logs=2000):
    """Recent CCC Codex spawn/resume logs, bounded to known repos."""
    if days is None:
        days = _codex_sidebar_backfill_window_days()
    try:
        max_logs = max(1, int(max_logs or 2000))
    except (TypeError, ValueError):
        max_logs = 2000
    try:
        now = float(now if now is not None else time.time())
    except (TypeError, ValueError):
        now = time.time()
    cutoff = now - (float(days) * 86400.0)
    raw_paths = []
    try:
        for entry in _core._load_spawn_registry():
            if entry.get("engine") == "codex" and entry.get("log"):
                raw_paths.append(entry.get("log"))
    except Exception:
        pass
    if repo_paths is None:
        try:
            repo_paths = [
                *_core._known_repo_paths(),
                *_recent_codex_thread_repo_paths(days=days, now=now),
            ]
        except Exception:
            repo_paths = []
    for repo_path in repo_paths or []:
        try:
            log_dir = _core.repo_log_dir(repo_path)
        except Exception:
            continue
        if not log_dir.is_dir():
            continue
        for pattern in ("spawn-codex-*.log", "resume-codex-*.log"):
            try:
                raw_paths.extend(log_dir.glob(pattern))
            except OSError:
                continue

    seen = set()
    recent = []
    for raw in raw_paths:
        try:
            p = Path(raw).expanduser().resolve()
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            st = p.stat()
        except (OSError, ValueError, RuntimeError):
            continue
        if st.st_mtime < cutoff:
            continue
        recent.append((st.st_mtime, p))
    recent.sort(key=lambda item: item[0], reverse=True)
    return [p for _, p in recent[:max_logs]]


def _codex_sidebar_project_root_for_thread(row):
    cwd = (row or {}).get("cwd") or ""
    if not cwd:
        return None
    try:
        root = (
            _core._git_toplevel_for_existing_dir(cwd)
            or _core._nearest_marked_repo_dir(cwd)
            or str(Path(cwd).expanduser().resolve())
        )
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        p = Path(root).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return str(p) if p.is_dir() else None


def _codex_sidebar_project_roots_from_values(repo_paths):
    roots = []
    seen = set()
    for repo in repo_paths or []:
        try:
            root = str(Path(repo).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        if root in seen or not Path(root).is_dir():
            continue
        seen.add(root)
        roots.append(root)
    return roots


def _codex_backfill_candidate_rows(days=None, repo_paths=None, now=None, limit=1000):
    """Recent Codex exec/cli rows that can be made sidebar-visible.

    This complements CCC spawn logs: the Codex DB is the durable index, while
    old CCC logs/registry entries can be pruned or missed. Keep the candidate
    set narrow so we only rewrite rows that Codex itself classifies as
    non-interactive and otherwise user-owned.
    """
    if days is None:
        days = _codex_sidebar_backfill_window_days()
    try:
        now = float(now if now is not None else time.time())
    except (TypeError, ValueError):
        now = time.time()
    cutoff = now - (float(days) * 86400.0)
    try:
        limit = max(1, int(limit or 1000))
    except (TypeError, ValueError):
        limit = 1000

    allowed_roots = None
    if repo_paths is not None:
        allowed_roots = set(_codex_sidebar_project_roots_from_values(repo_paths))

    out = []
    for row in _core._codex_fetch_threads(limit=limit):
        source = row.get("source")
        thread_source = row.get("thread_source")
        if source not in ("exec", "cli"):
            continue
        if thread_source and thread_source != "user":
            continue
        ts = _core._codex_ts_seconds(row, "updated") or _core._codex_ts_seconds(row, "created")
        if ts and ts < cutoff:
            break
        if allowed_roots is not None:
            root = _codex_sidebar_project_root_for_thread(row)
            if root not in allowed_roots:
                continue
        out.append(row)
    return out


def _codex_global_state_path():
    return Path.home() / ".codex" / ".codex-global-state.json"


def _read_codex_global_state():
    try:
        data = json.loads(_codex_global_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _codex_global_state_string_list(data, key):
    values = (data or {}).get(key)
    if not isinstance(values, list):
        return []
    return [str(x) for x in values if isinstance(x, str) and x.strip()]


def _codex_saved_workspace_roots():
    return _codex_global_state_string_list(
        _read_codex_global_state(),
        "electron-saved-workspace-roots",
    )


def _codex_desktop_app_is_running():
    if platform.system() != "Darwin":
        return False
    try:
        proc = subprocess.run(
            ["ps", "-axo", "command"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        if ".app/Contents/MacOS/Codex" in line:
            return True
    return False


def _open_codex_workspace_root_deeplink(root):
    if platform.system() != "Darwin" or not shutil.which("open"):
        return False
    url = "codex://new?path=" + urllib.parse.quote(str(root), safe="")
    try:
        proc = subprocess.run(
            ["open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _wait_for_codex_workspace_roots(roots, timeout_s=2.0):
    wanted = set(roots or [])
    if not wanted:
        return set()
    deadline = time.monotonic() + max(0.0, float(timeout_s or 0))
    present = set(_codex_saved_workspace_roots())
    while not wanted.issubset(present) and time.monotonic() < deadline:
        time.sleep(0.1)
        present = set(_codex_saved_workspace_roots())
    return present


def _register_codex_sidebar_project_roots_live(roots):
    current = set(_codex_saved_workspace_roots())
    missing = [root for root in roots if root not in current]
    if not missing:
        return 0
    data = _read_codex_global_state() or {}
    active_roots = _codex_global_state_string_list(data, "active-workspace-roots")
    for root in missing:
        _core._open_codex_workspace_root_deeplink(root)
    if active_roots:
        active = active_roots[0]
        try:
            if Path(active).expanduser().is_dir():
                _core._open_codex_workspace_root_deeplink(active)
        except (OSError, RuntimeError, ValueError):
            pass
    present = _core._wait_for_codex_workspace_roots(missing)
    return sum(1 for root in missing if root in present)


def _append_codex_project_roots_to_global_state(roots):
    state_path = _codex_global_state_path()
    data = _read_codex_global_state()
    if data is None:
        return 0

    saved_before = set(_codex_global_state_string_list(
        data,
        "electron-saved-workspace-roots",
    ))
    changed = False
    for key in ("electron-saved-workspace-roots", "project-order"):
        current = _codex_global_state_string_list(data, key)
        present = set(current)
        added = [root for root in roots if root not in present]
        if not added:
            data[key] = current
            continue
        data[key] = added + current
        changed = True

    if not changed:
        return 0
    try:
        tmp = state_path.with_name(f"{state_path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, state_path)
    except OSError:
        return 0
    saved_after = set(_codex_saved_workspace_roots())
    return sum(1 for root in roots if root not in saved_before and root in saved_after)


def _append_codex_sidebar_project_roots(repo_paths):
    """Add CCC-proven Codex repos to Codex Desktop's project sidebar state."""
    roots = _codex_sidebar_project_roots_from_values(repo_paths)
    if not roots:
        return 0
    if _read_codex_global_state() is None:
        return 0

    if _core._codex_desktop_app_is_running():
        return _register_codex_sidebar_project_roots_live(roots)
    return _append_codex_project_roots_to_global_state(roots)


def backfill_codex_sidebar_visibility(days=None, repo_paths=None, now=None, max_logs=2000):
    """Mark recent CCC-spawned Codex threads as Codex Desktop sidebar-visible."""
    if days is None:
        days = _codex_sidebar_backfill_window_days()
    try:
        now = float(now if now is not None else time.time())
    except (TypeError, ValueError):
        now = time.time()
    cutoff = now - (float(days) * 86400.0)
    ids = []
    seen_ids = set()

    def add_sid(sid):
        sid = str(sid or "").strip()
        if sid and sid not in seen_ids:
            seen_ids.add(sid)
            ids.append(sid)

    log_paths = _core._recent_codex_ccc_log_paths(
        repo_paths=repo_paths,
        days=days,
        now=now,
        max_logs=max_logs,
    )
    for path in log_paths:
        add_sid(_core._extract_codex_thread_id_from_log(path))

    try:
        registry_entries = _core._load_spawn_registry()
    except Exception:
        registry_entries = []
    for entry in registry_entries:
        if entry.get("engine") != "codex":
            continue
        entry_epoch = _codex_registry_entry_epoch(entry)
        if entry_epoch and entry_epoch < cutoff:
            continue
        log_path = entry.get("log")
        if log_path and not entry_epoch:
            try:
                if Path(log_path).expanduser().stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
        if not entry_epoch and not log_path:
            continue
        add_sid(entry.get("session_id") or entry.get("resumed_sid"))
        if log_path:
            add_sid(_core._extract_codex_thread_id_from_log(log_path))

    for row in _codex_backfill_candidate_rows(
        days=days,
        repo_paths=repo_paths,
        now=now,
        limit=max_logs,
    ):
        add_sid(row.get("id"))

    updated = 0
    already_visible = 0
    skipped = 0
    project_roots = []
    for sid in ids:
        before_row = _core._codex_thread_row(sid) or {}
        ok = _core._mark_codex_thread_user_visible(sid)
        after_row = _core._codex_thread_row(sid) or {}
        _restore_codex_rollout_mtime_from_row(after_row)
        root = _codex_sidebar_project_root_for_thread(after_row)
        if root:
            project_roots.append(root)
        after_thread_source = after_row.get("thread_source")
        after_source = after_row.get("source")
        if ok and after_thread_source == "user" and (after_source in (None, "vscode")):
            if (
                before_row.get("thread_source") == "user"
                and before_row.get("source") in (None, "vscode")
            ):
                already_visible += 1
            else:
                updated += 1
        else:
            skipped += 1
    projects_added = _core._append_codex_sidebar_project_roots(project_roots)
    return {
        "ok": True,
        "days": days,
        "scanned_logs": len(log_paths),
        "found": len(ids),
        "updated": updated,
        "already_visible": already_visible,
        "skipped": skipped,
        "projects_added": projects_added,
    }


def _codex_sidebar_visibility_backfill_once():
    try:
        result = _core.backfill_codex_sidebar_visibility()
    except Exception as e:
        print(f"  [codex-sidebar] backfill skipped ({e})")
        return
    if result.get("updated"):
        print(
            "  [codex-sidebar] marked "
            f"{result['updated']} recent CCC Codex thread(s) as IDE-visible"
        )
    if result.get("projects_added"):
        print(
            "  [codex-sidebar] registered "
            f"{result['projects_added']} recent CCC Codex project(s) with Codex Desktop"
        )


def _claude_desktop_backfill_window_days():
    raw = os.environ.get("CCC_CLAUDE_DESKTOP_BACKFILL_DAYS", "7")
    try:
        days = float(raw)
    except (TypeError, ValueError):
        days = 7.0
    return days if days > 0 else 7.0


def _claude_desktop_startup_backfill_max_logs():
    raw = os.environ.get("CCC_CLAUDE_DESKTOP_STARTUP_BACKFILL_MAX_LOGS", "200")
    try:
        max_logs = int(raw)
    except (TypeError, ValueError):
        max_logs = 200
    return max(1, max_logs)


def _is_probable_claude_spawn_log(path):
    name = Path(path).name
    blocked_prefixes = (
        "spawn-codex-",
        "resume-codex-",
        "spawn-gemini-",
        "resume-gemini-",
        "spawn-antigravity-",
        "resume-antigravity-",
    )
    if name.startswith(blocked_prefixes):
        return False
    return (
        (name.startswith("spawn-") or name.startswith("resume-"))
        and name.endswith(".log")
        and not name.endswith(".agy.log")
    )


def _recent_claude_ccc_log_paths(repo_paths=None, days=None, now=None, max_logs=2000):
    """Recent CCC Claude spawn/resume logs, bounded to known repos."""
    if days is None:
        days = _claude_desktop_backfill_window_days()
    try:
        max_logs = max(1, int(max_logs or 2000))
    except (TypeError, ValueError):
        max_logs = 2000
    try:
        now = float(now if now is not None else time.time())
    except (TypeError, ValueError):
        now = time.time()
    cutoff = now - (float(days) * 86400.0)
    raw_paths = []
    registry_entries = []
    try:
        registry_entries = _core._load_spawn_registry()
    except Exception:
        registry_entries = []
    for entry in registry_entries:
        engine = entry.get("engine") or "claude"
        if engine == "claude" and entry.get("log"):
            raw_paths.append(entry.get("log"))

    if repo_paths is None:
        candidates = []
        try:
            candidates.extend(_core._known_repo_paths())
        except Exception:
            pass
        try:
            candidates.extend(_core._discover_repo_paths_from_projects())
        except Exception:
            pass
        for entry in registry_entries:
            engine = entry.get("engine") or "claude"
            if engine != "claude":
                continue
            for key in ("repo_path", "cwd"):
                if entry.get(key):
                    candidates.append(entry.get(key))
        repo_paths = candidates

    seen_repos = set()
    for repo_path in repo_paths or []:
        try:
            repo = str(Path(repo_path).expanduser().resolve())
        except (OSError, RuntimeError, ValueError, TypeError):
            continue
        if repo in seen_repos:
            continue
        seen_repos.add(repo)
        try:
            log_dir = _core.repo_log_dir(repo)
        except Exception:
            continue
        if not log_dir.is_dir():
            continue
        for pattern in ("spawn-*.log", "resume-*.log"):
            try:
                raw_paths.extend(log_dir.glob(pattern))
            except OSError:
                continue

    seen = set()
    recent = []
    for raw in raw_paths:
        try:
            p = Path(raw).expanduser().resolve()
            key = str(p)
            if key in seen or not _is_probable_claude_spawn_log(p):
                continue
            seen.add(key)
            st = p.stat()
        except (OSError, ValueError, RuntimeError, TypeError):
            continue
        if st.st_mtime < cutoff:
            continue
        recent.append((st.st_mtime, p))
    recent.sort(key=lambda item: item[0], reverse=True)
    return [p for _mtime, p in recent[:max_logs]]


def backfill_claude_desktop_visibility(
    days=None,
    repo_paths=None,
    now=None,
    max_logs=2000,
    skip_existing=False,
):
    """Create Claude Desktop sidebar metadata for recent CCC-spawned sessions."""
    if days is None:
        days = _claude_desktop_backfill_window_days()
    try:
        now = float(now if now is not None else time.time())
    except (TypeError, ValueError):
        now = time.time()
    cutoff = now - (float(days) * 86400.0)
    pruned = _core.prune_unresumable_claude_desktop_metadata().get("pruned", 0)
    ids = []
    seen_ids = set()
    entries_by_sid = {}

    def add_sid(sid, entry=None):
        sid = str(sid or "").strip()
        if not sid or not _core._SESSION_UUID_RE.match(sid):
            return
        if entry and sid not in entries_by_sid:
            entries_by_sid[sid] = entry
        if sid not in seen_ids:
            seen_ids.add(sid)
            ids.append(sid)

    log_paths = _recent_claude_ccc_log_paths(
        repo_paths=repo_paths,
        days=days,
        now=now,
        max_logs=max_logs,
    )
    for path in log_paths:
        add_sid(_core.extract_session_id(path))

    try:
        registry_entries = _core._load_spawn_registry()
    except Exception:
        registry_entries = []
    for entry in registry_entries:
        engine = entry.get("engine") or "claude"
        if engine != "claude":
            continue
        entry_epoch = _spawn_registry_entry_epoch(entry)
        if entry_epoch and entry_epoch < cutoff:
            continue
        log_path = entry.get("log")
        if log_path and not entry_epoch:
            try:
                if Path(log_path).expanduser().stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
        if not entry_epoch and not log_path:
            continue
        add_sid(entry.get("session_id") or entry.get("resumed_sid"), entry)
        if log_path:
            add_sid(_core.extract_session_id(log_path), entry)

    metadata_index = _core._claude_desktop_metadata_index()
    updated = 0
    already_visible = 0
    skipped = 0
    for sid in ids:
        before_path = _core._claude_desktop_metadata_path_for_cli_session(
            sid,
            metadata_index=metadata_index,
        )
        if skip_existing and before_path:
            already_visible += 1
            continue
        ok = _core._ensure_claude_desktop_session_visible(
            sid,
            spawn_entry=entries_by_sid.get(sid),
            metadata_index=metadata_index,
        )
        if ok:
            if before_path:
                already_visible += 1
            else:
                updated += 1
        else:
            skipped += 1

    return {
        "ok": True,
        "days": days,
        "scanned_logs": len(log_paths),
        "found": len(ids),
        "updated": updated,
        "already_visible": already_visible,
        "skipped": skipped,
        "pruned": pruned,
        "skip_existing": bool(skip_existing),
    }


def _claude_desktop_visibility_backfill_once():
    try:
        result = _core.backfill_claude_desktop_visibility(
            max_logs=_claude_desktop_startup_backfill_max_logs(),
            skip_existing=True,
        )
    except Exception as e:
        print(f"  [claude-desktop] backfill skipped ({e})")
        return
    if result.get("updated"):
        print(
            "  [claude-desktop] added "
            f"{result['updated']} recent CCC Claude session(s) to the Desktop sidebar"
        )


def _register_codex_sidebar_project_for_spawn_entry(entry, sid=None):
    if not isinstance(entry, dict):
        return 0
    root = None
    for key in ("repo_path", "cwd"):
        root = _codex_sidebar_project_root_for_thread({"cwd": entry.get(key) or ""})
        if root:
            break
    if not root and sid:
        root = _codex_sidebar_project_root_for_thread(_core._codex_thread_row(sid))
    if not root:
        return 0
    return _core._append_codex_sidebar_project_roots([root])


def _codex_spawn_pid_by_thread_id():
    out = {}
    for s in _core._spawned_sessions:
        if s.get("engine") != "codex":
            continue
        sid = s.get("session_id") or s.get("resumed_sid") or _core._extract_codex_thread_id_from_log(s.get("log"))
        if sid:
            try:
                alive = _core._poll_spawn_entry(s) is None
            except Exception:
                alive = False
            if _core._mark_codex_thread_user_visible(sid, update_rollout=True):
                _core._register_codex_sidebar_project_for_spawn_entry(s, sid)
            else:
                _schedule_codex_visibility_retry(sid, spawn_entry=s)
            if not s.get("session_id"):
                s["session_id"] = sid
                _core._update_spawn_session_id_in_registry(s.get("pid"), sid)
            parent_sid = s.get("parent_session_id") or ""
            if parent_sid:
                # Persist the link so it survives spawn-registry pruning.
                # First-time only (idempotent); cheap dict check skips the
                # file write on subsequent polls. (CCC-465)
                _core._persist_codex_parent_link(sid, parent_sid)
            _core._codex_thread_registry_upsert(
                sid,
                source="ccc-spawn-registry",
                visibility="user-visible",
                transport_owner=(
                    "ccc-managed-app-server"
                    if s.get("app_server_spawn") else
                    "ccc-codex-exec"
                ),
                transport=(
                    _core._codex_app_server_transport_kind()
                    if s.get("app_server_spawn") else
                    "codex-exec"
                ),
                cwd=s.get("cwd") or "",
                repo_path=s.get("repo_path") or "",
                title=s.get("name") or "",
                name=s.get("name") or "",
                parent_session_id=parent_sid,
                model=s.get("model") or "",
                ccc={
                    "spawn_id": s.get("spawn_id") or s.get("pid") or "",
                    "pid": s.get("pid") or "",
                    "log": s.get("log") or "",
                    "spawned_at": s.get("started") or "",
                    "prompt": s.get("prompt") or "",
                    "app_server_spawn": bool(s.get("app_server_spawn")),
                },
            )
            if sid not in out:
                out[sid] = {
                    "pid": s.get("pid"),
                    "alive": alive,
                    "log": s.get("log"),
                    "cwd": s.get("cwd") or "",
                    "repo_path": s.get("repo_path") or "",
                    "spawned_at": s.get("started") or "",
                    "prompt": s.get("prompt") or "",
                    "model": s.get("model") or "",
                    "parent_session_id": parent_sid,
                }
    for sid, entry in _core._codex_thread_registry_entries().items():
        if sid and sid not in out:
            out[sid] = _codex_thread_registry_spawn_shape(entry)
    return out


def _codex_cwd_matches_repo(cwd, repo_path, git_top_cache):
    if not cwd:
        return False
    try:
        p = Path(cwd).expanduser().resolve()
        root = Path(repo_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        p = Path(str(cwd))
        root = Path(str(repo_path))
    try:
        if p == root or root in p.parents:
            return True
    except RuntimeError:
        pass
    try:
        return _core._git_toplevel_for_path(str(p), git_top_cache) == str(root)
    except Exception:
        return False


# Per-file resume state for incremental codex tail parsing: path -> {offset,
# pos, pending_calls, meta, meta_version}. In-memory only (NOT persisted with
# _conv_meta_cache) — after a restart the first poll does one full parse and
# rebuilds it. Guarded by _conv_meta_cache_lock.
_codex_tail_resume = {}


def _extract_codex_tail_meta(path):
    try:
        st = path.stat()
    except OSError:
        return {}
    mtime = st.st_mtime
    size = st.st_size
    spath = str(path)
    cached = _core._conv_meta_cache.get(spath)
    if (
        cached
        and cached.get("mtime") == mtime
        and cached.get("engine") == "codex"
        and cached.get("meta_version") == _CODEX_META_VERSION
    ):
        return cached

    # Incremental resume. Rollout JSONL is append-only, so rather than
    # re-parsing the whole (often multi-MB) file on every poll — the mtime
    # cache above *always* misses for a live, actively-appending session, which
    # is exactly the session live-activity polls — carry forward the prior
    # parse state and read only the bytes appended since the last call.
    with _core._conv_meta_cache_lock:
        resume = _core._codex_tail_resume.get(spath)
    if (
        resume
        and resume.get("meta_version") == _CODEX_META_VERSION
        and size >= resume.get("offset", 0)
    ):
        meta = resume["meta"]
        pending_calls = resume["pending_calls"]
        pos = resume["pos"]
        start_offset = resume["offset"]
    else:
        meta = {
            "engine": "codex",
            "meta_version": _CODEX_META_VERSION,
            "mtime": mtime,
            "first_message": None,
            "last_meaningful_ts": 0,
            "last_prompt": None,
            "last_assistant_text": None,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "pending_tool_ts": 0,
            "needs_approval": False,
            "needs_approval_message": "",
            "pending_approval_call_id": "",
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_edit_pos": 0,
            "last_commit_pos": 0,
            "last_push_pos": 0,
            "tail_pr_number": None,
            "tail_pr_url": None,
            "tail_branch": None,
            "tail_worktree_path": None,
            "has_external_cd": False,
            "cwd": None,
            "model": None,
            "latest_input_tokens": 0,
            "lifetime_tokens": 0,
            "context_limit": 0,
        }
        pending_calls = {}
        pos = 0
        start_offset = 0
    pr_url_re = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d{1,7})")
    # Binary + readline() so f.tell()/offset accounting stays exact (text-mode
    # iteration buffers ahead and corrupts the resume offset).
    try:
        with open(path, "rb") as f:
            if start_offset:
                f.seek(start_offset)
            while True:
                raw = f.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    # Partial trailing line — writer is mid-append. Leave the
                    # offset before it so the next poll re-reads it complete.
                    break
                start_offset += len(raw)
                pos += 1
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                usage = _core._codex_token_usage_from_event(ev)
                if usage:
                    inp = _core._codex_int(usage.get("input_tokens"))
                    if not inp:
                        # Post-compaction marker: Codex writes a token_count
                        # whose last_token_usage has every PER-TURN field
                        # zeroed and reports the size of the freshly rebuilt
                        # context in total_tokens only. Skipping it left the
                        # context pill on the stale pre-compact number, while
                        # `_extract_codex_usage` read the zero and showed 0.
                        # Both extractors now agree on total_tokens here.
                        inp = _core._codex_int(usage.get("total_tokens"))
                    if inp:
                        meta["latest_input_tokens"] = inp
                    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                    info = payload.get("info") or {}
                    if isinstance(info, dict):
                        total_usage = info.get("total_token_usage")
                        if isinstance(total_usage, dict) and total_usage:
                            reported_total = (
                                _core._codex_int(total_usage.get("total_tokens"))
                                or (
                                    _core._codex_int(total_usage.get("input_tokens"))
                                    + _core._codex_int(total_usage.get("output_tokens"))
                                )
                            )
                            meta["lifetime_tokens"] = max(
                                _core._codex_int(meta.get("lifetime_tokens")),
                                reported_total,
                            )
                        else:
                            meta["lifetime_tokens"] += (
                                _core._codex_int(usage.get("total_tokens"))
                                or (
                                    _core._codex_int(usage.get("input_tokens"))
                                    + _core._codex_int(usage.get("output_tokens"))
                                )
                            )
                        cl = _core._codex_int(info.get("model_context_window"))
                        if cl:
                            meta["context_limit"] = cl
                    continue
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                ts_epoch = _codex_event_epoch(ev)
                ev_type = ev.get("type")
                ptype = payload.get("type")
                if ev_type in ("session_meta", "turn_context"):
                    meta["cwd"] = payload.get("cwd") or meta["cwd"]
                    meta["model"] = payload.get("model") or meta["model"]
                    continue
                if ev_type == "event_msg":
                    if ptype == "user_message":
                        text = _core._strip_ccc_session_state_instruction(
                            payload.get("message") or ""
                        ).strip()
                        if text:
                            meta["first_message"] = meta["first_message"] or text
                            meta["last_prompt"] = text
                        meta["last_event_type"] = "user"
                        meta["pending_tool"] = None
                        meta["pending_file"] = None
                        meta["pending_tool_ts"] = 0
                        meta["needs_approval"] = False
                        meta["needs_approval_message"] = ""
                        meta["pending_approval_call_id"] = ""
                        pending_calls.clear()
                        if ts_epoch:
                            meta["last_meaningful_ts"] = ts_epoch
                    elif ptype == "agent_message":
                        text = (payload.get("message") or "").strip()
                        if text:
                            meta["last_assistant_text"] = text
                            meta.update(_extract_codex_summary_signals(text, pr_url_re))
                        meta["last_event_type"] = "assistant"
                        if ts_epoch:
                            meta["last_meaningful_ts"] = ts_epoch
                    elif ptype == "task_complete":
                        text = (payload.get("last_agent_message") or payload.get("message") or "").strip()
                        if text:
                            meta.update(_extract_codex_summary_signals(text, pr_url_re))
                        meta["last_event_type"] = "result"
                        meta["pending_tool"] = None
                        meta["pending_file"] = None
                        meta["pending_tool_ts"] = 0
                        meta["needs_approval"] = False
                        meta["needs_approval_message"] = ""
                        meta["pending_approval_call_id"] = ""
                        pending_calls.clear()
                        if ts_epoch:
                            meta["last_meaningful_ts"] = ts_epoch
                    continue
                if ev_type != "response_item":
                    continue
                if ptype in ("function_call", "custom_tool_call"):
                    name = payload.get("name") or ""
                    is_custom_tool = ptype == "custom_tool_call"
                    raw_input = payload.get("input") if isinstance(payload.get("input"), str) else ""
                    args = {} if is_custom_tool else _codex_args(payload.get("arguments"))
                    detail = (
                        _codex_custom_tool_detail(raw_input, name)
                        if is_custom_tool else _codex_tool_detail(name, args)
                    )
                    call_id = payload.get("call_id") or ""
                    meta["last_event_type"] = "assistant"
                    if ts_epoch:
                        meta["last_meaningful_ts"] = ts_epoch
                    tool_name = (
                        _codex_custom_tool_kind(raw_input, name)
                        if is_custom_tool else (_codex_tool_name(name) or name)
                    )
                    meta["pending_tool"] = tool_name
                    meta["pending_file"] = (detail[:80] if isinstance(detail, str) else None)
                    meta["pending_tool_ts"] = ts_epoch or meta.get("last_meaningful_ts") or mtime
                    approval_required = _codex_tool_requires_approval(name, args, raw_input)
                    approval_message = _codex_tool_approval_message(args, raw_input)
                    if approval_required:
                        meta["needs_approval"] = True
                        meta["needs_approval_message"] = approval_message
                        meta["pending_approval_call_id"] = call_id
                    if tool_name == "apply_patch":
                        meta["has_edit"] = True
                        meta["last_edit_pos"] = pos
                    cmd = (
                        _codex_custom_tool_command(raw_input)
                        if is_custom_tool else _codex_tool_command(name, args)
                    )
                    tool_workdir = (
                        _codex_custom_tool_workdir(raw_input)
                        if is_custom_tool else _codex_tool_workdir(name, args)
                    ) or meta.get("cwd")
                    if cmd:
                        signals = _codex_command_signals(cmd, base_cwd=tool_workdir)
                        if signals["edit"]:
                            meta["has_edit"] = True
                            meta["last_edit_pos"] = pos
                        if signals["commit"]:
                            meta["has_commit"] = True
                            meta["last_commit_pos"] = pos
                        if signals["push"]:
                            meta["has_push"] = True
                            meta["last_push_pos"] = pos
                        if signals["external_cd"]:
                            meta["has_external_cd"] = True
                        if signals.get("worktree_path"):
                            meta["tail_worktree_path"] = signals["worktree_path"]
                        if signals.get("worktree_branch"):
                            meta["tail_branch"] = signals["worktree_branch"]
                        if call_id:
                            pending_calls[call_id] = {
                                "cmd": cmd,
                                "pr": signals["pr"],
                                "approval": approval_required,
                            }
                    elif call_id:
                        pending_calls[call_id] = {
                            "cmd": "",
                            "pr": False,
                            "approval": approval_required,
                        }
                elif ptype in ("function_call_output", "custom_tool_call_output"):
                    call_id = payload.get("call_id") or ""
                    call = pending_calls.pop(call_id, {})
                    if call:
                        meta["pending_tool"] = None
                        meta["pending_file"] = None
                        meta["pending_tool_ts"] = 0
                        if call.get("approval") and meta.get("pending_approval_call_id") == call_id:
                            meta["needs_approval"] = False
                            meta["needs_approval_message"] = ""
                            meta["pending_approval_call_id"] = ""
                    meta["last_event_type"] = "assistant"
                    if ts_epoch:
                        meta["last_meaningful_ts"] = ts_epoch
                    out = payload.get("output") or ""
                    if call.get("pr") and isinstance(out, str):
                        mp = pr_url_re.search(out)
                        if mp:
                            meta["tail_pr_number"] = int(mp.group(2))
                            meta["tail_pr_url"] = (
                                "https://github.com/" + mp.group(1) + "/pull/" + mp.group(2)
                            )
                else:
                    meta["last_event_type"] = "assistant"
                    if ts_epoch:
                        meta["last_meaningful_ts"] = ts_epoch
            end_offset = start_offset
    except OSError:
        return {}

    meta["mtime"] = mtime
    if not meta.get("last_meaningful_ts"):
        meta["last_meaningful_ts"] = mtime
    with _core._conv_meta_cache_lock:
        _core._conv_meta_cache[spath] = meta
        _core._codex_tail_resume[spath] = {
            "meta_version": _CODEX_META_VERSION,
            "offset": end_offset,
            "pos": pos,
            "pending_calls": pending_calls,
            "meta": meta,
        }
        _core._conv_meta_cache_dirty = True
    return meta


def _codex_stale_tool_threshold_s():
    try:
        threshold = float(os.environ.get("CCC_CODEX_STALE_TOOL_SEC", "900"))
    except (TypeError, ValueError):
        threshold = 900.0
    return max(0.0, threshold)


def _codex_stale_tool_fields(tail, now=None, threshold_s=None):
    threshold = _core._codex_stale_tool_threshold_s() if threshold_s is None else max(0.0, float(threshold_s))
    fields = {
        "stale_tool_call": False,
        "stale_tool_age_s": 0,
        "stale_tool_threshold_s": int(threshold),
    }
    if not tail or not tail.get("pending_tool"):
        return fields
    ts = tail.get("pending_tool_ts") or 0
    if not ts:
        return fields
    try:
        age = max(0.0, float(now if now is not None else time.time()) - float(ts))
    except (TypeError, ValueError):
        return fields
    fields["stale_tool_age_s"] = int(age)
    fields["stale_tool_call"] = bool(threshold > 0 and age >= threshold)
    return fields


def _stale_tool_threshold_s():
    """Stale-tool threshold for Claude/headless sessions (seconds).

    Mirrors the Codex knob but for the headless tool-child path. A tool child
    that has been "running" longer than this with no result is treated as hung,
    so the UI can warn the user (queued input cannot drain while a tool child is
    active). Configurable via ``CCC_STALE_TOOL_SEC`` (default 900s / 15m).
    """
    try:
        threshold = float(os.environ.get("CCC_STALE_TOOL_SEC", "900"))
    except (TypeError, ValueError):
        threshold = 900.0
    return max(0.0, threshold)


def _claude_stale_tool_fields(started_at, in_flight, now=None, threshold_s=None):
    """Stale-tool fields for a Claude headless session's active tool child.

    Codex synthesizes these from its rollout tail; Claude headless sessions have
    no rollout, but ``_spawn_entry_active_tool_child`` gives the real child start
    time (from ``ps`` etime). When an in-flight tool child has aged past the
    threshold it is almost certainly wedged (the real case: ``rg`` blocked on a
    ``.stdin`` FIFO for ~3.7h), and the input queue cannot drain while a child
    is alive — so flag it for the UI.
    """
    threshold = _core._stale_tool_threshold_s() if threshold_s is None else max(0.0, float(threshold_s))
    fields = {
        "stale_tool_call": False,
        "stale_tool_age_s": 0,
        "stale_tool_threshold_s": int(threshold),
    }
    if not in_flight or not started_at:
        return fields
    try:
        age = max(0.0, float(now if now is not None else time.time()) - float(started_at))
    except (TypeError, ValueError):
        return fields
    fields["stale_tool_age_s"] = int(age)
    fields["stale_tool_call"] = bool(threshold > 0 and age >= threshold)
    return fields


def _codex_activity_fields_from_tail(tail, live):
    """Map Codex rollout tail state into the sidecar-shaped UI fields.

    Claude sessions get this from command-center hooks. Codex does not run
    those hooks, so live rows synthesize equivalent activity from the rollout:
    an unfinished tool call names the tool; otherwise a mid-turn user/assistant
    tail shows as generic thinking.
    """
    fields = {
        "sidecar_status": None,
        "sidecar_has_writes": False,
        "sidecar_tool": None,
        "sidecar_file": None,
        "sidecar_ts": 0,
        "sidecar_in_flight": False,
        "question_waiting": False,
        "question_text": "",
        "question_header": "",
        "question_preamble": "",
        "question_options": [],
        "question_option_details": [],
        "needs_approval": bool((tail or {}).get("needs_approval")),
        "needs_approval_message": (tail or {}).get("needs_approval_message") or "",
    }
    if not live or not tail:
        return fields
    if _core._codex_stale_tool_fields(tail).get("stale_tool_call"):
        return fields

    ts = tail.get("last_meaningful_ts") or 0
    pending_tool = tail.get("pending_tool")
    if pending_tool:
        fields.update({
            "sidecar_status": "active",
            "sidecar_tool": pending_tool,
            "sidecar_file": tail.get("pending_file"),
            "sidecar_ts": ts,
            "sidecar_in_flight": True,
        })
        return fields

    if tail.get("last_event_type") in ("user", "assistant"):
        fields.update({
            "sidecar_status": "active",
            "sidecar_tool": "Thinking",
            "sidecar_file": None,
            "sidecar_ts": ts,
            "sidecar_in_flight": True,
        })
    return fields


def _codex_app_activity_superseded_by_tail(app_activity, tail):
    if not app_activity or not app_activity.get("sidecar_status") or not tail:
        return False
    # Recovery describes why the whole turn exists, not merely its latest
    # item. A newer rollout reasoning/tool event must not replace the recovery
    # label with ordinary "Thinking" while the watchdog continuation runs.
    if app_activity.get("sidecar_tool") == "Recovery":
        return False
    try:
        app_ts = float(app_activity.get("sidecar_ts") or 0)
        tail_ts = float(tail.get("last_meaningful_ts") or 0)
        pending_ts = float(tail.get("pending_tool_ts") or 0)
    except (TypeError, ValueError):
        return False
    newest_tail_ts = max(tail_ts, pending_ts)
    if not app_ts or newest_tail_ts <= app_ts + 2:
        return False
    if tail.get("needs_approval"):
        return True
    pending_tool = tail.get("pending_tool")
    if not pending_tool:
        return True
    return pending_tool != app_activity.get("sidecar_tool")


def _codex_fresh_threshold_s():
    try:
        v = float(os.environ.get("CCC_CODEX_FRESH_SEC", "40"))
    except (TypeError, ValueError):
        v = 40.0
    return max(0.0, v)


def _codex_recent_window_s():
    try:
        v = float(os.environ.get("CCC_CODEX_RECENT_SEC", str(24 * 3600)))
    except (TypeError, ValueError):
        v = float(24 * 3600)
    return max(0.0, v)


def _codex_row_state(tail, mtime, now, pool_alive, has_live_proc):
    """Classify one codex session into working / idle / stuck / offline.

    Pure function (no I/O) so it is unit-testable. Caller applies the
    recency gate and resolves pool/liveness/mtime before calling.

    Priority: offline (engine down) > stuck (mid-turn, stale) >
    working (mid-turn, fresh) > idle (turn complete).
    """
    if not tail:
        return None
    if tail.get("needs_approval"):
        return "waiting"
    mid_turn = bool(tail.get("pending_tool")) or (
        tail.get("last_event_type") in ("user", "assistant")
    )
    if not pool_alive and not has_live_proc:
        return "offline"
    if mid_turn:
        try:
            age = max(0.0, float(now) - float(mtime or 0))
        except (TypeError, ValueError):
            age = 0.0
        if age >= _core._codex_stale_tool_threshold_s():
            return "stuck"
        return "working"
    return "idle"


def _codex_pool_alive(now=None):
    """True when a `codex app-server` pool process is running.

    Cached for _ENGINE_LIVE_TTL like the engine-live scan. On any error,
    fall back to the last value (default True) so a transient ps failure
    never flips every codex row to a false 'offline'.
    """
    now = now if now is not None else time.time()
    cached = _core._codex_pool_alive_cache
    if now - cached["ts"] < _core._ENGINE_LIVE_TTL:
        return cached["alive"]
    try:
        alive = any(
            "app-server" in command
            for _pid_s, command in _core._raw_engine_process_commands("codex")
        )
    except Exception:
        return cached["alive"]
    _core._codex_pool_alive_cache["ts"] = now
    _core._codex_pool_alive_cache["alive"] = alive
    return alive


def _codex_state_fields(
    sid,
    now=None,
    note_writer_transition=True,
    rollout_path=None,
    rollout_stat=None,
    rollout_tail=None,
):
    """Resolve {codex_state, codex_fresh} for one codex session id.

    Applies the recency gate (no chip for sessions whose rollout hasn't
    been touched within _codex_recent_window_s). Fails quiet to nulls.
    """
    fields = {"codex_state": None, "codex_fresh": False}
    if not sid:
        return fields
    now = now if now is not None else time.time()
    # Resolve + stat the rollout ONCE (the thread-row lookup behind
    # _resolve_codex_rollout_path is a sqlite query — this function sits on
    # liveness paths, so it must not pay it twice).
    path = Path(rollout_path) if rollout_path is not None else None
    rollout = {}
    mtime = None
    try:
        if path is None:
            path = _core._resolve_codex_rollout_path(sid)
        if path:
            st = rollout_stat if rollout_stat is not None else Path(path).stat()
            mtime = st.st_mtime
            rollout = {
                "path": str(path),
                "size": getattr(st, "st_size", 0),
                "mtime_ns": getattr(st, "st_mtime_ns", int(mtime * 1_000_000_000)),
            }
    except OSError:
        path = None
        rollout = {}
    snap = {}
    try:
        app_state = _core._codex_app_server_thread_state(sid)
    except Exception:
        app_state = {}
    tail = rollout_tail if isinstance(rollout_tail, dict) else {}
    if path and mtime is not None and (now - mtime) <= _core._codex_recent_window_s():
        if rollout_tail is None:
            try:
                tail = _core._extract_codex_tail_meta(path) or {}
            except Exception:
                tail = {}
        if tail.get("needs_approval"):
            fields["codex_state"] = "waiting"
            fields["codex_fresh"] = True
            fields["codex_state_reason"] = tail.get("needs_approval_message") or "Codex is waiting for approval"
            return fields
    approval_item = _codex_app_server_pending_approval_item(app_state)
    if approval_item:
        fields["codex_state"] = "waiting"
        fields["codex_fresh"] = True
        fields["codex_state_reason"] = (
            approval_item.get("approval_message")
            or approval_item.get("detail")
            or "Codex is waiting for approval"
        )
        return fields
    if _codex_app_server_thread_needs_approval(app_state):
        fields["codex_state"] = "waiting"
        fields["codex_fresh"] = True
        fields["codex_state_reason"] = _codex_app_server_thread_approval_message(app_state)
        return fields
    try:
        # Writer attribution (desktop ↔ CCC coordination): reuses the rollout
        # stat above + the TTL-cached desktop-attachment map. Also feeds the
        # durable external-turn transition events.
        snap = _core._codex_thread_writer_snapshot(sid, now, app_state=app_state, rollout=rollout)
        if note_writer_transition:
            _core._codex_note_external_writer_transition(sid, snap)
        if snap.get("writer"):
            fields["codex_writer"] = snap["writer"]
        if snap.get("desktop_attached"):
            fields["codex_desktop_attached"] = True
    except Exception:
        snap = {}
    try:
        if app_state and (app_state.get("active_turn_id") or str(app_state.get("status") or "").lower() == "active"):
            try:
                app_ts = float(app_state.get("last_activity_at") or app_state.get("last_event_at") or 0)
                tail_ts = float((tail or {}).get("last_meaningful_ts") or 0)
            except (TypeError, ValueError):
                app_ts = 0.0
                tail_ts = 0.0
            if not (tail_ts > app_ts + 2 and (tail or {}).get("last_event_type") == "result"):
                fields["codex_state"] = "working"
                fields["codex_fresh"] = True
                return fields
    except Exception:
        pass
    if snap.get("external_active"):
        # A desktop or ownership-unknown turn is in flight: the session is live
        # and busy even though CCC cannot prove that it owns the active turn.
        fields["codex_state"] = "working"
        fields["codex_fresh"] = True
        fields["codex_state_reason"] = (
            "Codex desktop is writing this thread"
            if snap.get("writer") == "desktop"
            else "An active Codex turn is writing this thread"
        )
        return fields
    if not path or mtime is None:
        return fields
    if (now - mtime) > _core._codex_recent_window_s():
        return fields
    try:
        if not tail:
            tail = _core._extract_codex_tail_meta(path) or {}
        pool_alive = _core._codex_pool_alive(now)
        has_live_proc = sid in _core._live_engine_session_ids()
        state = _core._codex_row_state(tail, mtime, now, pool_alive, has_live_proc)
    except Exception:
        return fields
    fields["codex_state"] = state
    if state == "working":
        fields["codex_fresh"] = (now - float(mtime)) < _codex_fresh_threshold_s()
    elif state == "stuck":
        fields["codex_state_reason"] = _codex_stuck_reason(tail, mtime, now)
    return fields


def _codex_stuck_reason(tail, mtime, now):
    """Human-readable 'why is this codex session stuck' string.

    Stuck = mid-turn but the rollout has not advanced past the stale
    threshold. Name the tool it stalled on (if any) and how long it has been
    silent, so the UI can answer 'do we know why it was stuck?'.
    """
    try:
        mins = int(max(0.0, float(now) - float(mtime or 0)) // 60)
    except (TypeError, ValueError):
        mins = 0
    ago = f"{mins}m" if mins else "under a minute"
    pending = (tail or {}).get("pending_tool")
    if pending:
        target = (tail or {}).get("pending_file") or ""
        label = (f"{pending} {target}".strip())
        return f"No output for {ago} while running {label} - the tool call looks hung."
    return f"No output for {ago} after the last message - the turn stalled with no tool running."


_codex_stuck_summary_cache = {"ts": 0.0, "value": None}
_codex_stuck_summary_lock = threading.Lock()


def _codex_stuck_summary_ttl_s():
    try:
        value = float(os.environ.get("CCC_CODEX_STUCK_SUMMARY_TTL_SEC", "60"))
    except (TypeError, ValueError):
        value = 60.0
    return max(1.0, value)


def build_codex_stuck_summary(now=None, force=False):
    """Count recent Codex threads carrying the dashboard's ``Stuck`` label.

    The cheap pure classifier prefilters recent rollout tails before the more
    authoritative app-server/writer check. Results are cached because the
    footer is monitoring a fleet-level heuristic, not a millisecond-precise
    process signal. The read-only confirmation deliberately avoids recording
    writer-transition events: observing the counter must not mutate threads.
    """
    now = float(now if now is not None else time.time())
    cache_now = time.monotonic()
    with _codex_stuck_summary_lock:
        cached = _codex_stuck_summary_cache.get("value")
        cached_at = float(_codex_stuck_summary_cache.get("ts") or 0)
        if (
            not force
            and cached is not None
            and (cache_now - cached_at) < _codex_stuck_summary_ttl_s()
        ):
            return dict(cached)

    rows = _core._codex_fetch_threads()
    pool_alive = _core._codex_pool_alive(now)
    live_ids = _core._live_engine_session_ids()
    recent_sessions = 0
    candidates = 0
    stuck_ids = []
    recent_window = _core._codex_recent_window_s()
    threshold = _core._codex_stale_tool_threshold_s()

    for row in rows:
        sid = row.get("id") or ""
        if not sid:
            continue
        path = _core._codex_rollout_path_from_row(row)
        if not path:
            continue
        try:
            rollout_stat = path.stat()
            mtime = rollout_stat.st_mtime
        except OSError:
            continue
        if max(0.0, now - float(mtime or 0)) > recent_window:
            continue
        recent_sessions += 1
        try:
            tail = _core._extract_codex_tail_meta(path) or {}
            coarse = _core._codex_row_state(tail, mtime, now, pool_alive, sid in live_ids)
        except Exception:
            continue
        if coarse != "stuck":
            continue
        candidates += 1
        confirmed = _core._codex_state_fields(
            sid,
            now,
            note_writer_transition=False,
            rollout_path=path,
            rollout_stat=rollout_stat,
            rollout_tail=tail,
        )
        if confirmed.get("codex_state") == "stuck":
            stuck_ids.append(sid)

    value = {
        "count": len(stuck_ids),
        "session_ids": stuck_ids,
        "candidates": candidates,
        "recent_sessions": recent_sessions,
        "threshold_s": int(threshold),
        "recent_window_s": int(recent_window),
        "as_of": now,
        "heuristic": True,
    }
    with _codex_stuck_summary_lock:
        _codex_stuck_summary_cache["ts"] = cache_now
        _codex_stuck_summary_cache["value"] = dict(value)
    return value
