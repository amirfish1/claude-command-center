# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 39977-40557).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import sqlite3
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Copilot CLI conversation ingestion (read-only).
#
# GitHub Copilot CLI keeps its store under ~/.copilot on every OS (overridable
# via the COPILOT_HOME env var): a session-store.db SQLite index plus one
# authoritative event log per session at session-state/<uuid>/events.jsonl.
# The DB schema is not formally versioned, so CCC treats the DB as a fast path
# only — column probes are defensive and a missing/unreadable/foreign DB falls
# back to scanning the event logs directly. Listing + transcript view only;
# no spawn / resume support.
# ---------------------------------------------------------------------------

COPILOT_LIVE_WINDOW_S = 180


def _copilot_home():
    raw = os.environ.get("COPILOT_HOME", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return Path.home() / ".copilot"


def _copilot_db_path():
    p = _copilot_home() / "session-store.db"
    try:
        return p if p.exists() else None
    except OSError:
        return None


def _copilot_connect():
    db = _copilot_db_path()
    if not db:
        return None
    try:
        con = sqlite3.connect(str(db), timeout=0.5)
        con.execute("PRAGMA query_only=1")
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _copilot_epoch(value):
    """Best-effort epoch seconds from a Copilot timestamp (epoch s/ms or ISO)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0.0
        try:
            v = float(s)
            return v / 1000.0 if v > 1e12 else v
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError, OSError):
            return 0.0
    return 0.0


def _copilot_first_col(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None


def _copilot_events_path(session_id):
    """Path to a session's events.jsonl, or None. Cheap probe — also used by
    engine detection, so it must return fast for foreign session ids."""
    sid = str(session_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or sid.startswith("."):
        return None
    p = _copilot_home() / "session-state" / sid / "events.jsonl"
    try:
        return p if p.is_file() else None
    except OSError:
        return None


def _is_copilot_session(session_id):
    return _copilot_events_path(session_id) is not None


def _copilot_event_text(data):
    """Pull message text out of an event's data payload, tolerating the
    several shapes Copilot events have been observed/guessed to use."""
    if not isinstance(data, dict):
        return ""
    for key in ("message", "content", "text"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, list):
            parts = []
            for item in v:
                if isinstance(item, dict):
                    t = item.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append(t)
                elif isinstance(item, str) and item.strip():
                    parts.append(item)
            if parts:
                return "\n".join(parts)
        if isinstance(v, dict):  # e.g. message: {role, content}
            nested = _copilot_event_text(v)
            if nested:
                return nested
    return ""


def _copilot_fetch_sessions(con, limit=None):
    """One dict per row of Copilot's `sessions` table, newest first. Column
    names are probed defensively — the schema is not formally versioned."""
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
    except sqlite3.Error:
        return []
    if "id" not in cols:
        return []
    order_col = _copilot_first_col(cols, (
        "updated_at", "time_updated", "modified_at", "last_activity_at",
        "created_at", "time_created", "created",
    ))
    sql = "SELECT * FROM sessions"
    if order_col:
        sql += f" ORDER BY {order_col} DESC"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    title_col = _copilot_first_col(cols, ("title", "summary", "name"))
    repo_col = _copilot_first_col(cols, ("repository", "repo"))
    cwd_col = _copilot_first_col(cols, ("cwd", "directory", "working_directory", "workspace"))
    branch_col = _copilot_first_col(cols, ("branch", "git_branch"))
    model_col = _copilot_first_col(cols, ("model", "model_id"))
    created_col = _copilot_first_col(cols, ("created_at", "time_created", "created"))
    updated_col = _copilot_first_col(cols, ("updated_at", "time_updated", "modified_at", "last_activity_at", "updated"))
    out = []
    try:
        for r in con.execute(sql):
            d = dict(r)
            sid = str(d.get("id") or "").strip()
            if not sid:
                continue
            ev_path = _copilot_events_path(sid)
            out.append({
                "id": sid,
                "cwd": str(d.get(cwd_col) or "") if cwd_col else "",
                "title": str(d.get(title_col) or "").strip() if title_col else "",
                "repository": str(d.get(repo_col) or "").strip() if repo_col else "",
                "branch": str(d.get(branch_col) or "").strip() if branch_col else "",
                "model": str(d.get(model_col) or "").strip() if model_col else "",
                "created": _copilot_epoch(d.get(created_col)) if created_col else 0.0,
                "updated": _copilot_epoch(d.get(updated_col)) if updated_col else 0.0,
                "archived": False,
                "jsonl_path": str(ev_path) if ev_path else "",
            })
    except sqlite3.Error:
        return out
    return out


def _copilot_turn_texts(con, sid):
    """(first_user_text, last_assistant_text) from the turns table;
    ('', '') when the table/columns don't match this build of the CLI."""
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(turns)")}
    except sqlite3.Error:
        return "", ""
    sid_col = _copilot_first_col(cols, ("session_id", "sessionId", "session"))
    text_col = _copilot_first_col(cols, ("content", "text", "message"))
    if not sid_col or not text_col:
        return "", ""
    role_col = "role" if "role" in cols else None
    order_col = _copilot_first_col(cols, ("created_at", "time_created", "timestamp", "rowid", "id"))
    sql = f"SELECT * FROM turns WHERE {sid_col}=?"
    if order_col and order_col != "rowid":
        sql += f" ORDER BY {order_col}"
    first_user = ""
    last_assistant = ""
    try:
        for r in con.execute(sql, (sid,)):
            d = dict(r)
            text = d.get(text_col)
            if not isinstance(text, str) or not text.strip():
                continue
            role = str(d.get(role_col) or "").lower() if role_col else ""
            if role == "user":
                if not first_user:
                    first_user = text.strip()
            elif role == "assistant":
                last_assistant = text.strip()
    except sqlite3.Error:
        pass
    return first_user, last_assistant


def _copilot_sessions_from_event_logs(limit=None):
    """Fallback listing when session-store.db is missing/unreadable: scan
    session-state/*/events.jsonl and mine session.start context + the first
    user/assistant messages from the head of each log."""
    root = _copilot_home() / "session-state"
    try:
        entries = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        return []
    out = []
    for d in entries:
        try:
            if not d.is_dir():
                continue
        except OSError:
            continue
        ev_path = d / "events.jsonl"
        try:
            st = ev_path.stat()
        except OSError:
            continue
        info = {
            "id": d.name,
            "cwd": "",
            "title": "",
            "repository": "",
            "branch": "",
            "model": "",
            "created": 0.0,
            "updated": float(st.st_mtime),
            "archived": False,
            "jsonl_path": str(ev_path),
            "size": st.st_size,
            "first_user": "",
            "last_assistant": "",
        }
        try:
            with open(ev_path, "r", encoding="utf-8", errors="replace") as f:
                for i, raw in enumerate(f):
                    if i >= 200:
                        break
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    ts = _copilot_epoch(ev.get("timestamp") or ev.get("ts"))
                    if ts:
                        if not info["created"] or ts < info["created"]:
                            info["created"] = ts
                        if ts > info["updated"]:
                            info["updated"] = ts
                    etype = str(ev.get("type") or "").lower()
                    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                    if etype == "session.start":
                        ctx = data.get("context") if isinstance(data.get("context"), dict) else data
                        info["repository"] = str(ctx.get("repository") or info["repository"] or "").strip()
                        info["branch"] = str(ctx.get("branch") or info["branch"] or "").strip()
                        cwd = str(ctx.get("cwd") or ctx.get("workingDirectory") or "").strip()
                        if cwd:
                            info["cwd"] = cwd
                    elif "user" in etype and not info["first_user"]:
                        info["first_user"] = _copilot_event_text(data).strip()
                    elif "assistant" in etype:
                        text = _copilot_event_text(data).strip()
                        if text:
                            info["last_assistant"] = text
        except OSError:
            continue
        if not info["created"]:
            info["created"] = info["updated"]
        out.append(info)
    out.sort(key=lambda s: s.get("updated") or 0, reverse=True)
    if limit and limit > 0:
        out = out[: int(limit)]
    return out


def _copilot_repo_slug_matches(slug, repo_path):
    """True when an owner/repo slug plausibly names the local repo directory
    (basename match). Used only when the session recorded no local cwd."""
    if not slug or not repo_path:
        return False
    name = str(slug).rstrip("/").split("/")[-1].strip().lower()
    if name.endswith(".git"):
        name = name[:-4]
    try:
        local = Path(repo_path).name.strip().lower()
    except (TypeError, ValueError):
        return False
    return bool(name) and name == local


def find_copilot_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    """Discover GitHub Copilot CLI sessions from ~/.copilot (COPILOT_HOME).

    session-store.db is the fast path; a missing/unreadable/foreign DB falls
    back to scanning session-state/*/events.jsonl. Store not found → []."""
    sessions = None
    con = _copilot_connect()
    if con is not None:
        sessions = _copilot_fetch_sessions(con, limit=limit)
        if not sessions:
            # DB exists but is empty or speaks an unknown schema — the event
            # logs are authoritative, so still try them.
            con.close()
            con = None
            sessions = None
    if sessions is None:
        sessions = _copilot_sessions_from_event_logs(limit=limit)
    if not sessions:
        if con is not None:
            con.close()
        return []
    if repo_only:
        repo_path = _core.resolve_repo_path(repo_path)
        repo_path_obj = Path(repo_path)
    try:
        repo_pins = _core._load_repo_pins()
    except Exception:
        repo_pins = {}
    try:
        name_overrides = _core._load_session_name_overrides()
    except Exception:
        name_overrides = {}
    try:
        archived_set, trashed_set = _core._load_conversation_lifecycle_sets()
    except Exception:
        archived_set, trashed_set = set(), set()
    try:
        verified_set = set(_core._load_verified_conversations())
    except Exception:
        verified_set = set()
    try:
        last_interactions = _core._load_last_interactions()
    except Exception:
        last_interactions = {}

    cutoff = _core._session_scan_cutoff_ts(include_old)
    max_rows = _core._session_scan_file_limit(include_old)
    git_top_cache = {}
    now = time.time()
    out = []
    for s in sessions:
        sid = s.get("id")
        if not sid:
            continue
        cwd = s.get("cwd") or ""
        pinned = repo_pins.get(sid)
        pinned_repo = False
        if repo_only:
            if pinned and pinned != repo_path:
                continue
            if pinned == repo_path:
                pinned_repo = True
            elif cwd and _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                pass
            elif not cwd and _copilot_repo_slug_matches(s.get("repository"), repo_path):
                pass
            else:
                continue
        modified = s.get("updated") or s.get("created") or 0
        freshness = max(modified, last_interactions.get(sid) or 0)
        if not include_old and cutoff > 0 and freshness < cutoff:
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
        title = _core._strip_ccc_session_state_instruction(s.get("title") or "").strip()
        first_message = _core._strip_ccc_session_state_instruction(
            s.get("first_user") or ""
        ).strip()
        last_assistant_text = s.get("last_assistant") or ""
        if con is not None and not first_message and not last_assistant_text:
            first_message, last_assistant_text = _copilot_turn_texts(con, sid)
            first_message = _core._strip_ccc_session_state_instruction(first_message).strip()
        display_name = (
            name_overrides.get(sid)
            or _core._truncate_session_name(title)
            or (first_message[:80] if first_message else None)
            or (title[:80] if title else "Copilot session")
        )
        effective_cwd = _core._first_existing_dir(cwd, pinned) or cwd
        try:
            cwd_exists = bool(effective_cwd and Path(effective_cwd).is_dir())
        except OSError:
            cwd_exists = False
        folder_path = pinned or cwd or effective_cwd or ""
        if folder_path:
            _git_root = _core._find_git_root(folder_path)
            folder_label = _core._resolve_dir_case(_git_root or folder_path)
        elif s.get("repository"):
            folder_label = s["repository"]
        else:
            folder_label = "Copilot"
        _wt_worktree_label = None
        _wt_idx = folder_label.find("-wt-")
        if _wt_idx > 0:
            _wt_worktree_label = folder_label[_wt_idx + 4:]
            folder_label = folder_label[:_wt_idx]
        branch = s.get("branch") or ""
        is_live = (now - modified) < COPILOT_LIVE_WINDOW_S
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "copilot",
            "engine": "copilot",
            "timestamp": "",
            "branch": branch,
            "git_branch": branch,
            "first_message": first_message[:200],
            "display_name": display_name,
            "ai_title": title or None,
            "name_overridden": bool(name_overrides.get(sid)),
            "last_prompt": first_message[:200],
            "size": s.get("size") or 0,
            "modified": modified,
            "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)) if modified else "",
            "mtime": modified,
            "jsonl_path": s.get("jsonl_path") or "",
            "folder_label": folder_label,
            "folder_path": folder_path,
            "worktree_label": _wt_worktree_label,
            "session_cwd": effective_cwd,
            "session_cwd_exists": cwd_exists,
            "session_cwd_is_worktree": bool(
                effective_cwd and (Path(effective_cwd) / ".git").is_file()
            ),
            "worktree_dirty": (
                _core._worktree_dirty_cached(effective_cwd, modified)
                if resolve_worktree_dirty and effective_cwd else False
            ),
            "effective_branch": None,
            "effective_kind": None,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_edit_pos": 0,
            "last_commit_pos": 0,
            "last_push_pos": 0,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "pending_tool_ts": 0,
            "last_assistant_text": last_assistant_text,
            "tail_issue_number": None,
            "tail_pr_number": None,
            "tail_pr_url": None,
            "pr_state": None,
            "session_state": _core._parse_session_state(last_assistant_text),
            "archived": sid in archived_set or bool(s.get("archived")),
            "trashed": sid in trashed_set,
            "verified": sid in verified_set,
            "pinned_repo": pinned_repo,
            "last_interacted": last_interactions.get(sid),
            "is_live": is_live,
            "spawn_pid": None,
            "needs_approval": False,
            "needs_approval_message": "",
            "model": s.get("model") or "",
            "reasoning_effort": "",
        })
    if con is not None:
        con.close()
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    return out


def _parse_copilot_conversation(session_id, after_line=0):
    """Build a CCC transcript event list from a Copilot session's
    events.jsonl. Defensive by design: unknown event types are skipped and a
    malformed line never aborts the parse."""
    path = _copilot_events_path(session_id)
    if path is None:
        return {"events": [], "last_line": 0}
    events = []
    line = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                etype = str(ev.get("type") or "").lower()
                ts = str(ev.get("timestamp") or ev.get("ts") or "")
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                if "user" in etype:
                    text = _copilot_event_text(data).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "user_text",
                        "text": text, "images": [],
                    })
                elif "tool" in etype and ("start" in etype or "call" in etype):
                    name = str(data.get("toolName") or data.get("name") or data.get("tool") or "")
                    args = data.get("arguments") or data.get("input") or {}
                    if isinstance(args, dict):
                        detail = (
                            args.get("command")
                            or args.get("description")
                            or (json.dumps(args)[:200] if args else "")
                        )
                        command = args.get("command")
                    else:
                        detail = str(args)[:200] if args else ""
                        command = None
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"copilot-{line}",
                        "blocks": [{
                            "kind": "tool_use",
                            "name": name,
                            "detail": str(detail)[:200],
                            "id": str(data.get("toolCallId") or data.get("id") or ""),
                            "command": command,
                            "command_kind": None,
                        }],
                    })
                elif "tool" in etype and (
                    "complete" in etype or "end" in etype
                    or "result" in etype or "finish" in etype
                ):
                    result = data.get("result")
                    if result is None:
                        result = data.get("output")
                    if result is None:
                        result = data.get("error")
                    if isinstance(result, (dict, list)):
                        result = json.dumps(result)[:800]
                    is_error = bool(data.get("error")) or data.get("success") is False
                    if result is None and not is_error:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "tool_result",
                        "text": str(result or "")[:800],
                        "tool_use_id": str(data.get("toolCallId") or data.get("id") or ""),
                        "is_error": is_error,
                    })
                elif "assistant" in etype or "message" in etype:
                    text = _copilot_event_text(data).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"copilot-{line}",
                        "blocks": [{"kind": "text", "text": text}],
                    })
                # Anything else (session.start, checkpoints, metrics, unknown
                # future types) carries no transcript text — skip, never crash.
    except OSError:
        pass
    if after_line and after_line > 0:
        visible = [e for e in events if e["line"] > after_line]
    else:
        visible = events
    return {"events": visible, "last_line": line}

