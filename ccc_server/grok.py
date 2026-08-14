# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 40559-41398).

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
import urllib.request

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Grok CLI conversation ingestion + ACP spawn (xAI Grok Build).
#
# Two different tools install a `grok` binary under ~/.grok (overridable via
# the GROK_HOME env var) and overwrite each other:
#   Variant A — xAI "Grok Build" (Rust): per-session dirs at
#     sessions/<url-encoded-cwd>/<session-uuid>/ with a summary.json index
#     record and an ACP session-update stream (updates.jsonl, falling back
#     to chat_history.jsonl) as the authoritative transcript.
#   Variant B — superagent-ai/grok-cli (npm): a single SQLite grok.db with
#     workspaces / sessions / messages tables.
# Both stores may coexist (one tool overwrote the other's binary while old
# data remained), so both are scanned and their rows merged.
# Spawn / follow-up / cancel for variant A go through `grok agent stdio`
# (ACP) in server.py; this module stays the on-disk listing + replay path.
# ---------------------------------------------------------------------------

GROK_LIVE_WINDOW_S = 180


def _grok_home():
    raw = os.environ.get("GROK_HOME", "").strip()
    if raw:
        return Path(os.path.expanduser(raw))
    return Path.home() / ".grok"


def _grok_sid_ok(session_id):
    sid = str(session_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or sid.startswith("."):
        return ""
    return sid


def _grok_epoch(value):
    """Best-effort epoch seconds from a Grok timestamp (epoch s/ms or ISO)."""
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


def _grok_db_path():
    p = _grok_home() / "grok.db"
    try:
        return p if p.exists() else None
    except OSError:
        return None


def _grok_db_connect():
    db = _grok_db_path()
    if not db:
        return None
    try:
        con = sqlite3.connect(str(db), timeout=0.5)
        con.execute("PRAGMA query_only=1")
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _grok_session_dir(session_id):
    """Path to a variant-A session dir (sessions/<cwd-bucket>/<sid>/), or
    None. Cheap probe — also used by engine detection, so it must return
    fast for foreign session ids."""
    sid = _grok_sid_ok(session_id)
    if not sid:
        return None
    root = _grok_home() / "sessions"
    try:
        buckets = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        return None
    for bucket in buckets:
        try:
            cand = bucket / sid
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return None


def _is_grok_session(session_id):
    if _grok_session_dir(session_id) is not None:
        return True
    sid = _grok_sid_ok(session_id)
    if not sid:
        return False
    con = _grok_db_connect()
    if con is None:
        return False
    try:
        row = con.execute(
            "SELECT 1 FROM sessions WHERE id=? LIMIT 1", (sid,)
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        con.close()


def _grok_content_text(content):
    """Pull text out of a Grok/ACP content payload: a plain string, a
    {type: "text", text: ...} part, a list of parts, or a nested message
    dict. Shapes are guessed — anything unrecognized yields ""."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        t = content.get("text")
        if isinstance(t, str) and t.strip():
            return t
        for key in ("content", "message"):
            nested = _grok_content_text(content.get(key))
            if nested:
                return nested
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            t = _grok_content_text(item)
            if t and t.strip():
                parts.append(t)
        if parts:
            return "\n".join(parts)
    return ""


def _grok_event_role_text(ev):
    """(role, text) for one line of a Grok ACP updates.jsonl or a raw
    chat_history.jsonl — role is 'user' | 'assistant' | 'tool' | ''.
    Unknown shapes return ('', '') and are skipped by callers."""
    if not isinstance(ev, dict):
        return "", ""
    kind = str(ev.get("sessionUpdate") or "").lower()
    if kind:
        if "user" in kind:
            return "user", _grok_content_text(ev.get("content"))
        if "tool" in kind:
            return "tool", ""
        if "agent" in kind or "assistant" in kind:
            return "assistant", _grok_content_text(ev.get("content"))
        return "", ""
    role = str(ev.get("role") or "").lower()
    if role in ("user", "assistant"):
        return role, _grok_content_text(ev.get("content"))
    if role == "tool":
        return "tool", ""
    return "", ""


def _grok_decode_bucket_cwd(bucket):
    """Original cwd for a sessions/<bucket>/ dir: a `.cwd` sibling file
    wins (slug+hash bucket names), else the URL-decoded bucket name when
    it decodes to an absolute path."""
    try:
        cwd_file = bucket / ".cwd"
        if cwd_file.is_file():
            text = cwd_file.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    except OSError:
        pass
    try:
        decoded = urllib.parse.unquote(bucket.name)
    except Exception:
        return ""
    return decoded if decoded.startswith("/") else ""


def _grok_session_jsonl(session_dir):
    """Transcript file for a variant-A session dir: updates.jsonl (ACP
    stream) preferred, chat_history.jsonl (raw model messages) fallback."""
    for name in ("updates.jsonl", "chat_history.jsonl"):
        p = session_dir / name
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _grok_mine_jsonl_texts(jsonl_path):
    """(first_user, last_assistant, created, updated) mined from the head
    of a variant-A transcript file. Best-effort; all zeros when unreadable."""
    first_user = ""
    last_assistant = ""
    created = 0.0
    updated = 0.0
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
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
                ts = _grok_epoch(
                    ev.get("timestamp") or ev.get("ts") or ev.get("created_at")
                )
                if ts:
                    if not created or ts < created:
                        created = ts
                    if ts > updated:
                        updated = ts
                role, text = _grok_event_role_text(ev)
                text = text.strip()
                if role == "user" and text and not first_user:
                    first_user = text
                elif role == "assistant" and text:
                    last_assistant = text
    except OSError:
        pass
    return first_user, last_assistant, created, updated


def _grok_session_dir_info(session_dir, cwd):
    """One listing dict for a variant-A session dir, or None when the dir
    carries neither a summary.json nor a readable transcript file."""
    summary = {}
    sp = session_dir / "summary.json"
    try:
        if sp.is_file():
            data = json.loads(sp.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                summary = data
    except (OSError, json.JSONDecodeError):
        summary = {}
    jsonl = _grok_session_jsonl(session_dir)
    if jsonl is None and not summary:
        return None
    title = str(
        summary.get("title") or summary.get("summary") or summary.get("name") or ""
    ).strip()
    model = str(
        summary.get("model") or summary.get("modelId") or summary.get("model_id") or ""
    ).strip()
    created = _grok_epoch(
        summary.get("createdAt") or summary.get("created_at")
        or summary.get("created") or summary.get("startedAt")
    )
    updated = _grok_epoch(
        summary.get("updatedAt") or summary.get("updated_at")
        or summary.get("lastActiveAt") or summary.get("last_activity_at")
        or summary.get("modified")
    )
    size = 0
    first_user = ""
    last_assistant = ""
    if jsonl is not None:
        try:
            st = jsonl.stat()
            size = st.st_size
            if not updated:
                updated = float(st.st_mtime)
        except OSError:
            pass
        first_user, last_assistant, j_created, j_updated = _grok_mine_jsonl_texts(jsonl)
        if not created:
            created = j_created
        if j_updated and j_updated > updated:
            updated = j_updated
    if not created:
        created = updated
    if not updated:
        updated = created
    return {
        "id": session_dir.name,
        "cwd": cwd,
        "title": title,
        "model": model,
        "created": created,
        "updated": updated,
        "archived": False,
        "jsonl_path": str(jsonl) if jsonl else "",
        "size": size,
        "first_user": first_user,
        "last_assistant": last_assistant,
    }


def _grok_sessions_from_dirs(limit=None):
    """Variant-A listing: scan sessions/<cwd-bucket>/<sid>/ dirs under
    GROK_HOME. Missing store → []."""
    root = _grok_home() / "sessions"
    try:
        buckets = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        return []
    out = []
    for bucket in buckets:
        try:
            if not bucket.is_dir():
                continue
            subs = list(bucket.iterdir())
        except OSError:
            continue
        cwd = _grok_decode_bucket_cwd(bucket)
        for d in subs:
            try:
                if not d.is_dir():
                    continue
            except OSError:
                continue
            info = _grok_session_dir_info(d, cwd)
            if info:
                out.append(info)
    out.sort(key=lambda s: s.get("updated") or 0, reverse=True)
    if limit and limit > 0:
        out = out[: int(limit)]
    return out


def _grok_db_message_text(raw):
    """Text out of one grok.db messages.message_json value — usually a JSON
    string holding {role, content}, but tolerate already-decoded shapes."""
    if raw is None:
        return ""
    if isinstance(raw, (dict, list)):
        return _grok_content_text(raw)
    s = str(raw).strip()
    if not s:
        return ""
    if s[:1] in ("{", "["):
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            text = _grok_content_text(parsed)
            if text:
                return text
    return s


def _grok_db_sessions(con, limit=None):
    """Variant-B listing: sessions ⋈ workspaces from grok.db. Column names
    are probed defensively so a foreign/older DB degrades to []."""
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
    except sqlite3.Error:
        return []
    if "id" not in cols:
        return []
    try:
        wcols = {r["name"] for r in con.execute("PRAGMA table_info(workspaces)")}
    except sqlite3.Error:
        wcols = set()
    order_col = _core._copilot_first_col(cols, ("updated_at", "last_activity_at", "created_at"))
    join = ""
    select_extra = ""
    if "workspace_id" in cols and "id" in wcols:
        join = " LEFT JOIN workspaces w ON s.workspace_id = w.id"
        if "canonical_path" in wcols:
            select_extra += ", w.canonical_path AS ws_canonical_path"
        if "display_name" in wcols:
            select_extra += ", w.display_name AS ws_display_name"
    sql = f"SELECT s.*{select_extra} FROM sessions s{join}"
    if order_col:
        sql += f" ORDER BY s.{order_col} DESC"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    title_col = _core._copilot_first_col(cols, ("title", "recap_text", "summary", "name"))
    cwd_col = _core._copilot_first_col(cols, ("cwd_at_start", "cwd_last", "cwd"))
    model_col = _core._copilot_first_col(cols, ("model", "model_id"))
    created_col = _core._copilot_first_col(cols, ("created_at", "created"))
    updated_col = _core._copilot_first_col(cols, ("updated_at", "last_activity_at", "updated"))
    status_col = "status" if cols and "status" in cols else None
    out = []
    try:
        rows = list(con.execute(sql))
    except sqlite3.Error:
        return []
    for r in rows:
        d = dict(r)
        sid = str(d.get("id") or "").strip()
        if not sid:
            continue
        cwd = str(d.get(cwd_col) or "").strip() if cwd_col else ""
        if not cwd:
            cwd = str(d.get("ws_canonical_path") or "").strip()
        title = str(d.get(title_col) or "").strip() if title_col else ""
        status = str(d.get(status_col) or "").strip().lower() if status_col else ""
        first_user, last_assistant = _grok_db_turn_texts(con, sid)
        out.append({
            "id": sid,
            "cwd": cwd,
            "title": title,
            "model": str(d.get(model_col) or "").strip() if model_col else "",
            "created": _grok_epoch(d.get(created_col)) if created_col else 0.0,
            "updated": _grok_epoch(d.get(updated_col)) if updated_col else 0.0,
            "archived": status in ("archived", "deleted"),
            "jsonl_path": "",
            "size": 0,
            "first_user": first_user,
            "last_assistant": last_assistant,
        })
    return out


def _grok_db_turn_texts(con, sid):
    """(first_user_text, last_assistant_text) from grok.db's messages table;
    ('', '') when the table/columns don't match this build of the CLI."""
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(messages)")}
    except sqlite3.Error:
        return "", ""
    sid_col = _core._copilot_first_col(cols, ("session_id", "sessionId", "session"))
    body_col = _core._copilot_first_col(cols, ("message_json", "content", "text", "message"))
    if not sid_col or not body_col:
        return "", ""
    role_col = "role" if "role" in cols else None
    order_col = _core._copilot_first_col(cols, ("seq", "created_at", "rowid", "id"))
    first_user = ""
    last_assistant = ""
    sql = f"SELECT * FROM messages WHERE {sid_col}=?"
    if order_col and order_col != "rowid":
        sql += f" ORDER BY {order_col}"
    try:
        for r in con.execute(sql, (sid,)):
            d = dict(r)
            text = _grok_db_message_text(d.get(body_col)).strip()
            if not text:
                continue
            role = str(d.get(role_col) or "").lower() if role_col else ""
            if role == "user":
                if not first_user:
                    first_user = text
            elif role == "assistant":
                last_assistant = text
    except sqlite3.Error:
        pass
    return first_user, last_assistant


def find_grok_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    """Discover Grok CLI sessions from ~/.grok (GROK_HOME).

    Both on-disk variants are scanned and merged: variant A (xAI "Grok
    Build") per-session dirs under sessions/<encoded-cwd>/<uuid>/, and
    variant B (superagent-ai/grok-cli) rows in grok.db. No store → []."""
    sessions = _grok_sessions_from_dirs(limit=limit)
    con = _grok_db_connect()
    if con is not None:
        sessions.extend(_grok_db_sessions(con, limit=limit))
        con.close()
    if not sessions:
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
        display_name = (
            name_overrides.get(sid)
            or _core._truncate_session_name(title)
            or (first_message[:80] if first_message else None)
            or (title[:80] if title else "Grok session")
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
        else:
            folder_label = "Grok"
        _wt_worktree_label = None
        _wt_idx = folder_label.find("-wt-")
        if _wt_idx > 0:
            _wt_worktree_label = folder_label[_wt_idx + 4:]
            folder_label = folder_label[:_wt_idx]
        is_live = (now - modified) < GROK_LIVE_WINDOW_S
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "grok",
            "engine": "grok",
            "timestamp": "",
            "branch": "",
            "git_branch": "",
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
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    return out


def _parse_grok_updates_file(path):
    """CCC transcript events from a variant-A updates.jsonl (ACP
    session-update stream). Defensive by design: unknown update kinds are
    skipped and a malformed line never aborts the parse."""
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
                kind = str(ev.get("sessionUpdate") or ev.get("type") or "").lower()
                ts = str(ev.get("timestamp") or ev.get("ts") or "")
                if "user" in kind:
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "user_text",
                        "text": text, "images": [],
                    })
                elif "tool" in kind and (
                    "result" in kind or "update" in kind
                    or "complete" in kind or "finish" in kind
                ):
                    result = ev.get("rawOutput")
                    if result is None:
                        result = ev.get("output")
                    if result is None:
                        result = ev.get("result")
                    if result is None:
                        result = ev.get("error")
                    if isinstance(result, (dict, list)):
                        result = json.dumps(result)[:800]
                    is_error = bool(ev.get("error")) or str(
                        ev.get("status") or ""
                    ).lower() in ("failed", "error")
                    if result is None and not is_error:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "tool_result",
                        "text": str(result or "")[:800],
                        "tool_use_id": str(
                            ev.get("toolCallId") or ev.get("id") or ""
                        ),
                        "is_error": is_error,
                    })
                elif "tool" in kind:
                    name = str(
                        ev.get("title") or ev.get("name")
                        or ev.get("toolName") or ev.get("kind") or ""
                    )
                    args = ev.get("rawInput") or ev.get("input") or ev.get("arguments") or {}
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
                        "message_id": f"grok-{line}",
                        "blocks": [{
                            "kind": "tool_use",
                            "name": name,
                            "detail": str(detail)[:200],
                            "id": str(ev.get("toolCallId") or ev.get("id") or ""),
                            "command": command,
                            "command_kind": None,
                        }],
                    })
                elif "agent" in kind or "assistant" in kind or "message" in kind:
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"grok-{line}",
                        "blocks": [{"kind": "text", "text": text}],
                    })
                # Anything else (plan updates, usage signals, unknown future
                # kinds) carries no transcript text — skip, never crash.
    except OSError:
        pass
    return events, line


def _parse_grok_chat_history_file(path):
    """CCC transcript events from a variant-A chat_history.jsonl fallback
    ({role, content} raw model messages)."""
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
                role, text = _grok_event_role_text(ev)
                text = text.strip()
                if not text:
                    continue
                ts = str(ev.get("timestamp") or ev.get("ts") or "")
                if role == "user":
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "user_text",
                        "text": text, "images": [],
                    })
                elif role == "assistant":
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"grok-{line}",
                        "blocks": [{"kind": "text", "text": text}],
                    })
    except OSError:
        pass
    return events, line


def _parse_grok_db_messages(session_id):
    """CCC transcript events from variant-B grok.db messages, ordered by
    seq. Defensive: unknown roles/columns degrade, never crash."""
    con = _grok_db_connect()
    if con is None:
        return [], 0
    events = []
    line = 0
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(messages)")}
        sid_col = _core._copilot_first_col(cols, ("session_id", "sessionId", "session"))
        body_col = _core._copilot_first_col(cols, ("message_json", "content", "text", "message"))
        if not sid_col or not body_col:
            return [], 0
        role_col = "role" if "role" in cols else None
        ts_col = _core._copilot_first_col(cols, ("created_at", "timestamp", "ts"))
        order_col = _core._copilot_first_col(cols, ("seq", "created_at", "rowid", "id"))
        sql = f"SELECT * FROM messages WHERE {sid_col}=?"
        if order_col and order_col != "rowid":
            sql += f" ORDER BY {order_col}"
        for r in con.execute(sql, (session_id,)):
            d = dict(r)
            role = str(d.get(role_col) or "").lower() if role_col else ""
            text = _grok_db_message_text(d.get(body_col)).strip()
            if not text:
                continue
            ts = str(d.get(ts_col) or "") if ts_col else ""
            if role == "user":
                line += 1
                events.append({
                    "line": line, "ts": ts, "type": "user_text",
                    "text": text, "images": [],
                })
            elif role == "assistant":
                line += 1
                events.append({
                    "line": line, "ts": ts, "type": "assistant",
                    "message_id": f"grok-{line}",
                    "blocks": [{"kind": "text", "text": text}],
                })
            elif role == "tool":
                line += 1
                events.append({
                    "line": line, "ts": ts, "type": "tool_result",
                    "text": text[:800], "tool_use_id": "", "is_error": False,
                })
            # system / unknown roles carry no transcript text — skip.
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return events, line


def _parse_grok_conversation(session_id, after_line=0):
    """Build a CCC transcript event list for a Grok session: variant-A
    session dir (updates.jsonl preferred, chat_history.jsonl fallback) or
    variant-B grok.db messages."""
    session_dir = _grok_session_dir(session_id)
    if session_dir is not None:
        jsonl = _grok_session_jsonl(session_dir)
        if jsonl is None:
            return {"events": [], "last_line": 0}
        if jsonl.name == "updates.jsonl":
            events, line = _parse_grok_updates_file(jsonl)
        else:
            events, line = _parse_grok_chat_history_file(jsonl)
    else:
        events, line = _parse_grok_db_messages(session_id)
    if after_line and after_line > 0:
        visible = [e for e in events if e["line"] > after_line]
    else:
        visible = events
    return {"events": visible, "last_line": line}

