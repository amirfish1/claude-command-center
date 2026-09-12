"""Extracted from server.py (originally lines 41648-41847).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Persistent spawn-PID registry
# ---------------------------------------------------------------------------
# When the server restarts, the in-memory `_spawned_sessions` dict is wiped but
# the underlying `claude -p` children may still be running, orphaned. The
# registry (`spawned-pids.json`) lets us re-discover them on the next boot so
# the dashboard's inject path doesn't bottom out with "unknown pid".
#
# We never kill orphans — destructive action without an explicit ask is
# off-limits per CLAUDE.md. The sweep just rebuilds `_spawned_sessions` from
# verified-alive entries and prunes dead/reused PIDs from the file so it
# doesn't grow forever.
#
# The dashboard and persistent worker both update this file. Every
# read/modify/write transaction uses a shared flock; atomic replacement gives
# unlocked readers either the complete old snapshot or the complete new one.

class _ReattachedProc:
    """Stand-in for a real subprocess.Popen for processes we recovered from
    the registry on startup. We don't own their stdin/stdout (those died with
    the previous server), so writes are no-ops that report failure. `.poll()`
    returns None while the PID is alive and a sentinel exit code once it isn't,
    which is what callers (`list_spawned_sessions`, `find_all_sessions`) check.
    """

    def __init__(self, pid):
        self.pid = pid
        self.stdin = None
        self._cached_exit = None

    def poll(self):
        if self._cached_exit is not None:
            return self._cached_exit
        try:
            waited_pid, status = os.waitpid(self.pid, os.WNOHANG)
            if waited_pid == self.pid:
                try:
                    self._cached_exit = os.waitstatus_to_exitcode(status)
                except (AttributeError, ValueError):
                    self._cached_exit = -1
                return self._cached_exit
        except ChildProcessError:
            pass
        except OSError:
            pass
        try:
            os.kill(self.pid, 0)
            if _core._pid_is_zombie(self.pid):
                self._cached_exit = -1
                return self._cached_exit
            return None
        except ProcessLookupError:
            self._cached_exit = -1
            return -1
        except PermissionError:
            # Process exists but is owned by another user; treat as alive.
            return None


def _load_spawn_registry():
    """Read the on-disk spawn registry. Tolerant of missing/malformed files
    — both yield an empty list so a corrupted registry can never block boot."""
    if not _core.SPAWNED_PIDS_FILE.exists():
        return []
    try:
        data = json.loads(_core.SPAWNED_PIDS_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [spawn-registry] ignoring malformed registry ({e})")
        return []
    if not isinstance(data, list):
        print(f"  [spawn-registry] ignoring registry with unexpected shape (not a list)")
        return []
    for entry in data:
        if isinstance(entry, dict) and not str(entry.get("spawned_via") or "").strip():
            name = str(entry.get("name") or entry.get("command_summary") or "").lower()
            if entry.get("parent_session_id"):
                entry["spawned_via"] = "subagent"
            elif name.startswith("lane-w") or name.startswith("lane-e") or "[watchtower]" in name or "[wt]" in name:
                entry["spawned_via"] = "watchtower"
            elif name.startswith("resume-"):
                entry["spawned_via"] = "resumed"
            else:
                entry["spawned_via"] = "ui"
    return data


_SPAWN_REGISTRY_THREAD_LOCK = threading.RLock()

# Read-only, mtime-cached view of the shared on-disk registry for lookups
# that must see spawns created by the OTHER process (worker vs dashboard).
_DISK_SPAWN_CACHE = {"mtime": None, "entries": []}


def _disk_spawn_entries_cached():
    try:
        mtime = _core.SPAWNED_PIDS_FILE.stat().st_mtime
    except OSError:
        return []
    if _DISK_SPAWN_CACHE["mtime"] != mtime:
        _DISK_SPAWN_CACHE["entries"] = _core._load_spawn_registry()
        _DISK_SPAWN_CACHE["mtime"] = mtime
    return _DISK_SPAWN_CACHE["entries"]


def _disk_spawn_entry_for_session(session_id):
    """Live (pid alive) on-disk registry entry for `session_id`, or None.
    Complements `_find_live_spawn_entry_for_session`, which only knows the
    spawns THIS process created or reattached at boot."""
    sid = (session_id or "").strip()
    if not sid:
        return None
    for entry in _disk_spawn_entries_cached():
        if not isinstance(entry, dict):
            continue
        if sid not in (entry.get("session_id"), entry.get("resumed_sid")):
            continue
        pid = entry.get("pid")
        try:
            if pid and _core._is_pid_alive(int(pid)):
                return entry
        except (TypeError, ValueError):
            continue
    return None


def _spawn_registry_lock_path():
    return _core.SPAWNED_PIDS_FILE.with_suffix(".lock")


@contextlib.contextmanager
def _spawn_registry_exclusive_lock():
    """Serialize registry mutations across dashboard and worker processes."""
    with _SPAWN_REGISTRY_THREAD_LOCK:
        lock_path = _spawn_registry_lock_path()
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_fh = lock_path.open("a+")
        except OSError as exc:
            # Preserve the registry's historical best-effort behavior on a
            # read-only state directory. The in-process lock still prevents
            # local thread races; the eventual save logs its own failure.
            print(f"  [spawn-registry] could not lock {lock_path} ({exc})")
            yield
            return
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            finally:
                lock_fh.close()


_codex_parent_links_cache = {"data": None}
_codex_parent_links_lock = threading.Lock()


def _load_codex_parent_links():
    """Return {codex_thread_id: parent_session_id} from the durable link file.

    In-memory cache; refreshed when the file changes. Tolerant of missing or
    malformed files — both yield an empty dict. (CCC-465)
    """
    with _codex_parent_links_lock:
        cached = _codex_parent_links_cache["data"]
        if cached is not None:
            return cached
    try:
        data = json.loads(_core.CODEX_PARENT_LINKS_FILE.read_text()) if _core.CODEX_PARENT_LINKS_FILE.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    with _codex_parent_links_lock:
        _codex_parent_links_cache["data"] = data
    return data


def _persist_codex_parent_link(thread_id, parent_session_id):
    """Write thread_id -> parent_session_id to the durable link file.

    Idempotent; first write wins. Called when the Codex thread_id is first
    discovered for a CCC-spawned session that has a parent_session_id. (CCC-465)
    """
    if not thread_id or not parent_session_id:
        return
    with _codex_parent_links_lock:
        try:
            existing = json.loads(_core.CODEX_PARENT_LINKS_FILE.read_text()) if _core.CODEX_PARENT_LINKS_FILE.exists() else {}
        except (OSError, json.JSONDecodeError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        if thread_id in existing:
            return
        existing[thread_id] = parent_session_id
        try:
            tmp = _core.CODEX_PARENT_LINKS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(existing, indent=2))
            os.replace(tmp, _core.CODEX_PARENT_LINKS_FILE)
            _codex_parent_links_cache["data"] = existing
        except OSError:
            pass
    # Feed the unified session graph.
    _core._session_graph_add_edge(
        parent_session_id, thread_id,
        source="codex-parent-link", engine="codex", resumable=True,
    )




# ---------------------------------------------------------------------------
# Recent-spawn feed (dashboard "a session is spawning" placeholders)
# ---------------------------------------------------------------------------
# A spawn started from the browser gets an instant client-side placeholder row
# (insertPendingSpawnCard in static/app.js). A spawn started anywhere else —
# `ccc spawn`, an agent POSTing /api/sessions/spawn, a WatchTower lane — had no
# such client, so the new session stayed invisible until its transcript
# materialized and the next full archive refresh picked it up (up to a minute,
# often more for Codex, which writes nothing for several seconds).
#
# This feed closes that gap: the registry already records every spawn from both
# the dashboard AND the worker, so the dashboard can render the same placeholder
# for a spawn it did not initiate. Read from DISK (mtime-cached) on purpose —
# the in-memory `_spawned_sessions` list only ever holds this process's own
# spawns, which is exactly the blind spot.
#
# Perf: no subprocess, no transcript read, no control-plane round trip. The only
# per-entry work is one os.kill(pid, 0) liveness check, and only for entries
# inside the (small) recency window — so cost scales with spawns-per-minute,
# not with the size of the registry or the session corpus.

_SPAWN_FEED_WINDOW_S = 300.0
_SPAWN_FEED_FIELDS = (
    "session_id", "resumed_sid", "name", "engine", "model", "reasoning_effort",
    "cwd", "repo_path", "parent_session_id", "command_summary", "spawned_via",
    "log",
)


def _spawn_feed_epoch(entry):
    """Epoch seconds for a registry entry's `spawned_at` stamp, or None.

    `_record_spawn_to_registry` writes `time.strftime("%Y%m%dT%H%M%S")` —
    local time, no offset — so parse it the same way it was produced.
    """
    raw = str((entry or {}).get("spawned_at") or "").strip()
    if not raw:
        return None
    try:
        return time.mktime(time.strptime(raw, "%Y%m%dT%H%M%S"))
    except (ValueError, OverflowError):
        return None


def _spawn_feed_recent(window_s=_SPAWN_FEED_WINDOW_S, now=None):
    """Spawns registered within the last `window_s` seconds, newest first.

    Shape mirrors the fields `/api/sessions/spawned` returns for a registry
    row so the frontend can reuse the same placeholder-card builder.
    """
    now = time.time() if now is None else now
    out = []
    for entry in _disk_spawn_entries_cached():
        if not isinstance(entry, dict) or entry.get("prewarm"):
            continue
        started = _spawn_feed_epoch(entry)
        if started is None or (now - started) > window_s or (started - now) > 60:
            continue
        pid = entry.get("pid")
        alive = False
        try:
            alive = bool(pid) and _core._is_pid_alive(int(pid))
        except (TypeError, ValueError):
            alive = False
        row = {k: entry.get(k) or "" for k in _SPAWN_FEED_FIELDS}
        row["pid"] = pid
        row["spawn_id"] = str(entry.get("spawn_id") or pid or "")
        row["spawned_at"] = entry.get("spawned_at") or ""
        row["spawned_at_epoch"] = started
        row["age_s"] = max(0.0, now - started)
        row["alive"] = alive
        out.append(row)
    out.sort(key=lambda r: r.get("spawned_at_epoch") or 0, reverse=True)
    return out


def _spawn_registry_change_token():
    """Cheap "did anything spawn?" signal for the session-status poll.

    Counts the on-disk registry, not just this process's in-memory list.
    A spawn created by the WORKER (which owns engine execution) never lands
    in the dashboard's `_spawned_sessions`, so a count taken from that alone
    could not grow — the client's "refresh the conv list NOW" trigger was
    dead for every worker-owned spawn, which is most of them.
    """
    try:
        disk = len(_disk_spawn_entries_cached())
    except Exception:
        disk = 0
    try:
        mem = len(_core._spawned_sessions)
    except Exception:
        mem = 0
    return disk + mem
