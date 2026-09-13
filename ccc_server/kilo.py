# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 39577-39975).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import time

from ccc_server import core as _core
from ccc_server import dbutil

# ---------------------------------------------------------------------------
# Kilo Code session ingestion (read-only).
#
# Kilo Code (OpenCode-derived) keeps its sessions in a SQLite DB at
# ~/.local/share/kilo/kilo.db with tables session / message / part. Reading it
# lets an externally-launched `kilo` appear on the board like a Claude or Codex
# session. The DB is WAL-mode and written live by the `kilo serve` daemon; a
# read-only-URI open can silently miss un-checkpointed WAL frames, so we open
# the file normally and immediately set PRAGMA query_only=1 — the standard WAL
# multi-reader path, which still cannot write to Kilo's DB.
# ---------------------------------------------------------------------------

KILO_LIVE_WINDOW_S = 180


def _kilo_db_path():
    p = Path.home() / ".local" / "share" / "kilo" / "kilo.db"
    return dbutil.path_if_exists(p)


def _kilo_connect():
    return dbutil.connect_readonly(_kilo_db_path())


def _kilo_model_str(raw):
    """Kilo stores model as JSON {providerID, modelID}; render it as a string."""
    if not raw:
        return ""
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return str(raw)
    if not isinstance(d, dict):
        return str(raw)
    mid = (d.get("modelID") or d.get("id") or "").strip()
    prov = (d.get("providerID") or "").strip()
    if prov and mid and "/" not in mid:
        return f"{prov}/{mid}"
    return mid or ""


def _kilo_fetch_sessions(con, limit=None):
    """Return one dict per row of Kilo's `session` table, newest first."""
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(session)")}
    except sqlite3.Error:
        return []
    if "id" not in cols:
        return []
    sql = "SELECT * FROM session ORDER BY time_updated DESC"
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    out = []
    try:
        for r in con.execute(sql):
            d = dict(r)
            out.append({
                "id": d.get("id") or "",
                "cwd": d.get("directory") or "",
                "title": (d.get("title") or "").strip(),
                "model": _kilo_model_str(d.get("model")),
                "agent": d.get("agent") or "",
                "created": (d.get("time_created") or 0) / 1000.0,
                "updated": (d.get("time_updated") or 0) / 1000.0,
                "archived": bool(d.get("time_archived")),
            })
    except sqlite3.Error:
        return out
    return out


def _kilo_first_user_text(con, sid):
    """First user-message text for a Kilo session (for the card preview)."""
    try:
        msg = con.execute(
            "SELECT id FROM message WHERE session_id=? "
            "AND json_extract(data,'$.role')='user' ORDER BY time_created LIMIT 1",
            (sid,),
        ).fetchone()
        if not msg:
            return ""
        for p in con.execute(
            "SELECT data FROM part WHERE message_id=? "
            "AND json_extract(data,'$.type')='text' ORDER BY time_created",
            (msg["id"],),
        ):
            try:
                txt = (json.loads(p["data"]) or {}).get("text") or ""
            except (ValueError, TypeError):
                txt = ""
            if txt.strip():
                return txt.strip()
    except sqlite3.Error:
        pass
    return ""


def _kilo_last_assistant_text(con, sid):
    """Last assistant text-part for a Kilo session (card subtitle / state)."""
    try:
        for m in con.execute(
            "SELECT id FROM message WHERE session_id=? "
            "AND json_extract(data,'$.role')='assistant' ORDER BY time_created DESC LIMIT 8",
            (sid,),
        ):
            texts = []
            for p in con.execute(
                "SELECT data FROM part WHERE message_id=? "
                "AND json_extract(data,'$.type')='text' ORDER BY time_created",
                (m["id"],),
            ):
                try:
                    t = (json.loads(p["data"]) or {}).get("text") or ""
                except (ValueError, TypeError):
                    t = ""
                if t.strip():
                    texts.append(t.strip())
            if texts:
                return "\n".join(texts)
    except sqlite3.Error:
        pass
    return ""


def find_kilo_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    """Discover external Kilo Code sessions from ~/.local/share/kilo/kilo.db."""
    con = _kilo_connect()
    if con is None:
        return []
    try:
        sessions = _kilo_fetch_sessions(con, limit=limit)
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
                elif not _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                    continue
            modified = s.get("updated") or s.get("created") or 0
            freshness = max(modified, last_interactions.get(sid) or 0)
            if not include_old and cutoff > 0 and freshness < cutoff:
                continue
            if not include_old and max_rows > 0 and len(out) >= max_rows:
                continue
            title = _core._strip_ccc_session_state_instruction(s.get("title") or "").strip()
            first_message = _core._strip_ccc_session_state_instruction(
                _kilo_first_user_text(con, sid)
            ).strip()
            # Kilo titles untouched conversations "New session - <iso>"; treat
            # those as not-AI-summarised so the ✨ glyph doesn't show.
            kilo_ai_title = title if (title and not title.startswith("New session")) else None
            display_name = (
                name_overrides.get(sid)
                or _core._truncate_session_name(title if kilo_ai_title else "")
                or (first_message[:80] if first_message else None)
                or (title[:80] if title else "Kilo session")
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
                folder_label = "Kilo"
            _wt_worktree_label = None
            _wt_idx = folder_label.find("-wt-")
            if _wt_idx > 0:
                _wt_worktree_label = folder_label[_wt_idx + 4:]
                folder_label = folder_label[:_wt_idx]
            last_assistant_text = _kilo_last_assistant_text(con, sid)
            is_live = (now - modified) < KILO_LIVE_WINDOW_S
            out.append({
                "id": sid,
                "session_id": sid,
                "source": "kilo",
                "engine": "kilo",
                "timestamp": "",
                "branch": "",
                "git_branch": "",
                "first_message": first_message[:200],
                "display_name": display_name,
                "ai_title": kilo_ai_title,
                "name_overridden": bool(name_overrides.get(sid)),
                "last_prompt": first_message[:200],
                "size": 0,
                "modified": modified,
                "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)) if modified else "",
                "mtime": modified,
                "jsonl_path": "",
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
                "trashed": sid in trashed_set or bool(s.get("trashed")),
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
    finally:
        con.close()
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    return out


def _parse_kilo_conversation(session_id, after_line=0):
    """Build a CCC transcript event list from a Kilo session's messages/parts."""
    con = _kilo_connect()
    if con is None:
        return {"events": [], "last_line": 0}
    events = []
    line = 0
    try:
        msgs = con.execute(
            "SELECT id, data FROM message WHERE session_id=? ORDER BY time_created",
            (session_id,),
        ).fetchall()
        for m in msgs:
            try:
                md = json.loads(m["data"]) if m["data"] else {}
            except (ValueError, TypeError):
                md = {}
            role = md.get("role")
            tc = (md.get("time") or {}).get("created")
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(tc / 1000.0)) if tc else ""
            try:
                parts = con.execute(
                    "SELECT data FROM part WHERE message_id=? ORDER BY time_created",
                    (m["id"],),
                ).fetchall()
            except sqlite3.Error:
                parts = []
            pdatas = []
            for p in parts:
                try:
                    pdatas.append(json.loads(p["data"]) if p["data"] else {})
                except (ValueError, TypeError):
                    pdatas.append({})
            if role == "user":
                text = "\n".join(
                    p.get("text", "") for p in pdatas if p.get("type") == "text"
                ).strip()
                if not text:
                    continue
                line += 1
                events.append({
                    "line": line, "ts": ts, "type": "user_text",
                    "text": text, "images": [],
                })
            elif role == "assistant":
                blocks = []
                tool_results = []
                for p in pdatas:
                    ptype = p.get("type")
                    if ptype in ("text", "reasoning"):
                        t = p.get("text") or ""
                        if t.strip():
                            blocks.append({"kind": "text", "text": t})
                    elif ptype == "tool":
                        st = p.get("state") or {}
                        inp = st.get("input") or {}
                        detail = (
                            inp.get("command")
                            or inp.get("description")
                            or st.get("title")
                            or (json.dumps(inp)[:200] if inp else "")
                        )
                        blocks.append({
                            "kind": "tool_use",
                            "name": p.get("tool", ""),
                            "detail": str(detail)[:200],
                            "id": p.get("callID", ""),
                            "command": inp.get("command"),
                            "command_kind": None,
                        })
                        out_text = st.get("output")
                        if out_text is not None:
                            tool_results.append({
                                "text": str(out_text)[:800],
                                "tool_use_id": p.get("callID", ""),
                                "is_error": st.get("status") == "error",
                            })
                if blocks:
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"kilo-{line}", "blocks": blocks,
                    })
                for tr in tool_results:
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "tool_result",
                        "text": tr["text"], "tool_use_id": tr["tool_use_id"],
                        "is_error": tr["is_error"],
                    })
    except sqlite3.Error:
        pass
    finally:
        con.close()
    if after_line and after_line > 0:
        visible = [e for e in events if e["line"] > after_line]
    else:
        visible = events
    return {"events": visible, "last_line": line}

