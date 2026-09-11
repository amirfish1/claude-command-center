"""Extracted from server.py (originally lines 44788-45812).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import federation
import json
import os
import re
import stat
import threading
import time
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Sidecar helpers
# Each group chat .md has a .json sidecar at the same basename storing
# session_ids/topic/mode/name_map plus lifecycle fields:
#   - started_at: unix ts the chat was first registered (set at creation)
#   - closed_at: unix ts the watcher dropped it (idle timeout or done marker);
#     None while still active. Falls back to .md mtime if the watcher missed it.
#   - archived: explicit "stop showing in In Group Chat" flag (default False).
#   - archived_at: unix ts the user pressed Archive (None if never archived).
#   - last_reminder_key: latest real chat post that already received a reminder.
# Both helpers are best-effort: missing files / corrupt JSON return {} rather
# than raising, so a hand-edited or partially-written sidecar can't take the
# whole list endpoint down.
# ---------------------------------------------------------------------------

def _group_chat_sidecar_path(chat_path: str) -> str:
    """Return the .json sidecar path for a chat .md (or any path)."""
    if chat_path.endswith(".md"):
        return chat_path[:-3] + ".json"
    if chat_path.endswith(".json"):
        return chat_path
    return chat_path + ".json"


def _load_group_chat_sidecar(chat_path: str) -> dict:
    """Load the sidecar JSON for a group chat. Returns {} on any failure."""
    sidecar = _group_chat_sidecar_path(chat_path)
    try:
        with open(sidecar, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _update_group_chat_sidecar(chat_path: str, **fields) -> bool:
    """Merge `fields` into the sidecar JSON. Returns True on success.

    Reads the existing sidecar, applies the merge, writes back atomically.
    Silent on missing-file (creates a fresh sidecar) so the caller doesn't
    need to special-case "this chat predates the sidecar fields" — but a
    sidecar is only created if at least one valid field is supplied.
    """
    if not fields:
        return False
    sidecar = _group_chat_sidecar_path(chat_path)
    data = _core._load_group_chat_sidecar(chat_path)
    data.update(fields)
    try:
        with open(sidecar, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return True
    except OSError:
        return False


def _valid_group_chat_uuid(value: str) -> bool:
    """Return True when value is a canonical UUID string."""
    try:
        return str(uuid.UUID(str(value or "").strip())) == str(value or "").strip().lower()
    except (ValueError, AttributeError, TypeError):
        return False


def _ensure_group_chat_uuid(chat_path: str, meta: "dict | None" = None) -> str:
    """Return a stable UUID for a group chat, backfilling old sidecars.

    Early group-chat sidecars were identified only by their markdown path.
    Backfilling a UUID lets the UI key rows by identity even when the topic
    changes or two chats share the same title.
    """
    data = meta if isinstance(meta, dict) else _core._load_group_chat_sidecar(chat_path)
    existing = str((data or {}).get("uuid") or (data or {}).get("id") or "").strip().lower()
    if _valid_group_chat_uuid(existing):
        if (data or {}).get("uuid") != existing:
            _core._update_group_chat_sidecar(chat_path, uuid=existing)
        return existing
    generated = str(uuid.uuid4())
    _core._update_group_chat_sidecar(chat_path, uuid=generated)
    return generated


def _group_chat_include_human(meta: dict, header_part: str = "") -> bool:
    """Infer whether the human is a participant for legacy sidecars."""
    if isinstance(meta, dict) and "include_human" in meta:
        return bool(meta.get("include_human"))
    m = re.search(r"^\*\*Participants:\*\*(.*)$", header_part or "", re.MULTILINE)
    if m and re.search(r"\bhuman\b", m.group(1), re.IGNORECASE):
        return True
    return False


def _group_chat_participants_str(meta: dict, header_part: str = "") -> str:
    name_map = (meta or {}).get("name_map") or {}
    session_ids = (meta or {}).get("session_ids") or []
    names = [_core._group_chat_participant_label(sid, name_map, session_ids) for sid in session_ids]
    if _group_chat_include_human(meta or {}, header_part):
        names.append("human")
    return ", ".join(f"`{n}`" for n in names) or "`human`"


def _group_chat_message_count(md_path: str) -> int:
    """Cheap message count: lines starting with '## '. Best-effort only."""
    try:
        with open(md_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return 0
    return sum(1 for line in content.splitlines() if line.startswith("## "))


_active_coordinations: dict = {}          # chat_path → {mtime, last_nudge, last_activity}
_coord_lock = threading.Lock()

_COORD_NUDGE_INTERVAL   = 60    # seconds between nudges for the same chat
_COORD_POLL_INTERVAL    = 30    # watcher thread sleep interval
_COORD_DEATH_TIMEOUT    = 45 * 60  # 45 min with no file change → drop


def _register_coordination(chat_path: str) -> None:
    """Add a newly-started (or recovered) coordination to the watcher.

    If the chat is already registered, just refresh mtime + last_activity;
    do NOT reset last_nudge. Previously this function clobbered the whole
    entry (last_nudge → 0), which opened a race window during clear /
    add-participant flows: between the register call and the explicit
    nudge that follows, the background watcher could tick, see file
    change + last_nudge=0 (debounce passed), and fire its own competing
    nudge — producing two `pinged ...` log lines at the same second.
    """
    # Disabled chats stay out of the watcher entirely — even if a post or
    # add-participant flow calls this, the user's "disable" knob wins.
    if _group_chat_is_paused(chat_path):
        return
    try:
        mtime = os.stat(chat_path).st_mtime
    except OSError:
        mtime = time.time()
    with _coord_lock:
        existing = _active_coordinations.get(chat_path)
        now = time.time()
        if existing is None:
            _active_coordinations[chat_path] = {
                "mtime": mtime,
                "last_nudge": 0.0,
                "last_activity": now,
            }
        else:
            existing["mtime"] = mtime
            existing["last_activity"] = now


def _is_coord_done(chat_path: str) -> bool:
    """Return True if the chat file signals completion (DONE state)."""
    try:
        with open(chat_path, "r", encoding="utf-8") as fh:
            tail = fh.read()[-2000:]   # only check the tail
        return "we're done" in tail.lower() or "✅ done" in tail and "step" not in tail.lower().split("✅ done")[-1][:100]
    except OSError:
        return False


def _coordination_watcher() -> None:
    """Daemon thread: poll all tracked coordinations, nudge on change, drop on death."""
    while True:
        time.sleep(_COORD_POLL_INTERVAL)
        now = time.time()
        with _coord_lock:
            paths = list(_active_coordinations.keys())

        for path in paths:
            try:
                _core._group_chat_update_header_if_changed(path)
            except Exception:
                pass
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                # File deleted — drop it. Can't write closed_at because the .md
                # is gone; the sidecar may also be gone. The list endpoint
                # falls back to .md mtime when closed_at is missing, but in
                # this case the sidecar will be filtered out anyway since the
                # .md is missing too.
                with _coord_lock:
                    _active_coordinations.pop(path, None)
                continue

            # Atomic check-and-claim of the nudge slot: hold _coord_lock
            # across the read of last_nudge AND the write that claims it.
            # Without this, two watcher ticks (or any other concurrent
            # caller) could both read the same old last_nudge, both decide
            # the debounce window had passed, and both fire — producing
            # duplicate `pinged …` log lines at the exact same second.
            fire_nudge = False
            should_drop = False
            with _coord_lock:
                entry = _active_coordinations.get(path)
                if entry is None:
                    continue
                changed = mtime != entry["mtime"]
                idle_seconds = now - entry["last_activity"]
                if changed:
                    entry["mtime"] = mtime
                    entry["last_activity"] = now
                if idle_seconds > _COORD_DEATH_TIMEOUT or _is_coord_done(path):
                    should_drop = True
                elif changed and (now - entry["last_nudge"]) > _COORD_NUDGE_INTERVAL:
                    entry["last_nudge"] = now
                    fire_nudge = True

            if should_drop:
                with _coord_lock:
                    _active_coordinations.pop(path, None)
                try:
                    _core._update_group_chat_sidecar(path, closed_at=time.time())
                except Exception:
                    pass
                continue

            if fire_nudge:
                try:
                    _core._group_chat_nudge(path)
                except Exception:
                    pass
                # Defensive baseline bump: re-stat the file after the
                # nudge wrote its `pinged` log and align entry["mtime"]
                # to that. Without this, the next watcher tick can see
                # the post-nudge mtime as a "change" relative to the
                # pre-nudge baseline and re-fire — exactly the loop we
                # claimed to have stopped via _group_chat_log_system's
                # in-line bump. Belt + suspenders.
                try:
                    post_mtime = os.stat(path).st_mtime
                    with _coord_lock:
                        e2 = _active_coordinations.get(path)
                        if e2 is not None:
                            e2["mtime"] = post_mtime
                except OSError:
                    pass


def _start_coordination_watcher() -> None:
    """Recover any in-progress coordinations from disk and start the watcher thread.

    Skip chats whose sidecar has `archived: true` — those have been
    explicitly retired by the user and must not be re-registered. Without
    this filter, a server restart silently un-archives chats from the
    watcher's perspective and resumes nudging participants of chats the
    user thought were closed for good.

    Also skip chats with `closed_at` set: the watcher already dropped them on
    an idle timeout or a done marker, so they are no longer being coordinated.
    Re-registering them on boot would resurrect a finished chat as "active"
    and silently resume nudging (and token spend) — the surprising "the group
    chat started orchestrating just because I reloaded the server" behaviour.
    A human post clears closed_at and re-registers, so genuinely-active chats
    (closed_at is None) still resume normally.

    CCC_CHAT_ORCHESTRATOR=wt hands auto-nudging to the WatchTower daemon
    (wt's chats.nudge_tick runs against the same ~/.claude/group-chats files
    with the same last_reminder_key dedup), so this watcher must not start:
    two orchestrators on one chat means racing nudges and double token spend.
    Everything else about group chats in CCC (create/post/read UI, manual
    Nudge button, the synchronous check-in on create/add) stays active; only
    the background auto-nudge loop is delegated.
    """
    if os.environ.get("CCC_CHAT_ORCHESTRATOR", "").strip().lower() == "wt":
        print("[coord] CCC_CHAT_ORCHESTRATOR=wt: auto-nudging delegated to WatchTower daemon; coordination watcher not started")
        return

    group_chats_dir = os.path.expanduser("~/.claude/group-chats")
    cutoff = time.time() - _COORD_DEATH_TIMEOUT
    try:
        for fname in os.listdir(group_chats_dir):
            if not fname.endswith(".json"):
                continue
            sidecar = os.path.join(group_chats_dir, fname)
            md_path = sidecar[:-5] + ".md"
            try:
                if os.stat(md_path).st_mtime < cutoff:
                    continue   # too old
                meta = _core._load_group_chat_sidecar(md_path)
                if meta.get("archived"):
                    continue   # explicitly retired — don't resurrect
                if meta.get("closed_at"):
                    continue   # already idled/done — don't resume on boot
                _core._register_coordination(md_path)
            except OSError:
                continue
    except OSError:
        pass

    t = threading.Thread(target=_coordination_watcher, daemon=True, name="coord-watcher")
    t.start()


@_core._ttl_memo_keyed(10.0)
def _group_chat_participant_meta(session_id: str) -> dict:
    """Cheap-ish status snapshot for one participant of a group chat.

    Returns {last_activity, is_live, wip, pending_tool, sidecar_status,
    needs_approval}. Used by the sidebar's indented participants list to
    show whether each session is active, what tool is running, etc. —
    the same chips the main conversation list uses, scoped to one row.

    Memoised per session_id for 10 s (_ttl_memo_keyed): session_live_status
    forks ps (up to 20 times via _proc_ancestor_terminal) on each call, and
    this function is invoked per participant on every group-chat read cache
    miss (~every 3 s). 10 s liveness staleness is imperceptible in the chat
    reader and eliminates the CPU spike on group-chat creation.
    """
    meta = {
        "last_activity": 0,
        "is_live": False,
        "wip": False,
        "pending_tool": None,
        "sidecar_status": None,
        "needs_approval": False,
        "question_waiting": False,
    }
    if not session_id:
        return meta
    try:
        cwd = _core.find_session_cwd(session_id)
    except Exception:
        cwd = None
    # Live status (cheap: ps grep + sidecar file presence).
    try:
        status = _core.session_live_status(session_id, cwd) or {}
    except Exception:
        status = {}
    meta["is_live"] = bool(status.get("live"))
    # Transcript mtime — best proxy for "last activity" without
    # jsonl-scanning the full transcript.
    try:
        if cwd:
            jsonl = _core._canonical_conversation_path(cwd, session_id)
            if jsonl and jsonl.exists():
                meta["last_activity"] = jsonl.stat().st_mtime
    except (OSError, ValueError):
        pass
    # Sidecar fields — only meaningful when live.
    if meta["is_live"]:
        sc = _core._read_sidecar_state(session_id) or {}
        inflight = _core._live_in_flight_or_none(session_id, _core._read_in_flight_state(session_id)) or {}
        notif = _core._read_notification_state(session_id) or {}
        meta["sidecar_status"] = sc.get("status") if sc else None
        meta["pending_tool"] = (inflight or sc).get("tool")
        meta["question_waiting"] = bool(
            inflight and inflight.get("tool") == "AskUserQuestion"
        )
        meta["wip"] = bool(
            sc and sc.get("status") == "active"
            and (inflight or sc.get("tool"))
        )
        meta["needs_approval"] = _core._notification_is_blocking(notif)
    return meta


def _group_chat_compute_waiting(real_path: str, session_ids: list, name_map: dict) -> dict:
    """Inspect the chat tail and return who we're 'waiting on' for the row
    summary. Mirrors the nudge-targeting logic so the sidebar's hint
    matches what the watcher would actually do on the next tick.

    Returns {last_author_hash, last_author_is_human, waiting_on_hashes}.
    waiting_on_hashes is the list of 8-char prefixes that would be pinged
    if a nudge fired right now.
    """
    out = {"last_author_hash": None, "last_author_is_human": False, "waiting_on_hashes": []}
    try:
        with open(real_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return out
    selection = _core._group_chat_auto_nudge_selection(content, session_ids, name_map)
    if selection.get("skipped") == "no recent author":
        return out
    out["last_author_hash"] = selection.get("last_author_hash")
    out["last_author_is_human"] = bool(selection.get("last_author_is_human"))
    out["waiting_on_hashes"] = [sid[:8].lower() for sid in selection.get("targets") or []]
    return out


GROUP_CHAT_ACTIVE_WINDOW_S = 15 * 60
# The All sidebar offers a seven-day window, so retain lightweight summaries
# for that entire period. These rows read only sidecar metadata and file mtimes
# (never messages or participant state), keeping the background poll cheap.
GROUP_CHAT_SIDEBAR_WINDOW_S = 7 * 24 * 60 * 60


def group_chat_activity_state(meta: dict, now: float | None = None) -> str:
    """Return the durable, user-facing activity state for one group chat."""
    now = time.time() if now is None else now
    if meta.get("archived"):
        return "archived"
    if meta.get("paused"):
        return "paused"
    if meta.get("closed_at"):
        return "closed"
    last_activity = max(
        float(meta.get("created_at") or meta.get("started_at") or 0),
        float(meta.get("last_message_at") or 0),
        float(meta.get("participant_changed_at") or 0),
    )
    if last_activity and now - last_activity < GROUP_CHAT_ACTIVE_WINDOW_S:
        return "active"
    return "inactive"


def _list_active_group_chat_summaries(now: float | None = None) -> list:
    """Return recent sidebar chat rows without reading messages or participants."""
    now = time.time() if now is None else now
    group_chats_dir = os.path.expanduser("~/.claude/group-chats")
    try:
        filenames = os.listdir(group_chats_dir)
    except OSError:
        return []
    summaries = []
    for filename in filenames:
        if not filename.endswith(".json"):
            continue
        sidecar_path = os.path.join(group_chats_dir, filename)
        try:
            with open(sidecar_path, encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        md_path = sidecar_path[:-5] + ".md"
        try:
            last_mtime = os.stat(md_path).st_mtime
        except OSError:
            continue
        last_activity = max(
            float(meta.get("created_at") or meta.get("started_at") or 0),
            float(meta.get("last_message_at") or 0),
            float(meta.get("participant_changed_at") or 0),
        )
        chat_state = _core.group_chat_activity_state(meta, now)
        if chat_state == "archived":
            continue
        if chat_state != "active" and (not last_activity or now - last_activity >= GROUP_CHAT_SIDEBAR_WINDOW_S):
            continue
        # CCC-508: id/uuid/path are cheap (already-loaded meta + a stable
        # sidecar field), unlike the participant-probing/message-reading this
        # summary deliberately skips. Omitting them left every sidebar
        # "In Group Chat" row with an empty data-gc-path/data-gc-id, so its
        # click handler's `if (path || chatId)` guard silently no-opened —
        # the row looked clickable but never actually opened the reader.
        chat_uuid = _ensure_group_chat_uuid(md_path, meta)
        summaries.append({
            "id": chat_uuid,
            "uuid": chat_uuid,
            "path": md_path,
            "path_tilde": "~/.claude/group-chats/" + os.path.basename(md_path),
            "topic": meta.get("topic", ""),
            "state": chat_state,
            # Sidebar consumers share the full-listing contract and use
            # `status` to count/render active chats. Keep `state` for
            # compatibility while exposing the same active state under the
            # established field name.
            "status": chat_state if chat_state in {"active", "paused"} else "closed",
            "session_ids": meta.get("session_ids") or [],
            "name_map": meta.get("name_map") or {},
            # Current-session filtering and ordering use these timestamps.
            # They are available from the sidecar/stat call, so including them
            # keeps an active empty chat visible without reading its messages.
            "started_at": meta.get("started_at") or meta.get("created_at") or 0,
            "last_mtime": last_mtime,
            "last_activity": last_activity or last_mtime,
        })
    return summaries


def _list_group_chats(include_archived: bool = False, only_archived: bool = False) -> list:
    """Scan ~/.claude/group-chats/ and build a list of chat entries.

    Each entry: {id, uuid, path, path_tilde, topic, mode, session_ids, status,
    started_at, closed_at, archived_at, last_mtime, last_activity,
    orchestrator_timer_active, orchestrator_last_trigger_at, message_count}.
    Status is one of "active" / "closed" / "archived":
      - active = .md exists AND chat is in _active_coordinations dict.
      - closed = sidecar present, archived flag falsy, NOT in _active_coordinations.
      - archived = sidecar's archived flag is true.
    Sorted reverse-chronologically by mtime.
    """
    group_chats_dir = os.path.expanduser("~/.claude/group-chats")
    try:
        fnames = os.listdir(group_chats_dir)
    except OSError:
        return []
    with _coord_lock:
        active_paths = set(_active_coordinations.keys())
        active_meta = {p: dict(e) for p, e in _active_coordinations.items()}

    out = []
    for fname in fnames:
        if not fname.endswith(".json"):
            continue
        sidecar = os.path.join(group_chats_dir, fname)
        md_path = sidecar[:-5] + ".md"
        try:
            stat = os.stat(md_path)
        except OSError:
            continue   # .md missing — skip stale sidecars
        meta = _core._load_group_chat_sidecar(md_path)
        is_archived = bool(meta.get("archived"))
        if only_archived and not is_archived:
            continue
        if not include_archived and is_archived:
            continue
        is_paused = bool(meta.get("paused"))
        if is_archived:
            status = "archived"
        elif is_paused:
            # User-disabled: inert, not in the watcher, but distinct from a
            # naturally-closed chat so the UI can offer "Enable".
            status = "paused"
        elif md_path in active_paths:
            status = "active"
            try:
                _core._group_chat_update_header_if_changed(md_path)
                stat = os.stat(md_path)
            except Exception:
                pass
        else:
            status = "closed"
        # Closed_at fallback: if the watcher was bypassed (server restarted
        # while idle, or a hand-edited sidecar), we still want the UI to
        # show "closed Xh ago" — fall back to the .md mtime so the row
        # isn't stuck at "just now" forever.
        closed_at = meta.get("closed_at")
        if status == "closed" and not closed_at:
            closed_at = stat.st_mtime
        active_entry = active_meta.get(md_path) or {}
        last_activity = active_entry.get("last_activity") or stat.st_mtime
        last_nudge_at = active_entry.get("last_nudge") or 0
        last_reminder_at = meta.get("last_reminder_at") or 0
        try:
            orchestrator_last_trigger_at = max(float(last_nudge_at or 0), float(last_reminder_at or 0))
        except (TypeError, ValueError):
            orchestrator_last_trigger_at = last_nudge_at or last_reminder_at or 0
        sids = meta.get("session_ids") or []
        nm = _core._group_chat_enrich_name_map(md_path, meta)
        chat_uuid = _ensure_group_chat_uuid(md_path, meta)
        # Archived chats are history: no session is live and nothing is
        # "waiting", so skip the per-participant liveness probes (ps/lsof +
        # sidecar reads) and the chat-tail scan. The archive view lists ALL
        # archived chats, and doing the live work for each (53 probes across
        # 28 chats here) was the entire ~12s archive-load cost.
        if is_archived:
            participant_meta = {}
            waiting = {"last_author_hash": None, "last_author_is_human": False,
                       "waiting_on_hashes": []}
        else:
            # Per-participant status snapshot (live, last activity, WIP).
            # Keyed by full session_id so the UI can match it against the
            # name_map / participants list it already renders.
            participant_meta = {sid: _core._group_chat_participant_meta(sid) for sid in sids}
            # "Who are we waiting for" hint for the chat row, mirroring the
            # nudge-targeting logic so the sidebar's summary matches what
            # the watcher would actually do.
            waiting = _group_chat_compute_waiting(md_path, sids, nm)
        out.append({
            "id": chat_uuid,
            "uuid": chat_uuid,
            "host_node": meta.get("host_node") or federation.node_id(),
            "path": md_path,
            "path_tilde": "~/.claude/group-chats/" + os.path.basename(md_path),
            "topic": meta.get("topic", ""),
            "mode": meta.get("mode", "topic"),
            "session_ids": sids,
            # name_map (session_id → display_name) lets the UI render
            # the participant list under each chat row without an extra
            # round-trip per session.
            "name_map": nm,
            "status": status,
            "paused": is_paused,
            "paused_at": meta.get("paused_at"),
            "started_at": meta.get("started_at"),
            "closed_at": closed_at,
            "archived_at": meta.get("archived_at"),
            "trashed": bool(meta.get("trashed")),
            "last_mtime": stat.st_mtime,
            # Distinct from last_mtime: the last time a real message (human
            # or agent post) landed, not counting administrative writes like
            # pause/resume/nudge-log system lines. Falls back to last_mtime
            # for chats that predate this field.
            "last_message_at": meta.get("last_message_at") or stat.st_mtime,
            "last_activity": last_activity,
            "orchestrator_timer_active": status == "active",
            "orchestrator_last_nudge_at": last_nudge_at,
            "orchestrator_last_trigger_at": orchestrator_last_trigger_at,
            "last_reminder_at": last_reminder_at,
            "last_reminder_targets": meta.get("last_reminder_targets") or [],
            "message_count": _core._group_chat_message_count(md_path),
            "participant_meta": participant_meta,
            "last_author_hash": waiting["last_author_hash"],
            "last_author_is_human": waiting["last_author_is_human"],
            "waiting_on_hashes": waiting["waiting_on_hashes"],
            "lane": meta.get("lane"),
            "keywords": meta.get("keywords") or [],
        })
    out.sort(key=lambda c: c["last_mtime"], reverse=True)
    return out


def _gc_touches_repo(chat: dict, repo_path_canon: str) -> bool:
    """True if any participant session's cwd resolves into repo_path_canon."""
    if not repo_path_canon:
        return False
    for sid in chat.get("session_ids") or []:
        cwd = _core.find_session_cwd(sid)
        if not cwd:
            continue
        try:
            cwd_canon = str(Path(cwd).expanduser().resolve())
        except (OSError, ValueError, RuntimeError):
            continue
        if cwd_canon == repo_path_canon:
            return True
        if cwd_canon.startswith(repo_path_canon + os.sep):
            return True
    return False


def _resolve_group_chat_path(raw: str) -> str:
    """Canonicalize a chat path (accepts either absolute or ~-prefixed).
    Returns the realpath if it lies under ~/.claude/group-chats/, else "".
    """
    group_chats_dir = os.path.realpath(os.path.expanduser("~/.claude/group-chats"))
    try:
        real_path = os.path.realpath(os.path.expanduser(str(raw or "")))
    except Exception:
        return ""
    if not real_path.startswith(group_chats_dir + os.sep):
        return ""
    return real_path


def _resolve_group_chat_ref(raw_path: str = "", raw_uuid: str = "") -> str:
    """Resolve a group chat by path or UUID to its canonical markdown path."""
    real_path = _core._resolve_group_chat_path(raw_path)
    if real_path:
        return real_path
    chat_uuid = str(raw_uuid or "").strip().lower()
    if not _valid_group_chat_uuid(chat_uuid):
        return ""
    group_chats_dir = os.path.expanduser("~/.claude/group-chats")
    try:
        fnames = os.listdir(group_chats_dir)
    except OSError:
        return ""
    for fname in fnames:
        if not fname.endswith(".json"):
            continue
        md_path = os.path.join(group_chats_dir, fname[:-5] + ".md")
        meta = _core._load_group_chat_sidecar(md_path)
        existing = str(meta.get("uuid") or meta.get("id") or "").strip().lower()
        if existing == chat_uuid and os.path.exists(md_path):
            return os.path.realpath(md_path)
    return ""


def _group_chat_set_archived(raw_path: str, archived: bool, raw_uuid: str = "") -> dict:
    """Flip the archived flag on a chat sidecar. Drops the chat from the
    active watcher dict on archive (so it stops getting nudged). Returns
    the same shape as other group-chat handlers: {ok, error?}.
    """
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    fields = {"archived": bool(archived)}
    if archived:
        fields["archived_at"] = time.time()
    else:
        fields["archived_at"] = None
        fields["trashed"] = False
    if not _core._update_group_chat_sidecar(real_path, **fields):
        return {"ok": False, "error": "could not update sidecar"}
    if archived:
        # Log BEFORE the watcher pop so the baseline-mtime bump inside
        # _group_chat_log_system can still find the entry. After the pop
        # the bump becomes a harmless no-op for already-removed paths.
        _core._group_chat_log_system(real_path, "archived chat")
        with _coord_lock:
            _active_coordinations.pop(real_path, None)
    else:
        _core._group_chat_log_system(real_path, "unarchived chat")
    return {"ok": True}


def _group_chat_set_trashed(raw_path: str, trashed: bool, raw_uuid: str = "") -> dict:
    """Set Trash membership, archiving the chat first when necessary."""
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    if trashed and not _core._load_group_chat_sidecar(real_path).get("archived"):
        archive_result = _core._group_chat_set_archived(real_path, True)
        if not archive_result.get("ok"):
            return archive_result
    if not _core._update_group_chat_sidecar(real_path, trashed=bool(trashed)):
        return {"ok": False, "error": "could not update sidecar"}
    return {"ok": True}


def _group_chat_is_paused(chat_path: str) -> bool:
    """True if the chat's orchestration has been disabled by the user.

    Paused chats are inert: the watcher never nudges them, _register_coordination
    refuses to (re)add them to the active dict, and _group_chat_nudge bails. This
    is the user-facing "disable" knob — it stops CCC's token-burning loop for the
    chat without touching the participant sessions themselves.
    """
    try:
        return bool(_core._load_group_chat_sidecar(chat_path).get("paused"))
    except Exception:
        return False


def _group_chat_set_paused(raw_path: str, paused: bool, raw_uuid: str = "") -> dict:
    """Flip the paused flag on a chat sidecar — the enable/disable knob.

    On pause: drop the chat from _active_coordinations so the watcher stops
    nudging immediately. On resume: re-register so the watcher picks it back up
    on the next file change. Mirrors _group_chat_set_archived's contract.
    """
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    fields = {"paused": bool(paused)}
    fields["paused_at"] = time.time() if paused else None
    if not _core._update_group_chat_sidecar(real_path, **fields):
        return {"ok": False, "error": "could not update sidecar"}
    if paused:
        # Log BEFORE the watcher pop (same ordering rationale as archive) so the
        # baseline-mtime bump in _group_chat_log_system can still find the entry.
        _core._group_chat_log_system(real_path, "orchestration disabled — no further nudges")
        with _coord_lock:
            _active_coordinations.pop(real_path, None)
    else:
        _core._group_chat_log_system(real_path, "orchestration enabled")
        # Re-arm the watcher. _register_coordination re-reads the (now cleared)
        # paused flag and adds it back.
        _core._register_coordination(real_path)
    return {"ok": True}


def _group_chat_log_system(real_path: str, message: str) -> None:
    """Append a `> _<ts> — system: <message>_` line to the chat file and
    advance the watcher's baseline mtime so this administrative write
    isn't treated as participant activity. Without the baseline bump
    the watcher would see the system write, fire a nudge after the
    debounce window, write its own "pinged" line in turn, and loop —
    spamming the chat with system entries roughly once per minute.
    """
    if not real_path or not message:
        return
    try:
        # datetime.now() is naive — strftime("%Z") returns an empty string
        # for naive datetimes, leaving a stray "  — system" double-space.
        # astimezone() attaches the local tz so %Z renders "PDT" / "UTC".
        ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        ts = ""
    line = f"> _{ts} — system: {message}_\n\n"
    try:
        with open(real_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        return
    try:
        new_mtime = os.stat(real_path).st_mtime
    except OSError:
        return
    with _coord_lock:
        entry = _active_coordinations.get(real_path)
        if entry is not None:
            entry["mtime"] = new_mtime


def _group_chat_clear(raw_path: str, raw_uuid: str = "") -> dict:
    """Wipe message history from a chat: rewrite the .md with a fresh
    header (topic / Started / Mode / Participants), append a system
    log line marking the clear, then nudge all participants so they
    wake up and read the now-empty chat with a clean slate.

    Doesn't touch the sidecar — `session_ids` and `name_map` are
    preserved. Use Archive (📦) if you want to actually retire the
    chat; Clear is the equivalent of erasing the whiteboard with
    everyone still in the room.
    """
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}

    sidecar = _core._load_group_chat_sidecar(real_path)
    topic = sidecar.get("topic") or ""
    mode = sidecar.get("mode") or "topic"
    try:
        with open(real_path, "r", encoding="utf-8") as fh:
            existing_header = fh.read(4000)
    except OSError:
        existing_header = ""
    participants_str = _group_chat_participants_str(sidecar, existing_header)

    # Count existing messages so the system log can record what was wiped.
    prior_count = _core._group_chat_message_count(real_path)

    now = datetime.now().astimezone()
    full_ts = now.strftime("%Y-%m-%d %A %H:%M:%S %Z")
    header = (
        f"# Group Chat — {topic}\n"
        f"**Started:** {full_ts}\n"
        f"**Mode:** {mode}\n"
        f"**Participants:** {participants_str}\n"
    )
    try:
        with open(real_path, "w", encoding="utf-8") as fh:
            fh.write(header)
    except OSError as exc:
        return {"ok": False, "error": f"could not rewrite chat: {exc}"}

    # Update the watcher's baseline mtime so the clear write doesn't
    # double-fire a nudge (the explicit nudge below covers it).
    try:
        new_mtime = os.stat(real_path).st_mtime
        with _coord_lock:
            entry = _active_coordinations.get(real_path)
            if entry is not None:
                entry["mtime"] = new_mtime
    except OSError:
        pass

    _core._group_chat_log_system(
        real_path, f"cleared chat content (wiped {prior_count} messages)"
    )

    # Explicitly nudge so participants re-engage immediately. The skill
    # will see only the header + system log lines (no `##` posts), so its
    # exclude-last-writer logic returns no match and everyone is pinged.
    _core._register_coordination(real_path)
    nudge = _core._group_chat_nudge(real_path)

    return {"ok": True, "wiped": prior_count, "nudge": nudge}


def _group_chat_rename(raw_path: str, new_topic: str, raw_uuid: str = "") -> dict:
    """Rename a chat by updating the sidecar's `topic` field. The chat
    file header is repaired from the sidecar without touching appended
    messages. New posts to the chat will use the updated topic in their
    inject text.
    """
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    new_topic = (new_topic or "").strip()
    if not new_topic:
        return {"ok": False, "error": "missing topic"}

    sidecar = _core._load_group_chat_sidecar(real_path)
    old_topic = sidecar.get("topic") or ""
    if not _core._update_group_chat_sidecar(real_path, topic=new_topic):
        return {"ok": False, "error": "could not update sidecar"}

    if old_topic and old_topic != new_topic:
        _core._group_chat_log_system(
            real_path, f"renamed topic from `{old_topic}` to `{new_topic}`"
        )
    elif not old_topic:
        _core._group_chat_log_system(real_path, f"set topic to `{new_topic}`")

    _core._group_chat_update_header_if_changed(real_path, force_write=True)
    return {"ok": True, "topic": new_topic}


def _group_chat_remove_participant(raw_path: str, session_id: str, raw_uuid: str = "") -> dict:
    """Drop a session from an existing chat: remove from session_ids /
    name_map. The session's running /group-chat skill will keep cycling
    until it self-leaves (or is killed) — we just stop nudging it via
    the watcher because nudge reads session_ids fresh from the sidecar.
    Idempotent — removing an absent session is a no-op success.
    """
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}

    sidecar = _core._load_group_chat_sidecar(real_path)
    session_ids = list(sidecar.get("session_ids") or [])
    name_map = dict(sidecar.get("name_map") or {})

    if sid not in session_ids and sid not in name_map:
        return {"ok": True, "session_id": sid, "was_participant": False}

    session_ids = [s for s in session_ids if s != sid]
    name_map.pop(sid, None)

    if not _core._update_group_chat_sidecar(
        real_path, session_ids=session_ids, name_map=name_map
    ):
        return {"ok": False, "error": "could not update sidecar"}

    removed_label = sidecar.get("name_map", {}).get(sid) or sid
    _core._group_chat_log_system(real_path, f"removed `{removed_label}` ({sid[:8]})")
    _core._group_chat_update_header_if_changed(real_path, force_write=True)

    return {"ok": True, "session_id": sid, "was_participant": True}


def _group_chat_rename_participant(raw_path: str, session_id: str, new_name: str, raw_uuid: str = "") -> dict:
    """Rename a participant's display name in the sidecar's name_map.

    Transcript rendering resolves each message's speaker name from the hash
    at render time (gcDisplayName in app.js), never from literal text stored
    in the markdown headings, so this retroactively relabels every past
    message on the next poll — no rewrite of the .md file needed.
    """
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}

    clean_name = _core._group_chat_storeable_display_name(new_name, sid)
    if not clean_name:
        return {"ok": False, "error": "invalid name"}

    sidecar = _core._load_group_chat_sidecar(real_path)
    session_ids = list(sidecar.get("session_ids") or [])
    if sid not in session_ids:
        return {"ok": False, "error": "not found"}
    name_map = dict(sidecar.get("name_map") or {})
    old_name = name_map.get(sid) or _core._group_chat_fallback_agent_name(sid, session_ids)

    if old_name == clean_name:
        return {"ok": True, "session_id": sid, "name": clean_name, "changed": False}

    name_map[sid] = clean_name
    if not _core._update_group_chat_sidecar(real_path, name_map=name_map):
        return {"ok": False, "error": "could not update sidecar"}

    _core._group_chat_log_system(real_path, f"renamed `{old_name}` to `{clean_name}` ({sid[:8]})")
    _core._group_chat_update_header_if_changed(real_path, force_write=True)

    return {"ok": True, "session_id": sid, "name": clean_name, "changed": True}


def _group_chat_add_participant(raw_path: str, session_id: str, display_name: str = "", raw_uuid: str = "") -> dict:
    """Add a session to an existing chat: append to sidecar's session_ids /
    name_map, then inject /group-chat into the session so it joins live.
    Idempotent — re-adding an existing participant is a no-op success.
    """
    real_path = _core._resolve_group_chat_ref(raw_path, raw_uuid)
    if not real_path:
        return {"ok": False, "error": "forbidden"}
    if not os.path.exists(real_path):
        return {"ok": False, "error": "not found"}
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "missing session_id"}

    sidecar = _core._load_group_chat_sidecar(real_path)
    session_ids = list(sidecar.get("session_ids") or [])
    name_map = dict(sidecar.get("name_map") or {})
    topic = sidecar.get("topic") or ""
    mode = sidecar.get("mode") or "topic"

    already = sid in session_ids
    if not already:
        session_ids.append(sid)
    clean_display_name = _core._group_chat_storeable_display_name(display_name, sid)
    if clean_display_name and not name_map.get(sid):
        name_map[sid] = clean_display_name
    elif sid not in name_map:
        resolved = _core._group_chat_resolve_session_display_name(sid)
        if resolved:
            name_map[sid] = resolved

    if not _core._update_group_chat_sidecar(
        real_path, session_ids=session_ids, name_map=name_map
    ):
        return {"ok": False, "error": "could not update sidecar"}

    # If the chat had been dropped from the watcher (idle timeout, etc),
    # bring it back in — adding a participant means the user expects it
    # to be live again.
    _core._register_coordination(real_path)

    # Existing participants get the check-in instruction too (CCC-114): the
    # join link doubles as a "go read the chat now" nudge, so re-adding is
    # idempotent for membership but still delivers the check-in.
    text = _core._group_chat_checkin_text(real_path, topic, mode, sid)
    inject_result = _core._inject_text_into_session(sid, text, source="group-chat-add-participant")
    if not already:
        added_label = _core._group_chat_participant_label(sid, name_map, session_ids)
        _core._group_chat_log_system(real_path, f"added `{added_label}` ({sid[:8]})")
    _core._group_chat_update_header_if_changed(real_path, force_write=True)

    return {
        "ok": True,
        "session_id": sid,
        "already_participant": already,
        "inject": inject_result,
    }


