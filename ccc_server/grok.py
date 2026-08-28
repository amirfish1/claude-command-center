# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 40559-41398).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timezone
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


def grok_session_cwd(session_id):
    """cwd for a Grok CLI session (variant-A dir bucket or variant-B db row),
    or None. Used by server.py's find_session_cwd — without this, a Grok
    session with no live spawn-registry entry has no cwd resolution path at
    all, so "Launch" fails with "could not derive repo context"."""
    sid = _grok_sid_ok(session_id)
    if not sid:
        return None
    session_dir = _grok_session_dir(sid)
    if session_dir is not None:
        cwd = _grok_decode_bucket_cwd(session_dir.parent)
        if cwd:
            return cwd
    con = _grok_db_connect()
    if con is None:
        return None
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
        if "id" not in cols:
            return None
        cwd_col = _core._copilot_first_col(cols, ("cwd_at_start", "cwd_last", "cwd"))
        if not cwd_col:
            return None
        row = con.execute(
            f"SELECT {cwd_col} AS cwd FROM sessions WHERE id=? LIMIT 1", (sid,)
        ).fetchone()
        return str(row["cwd"]).strip() if row and row["cwd"] else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _extract_grok_usage(session_id):
    """Usage stats for a Grok session — model + reasoning effort only.

    Grok's local session store (variant-A summary.json / variant-B db row)
    doesn't record per-turn token usage the way kimi's wire.jsonl does, so
    token counts stay at 0 (same "unknown" shape cursor's usage uses).
    Without a model/engine here the conv-pane model pill has nothing to
    render and disappears entirely — the pill's `if (displayModel)` guard
    in app.js silently no-ops on an empty model (CCC-879).
    """
    override = _core._get_session_override(session_id)
    result = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "context_limit": 0,
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
        "engine": "grok",
        "override": override,
        "reasoning_effort": (override or {}).get("reasoning_effort") or "",
    }
    sid = _grok_sid_ok(session_id)
    if not sid:
        return result
    session_dir = _grok_session_dir(sid)
    if session_dir is not None:
        try:
            summary = json.loads((session_dir / "summary.json").read_text())
            result["model"] = str(summary.get("current_model_id") or "")
            if not result["reasoning_effort"]:
                result["reasoning_effort"] = str(summary.get("reasoning_effort") or "")
            return result
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    con = _grok_db_connect()
    if con is None:
        return result
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
        model_col = _core._copilot_first_col(cols, ("model", "model_id", "current_model_id"))
        if model_col:
            row = con.execute(
                f"SELECT {model_col} AS model FROM sessions WHERE id=? LIMIT 1", (sid,)
            ).fetchone()
            if row and row["model"]:
                result["model"] = str(row["model"]).strip()
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return result


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


def _grok_unwrap_acp_event(ev):
    """Flatten a Grok JSON-RPC envelope to the inner session-update dict.

    Newer Grok Build writes lines as `{"method": "session/update",
    "params": {"update": {...}, "sessionId": ...}}`.  Older lines and
    `chat_history.jsonl` are already flat.  Returns the inner `update` when
    wrapped, else the original dict, or `None` for non-dicts."""
    if not isinstance(ev, dict):
        return None
    if "sessionUpdate" in ev or "role" in ev or "type" in ev:
        return ev
    params = ev.get("params")
    if isinstance(params, dict):
        update = params.get("update")
        if isinstance(update, dict):
            return update
    return ev


def _grok_event_role_text(ev):
    """(role, text) for one line of a Grok ACP updates.jsonl or a raw
    chat_history.jsonl — role is 'user' | 'assistant' | 'tool' | ''.
    Unknown shapes return ('', '') and are skipped by callers.

    The envelope is unwrapped first, then `sessionUpdate`, `role`, or `type`
    is used to decide the role.  `chat_history.jsonl` uses `type` (not
    `role`) in real Grok Build output, so mapping `type` is required for the
    fallback transcript to load at all."""
    if not isinstance(ev, dict):
        return "", ""
    ev = _grok_unwrap_acp_event(ev)
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
    role = str(ev.get("role") or ev.get("type") or "").lower()
    if role in ("user", "assistant"):
        return role, _grok_content_text(ev.get("content"))
    if role == "tool_result":
        return "tool", _grok_content_text(ev.get("content"))
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


def _grok_read_subagent_meta(meta_path):
    """Parse one Grok Build ``subagents/<child>/meta.json``, or {}."""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _grok_subagent_parent_map():
    """child_sid -> {parent, name} from variant-A ``subagents/*/meta.json``.

    Grok Build records native spawn_subagent links under
    ``sessions/<cwd-bucket>/<parent>/subagents/<child>/meta.json``. The child
    is also a first-class session dir at the bucket level; this map is the
    parent pointer CCC's session graph and listing rows were missing.
    """
    out = {}
    root = _grok_home() / "sessions"
    try:
        buckets = list(root.iterdir()) if root.is_dir() else []
    except OSError:
        return out
    for bucket in buckets:
        try:
            if not bucket.is_dir():
                continue
            sessions = list(bucket.iterdir())
        except OSError:
            continue
        for session_dir in sessions:
            agents = session_dir / "subagents"
            try:
                if not session_dir.is_dir() or not agents.is_dir():
                    continue
                kids = list(agents.iterdir())
            except OSError:
                continue
            parent_sid = session_dir.name
            for child_dir in kids:
                try:
                    if not child_dir.is_dir():
                        continue
                except OSError:
                    continue
                meta = {}
                meta_path = child_dir / "meta.json"
                try:
                    if meta_path.is_file():
                        meta = _grok_read_subagent_meta(meta_path)
                except OSError:
                    meta = {}
                child_id = str(
                    meta.get("child_session_id")
                    or meta.get("subagent_id")
                    or child_dir.name
                ).strip()
                parent_id = str(meta.get("parent_session_id") or parent_sid).strip()
                if not child_id or not parent_id or child_id == parent_id:
                    continue
                if not _grok_sid_ok(child_id) or not _grok_sid_ok(parent_id):
                    continue
                name = str(
                    meta.get("description") or meta.get("subagent_type") or ""
                ).strip()
                out.setdefault(child_id, {"parent": parent_id, "name": name})
    return out


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
    parent_map = _grok_subagent_parent_map()
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
            if not info:
                continue
            link = parent_map.get(info["id"]) or {}
            info["parent_session_id"] = str(link.get("parent") or "")
            info["subagent_name"] = str(link.get("name") or "")
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
            "parent_session_id": s.get("parent_session_id") or "",
        })
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    return out


def _grok_event_ts(ev):
    """Best-effort ISO 8601 timestamp for a Grok ACP event."""
    raw = ev.get("timestamp") or ev.get("ts") or ev.get("created_at")
    if raw is None:
        return ""
    try:
        epoch = _grok_epoch(raw)
        if epoch:
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        pass
    return str(raw)


def _grok_tool_args_detail(args):
    """Return a human-readable detail string and command (if any) for a
    Grok tool-call rawInput dict."""
    if not isinstance(args, dict):
        s = str(args).strip()
        return (s[:200], s if " " in s or "\n" in s else None)
    for key in ("command", "query", "target_file", "path", "file_path", "description"):
        if args.get(key):
            val = str(args[key]).strip()
            return (val[:200], val if key == "command" else None)
    if args:
        try:
            compact = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            compact = str(args)
        return (compact[:200], None)
    return ("", None)


def _grok_tool_result_text(ev):
    """(text, is_error) for a Grok tool_call_update event."""
    status = str(ev.get("status") or "").lower()
    is_error = bool(ev.get("error")) or status in ("failed", "error")
    error_text = _grok_content_text(ev.get("error"))
    if error_text:
        return error_text[:1600], True
    for key in ("rawOutput", "output", "result", "content"):
        val = ev.get(key)
        if val is None:
            continue
        text = _grok_content_text(val)
        if text:
            return text[:1600], is_error
        # If the value is not text-extractable (e.g. an image payload), show
        # a small placeholder instead of a wall of base64 JSON.
        if isinstance(val, (dict, list)):
            try:
                dumped = json.dumps(val, ensure_ascii=False)
            except (TypeError, ValueError):
                dumped = str(val)
            if "data:image" in dumped or '"type":"image"' in dumped:
                return "[image output]", is_error
            return dumped[:400], is_error
        return str(val)[:400], is_error
    if is_error:
        return "Tool call failed", True
    return "", False


def _grok_hook_summary(ev):
    """Compact human-readable summary of a Grok hook_execution event."""
    event_name = str(ev.get("event_name") or "").strip()
    tool_name = str(ev.get("tool_name") or "").strip()
    runs = ev.get("runs") or []
    ok = 0
    failed = 0
    failed_names = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        status = run.get("status")
        if isinstance(status, dict):
            status = status.get("status")
        name = str(run.get("name") or "").strip()
        if str(status or "").lower() in ("success", "ok"):
            ok += 1
        else:
            failed += 1
            short = name.split(":")[-1].split("[")[0] if name else ""
            if short and short not in failed_names:
                failed_names.append(short)
    parts = []
    if event_name:
        parts.append(event_name)
    if tool_name:
        parts.append(tool_name)
    if ok or failed:
        parts.append(f"{ok} ok, {failed} failed")
    if failed_names:
        parts.append("failed: " + ", ".join(failed_names[:3]))
    return " · ".join(parts)


def _parse_grok_updates_file(path):
    """CCC transcript events from a variant-A updates.jsonl (ACP
    session-update stream). Defensive by design: unknown update kinds are
    skipped and a malformed line never aborts the parse.

    Newer Grok Build wraps each line in a JSON-RPC envelope and interleaves
    `agent_thought_chunk`, `hook_execution`, `image_dropped`, and
    `retry_state` updates alongside tool calls; all of these are surfaced so
    the conversation view matches the terminal."""
    events = []
    line = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    top = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(top, dict):
                    continue
                ev = _grok_unwrap_acp_event(top)
                if not isinstance(ev, dict):
                    continue
                # Carry the envelope timestamp into the inner update when the
                # inner dict doesn't already have one.
                if "timestamp" not in ev and "ts" not in ev:
                    ts_top = top.get("timestamp") or top.get("ts")
                    if ts_top is not None:
                        ev["timestamp"] = ts_top
                kind = str(ev.get("sessionUpdate") or "").lower()
                ts = _grok_event_ts(ev)
                if "user" in kind:
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "user_text",
                        "text": text, "images": [],
                    })
                elif "thought" in kind:
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"grok-{line}",
                        "blocks": [{"kind": "thinking", "text": text}],
                    })
                elif "hook" in kind and "execution" in kind:
                    summary = _grok_hook_summary(ev)
                    if not summary:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "system",
                        "subtype": "grok_hook_execution",
                        "text": summary,
                    })
                elif kind == "image_dropped":
                    notes = ev.get("notes") or []
                    if isinstance(notes, str):
                        notes = [notes]
                    note_text = " ".join(str(n) for n in notes if n).strip()
                    if not note_text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "system",
                        "subtype": "grok_note",
                        "text": note_text,
                    })
                elif kind == "retry_state":
                    reason = str(ev.get("reason") or "").strip()
                    if not reason:
                        continue
                    attempt = ev.get("attempt")
                    max_retries = ev.get("max_retries")
                    label = "Retrying"
                    if attempt is not None and max_retries is not None:
                        label += f" ({attempt}/{max_retries})"
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "system",
                        "subtype": "grok_retry",
                        "text": f"{label}: {reason}",
                    })
                elif "tool" in kind and (
                    "result" in kind or "update" in kind
                    or "complete" in kind or "finish" in kind
                ):
                    result_text, is_error = _grok_tool_result_text(ev)
                    if not result_text and not is_error:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "tool_result",
                        "text": str(result_text)[:1600],
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
                    detail, command = _grok_tool_args_detail(args)
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"grok-{line}",
                        "blocks": [{
                            "kind": "tool_use",
                            "name": name,
                            "detail": detail,
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
                # turn_completed, plan updates, usage signals, and unknown future
                # kinds carry no useful transcript text on their own — skip.
    except OSError:
        pass
    return events, line


def _parse_grok_chat_history_file(path):
    """CCC transcript events from a variant-A chat_history.jsonl fallback
    (raw model messages).  Real Grok Build output uses `type`, not `role`,
    and assistant messages carry `tool_calls`; tool results carry `content`
    and reasoning messages carry a `summary`."""
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
                ev = _grok_unwrap_acp_event(ev)
                if not isinstance(ev, dict):
                    continue
                ts = _grok_event_ts(ev)
                typ = str(ev.get("type") or ev.get("role") or "").lower()
                if typ == "user":
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "user_text",
                        "text": text, "images": [],
                    })
                elif typ == "reasoning":
                    summary = ev.get("summary") or []
                    if isinstance(summary, dict):
                        summary = [summary]
                    text = _grok_content_text(summary).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"grok-{line}",
                        "blocks": [{"kind": "thinking", "text": text}],
                    })
                elif typ == "assistant":
                    blocks = []
                    content_text = _grok_content_text(ev.get("content")).strip()
                    if content_text:
                        blocks.append({"kind": "text", "text": content_text})
                    tool_calls = ev.get("tool_calls") or []
                    if isinstance(tool_calls, dict):
                        tool_calls = [tool_calls]
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        name = str(tc.get("name") or "").strip()
                        args = tc.get("arguments") or tc.get("input") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {"arguments": args}
                        detail, command = _grok_tool_args_detail(args)
                        blocks.append({
                            "kind": "tool_use",
                            "name": name,
                            "detail": detail,
                            "id": str(tc.get("id") or ""),
                            "command": command,
                            "command_kind": None,
                        })
                    if not blocks:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "assistant",
                        "message_id": f"grok-{line}",
                        "blocks": blocks,
                    })
                elif typ == "tool_result":
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    line += 1
                    events.append({
                        "line": line, "ts": ts, "type": "tool_result",
                        "text": text[:1600],
                        "tool_use_id": str(ev.get("tool_call_id") or ev.get("id") or ""),
                        "is_error": False,
                    })
                # system / unknown types carry no transcript text — skip.
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

