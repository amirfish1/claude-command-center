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
import re
import sqlite3
import time
import urllib.request

from ccc_server import core as _core
from ccc_server import dbutil

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
    return dbutil.path_if_exists(_grok_home() / "grok.db")


def _grok_db_connect():
    return dbutil.connect_readonly(_grok_db_path())


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


def _grok_json_file(path):
    """Parse a small JSON file in a session dir; {} on any failure."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# Grok Build reports cost in "ticks": 1e10 costUsdTicks = $1 (documented on
# PromptUsage in xai-grok-shell/src/extensions/notification.rs).
_GROK_COST_TICKS_PER_USD = 1e10


def _extract_grok_usage(session_id):
    """Usage stats for a Grok session.

    Variant-A session dirs carry two sidecars the TUI's status bar reads
    (xai-grok-pager status_blocks.rs): ``usage.json`` — cumulative
    input/output/cache/reasoning tokens, per-turn rows, modelCalls and
    ``costUsdTicks`` (scrubbed/absent when billing was incomplete) — and
    ``signals.json`` — ``contextTokensUsed``/``contextWindowTokens`` for
    the context gauge. ``summary.json`` still supplies model + reasoning
    effort; the variant-B db fallback only gives the model.
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
        summary = _grok_json_file(session_dir / "summary.json")
        result["model"] = str(summary.get("current_model_id") or "")
        if not result["reasoning_effort"]:
            result["reasoning_effort"] = str(summary.get("reasoning_effort") or "")
        usage = _grok_json_file(session_dir / "usage.json")
        totals = usage.get("session") if isinstance(usage.get("session"), dict) else {}
        turns = [t for t in usage.get("turns") or [] if isinstance(t, dict)]
        def _u(d, key):
            try:
                return int(d.get(key) or 0)
            except (TypeError, ValueError):
                return 0
        result["total_input_tokens"] = _u(totals, "inputTokens")
        result["total_output_tokens"] = _u(totals, "outputTokens")
        result["total_cache_read_tokens"] = _u(totals, "cachedReadTokens")
        result["total_cache_creation_tokens"] = _u(totals, "cacheCreationTokens")
        if turns:
            per_turn = [_u(t, "inputTokens") for t in turns]
            result["latest_input_tokens"] = per_turn[-1]
            result["peak_input_tokens"] = max(per_turn)
        ticks = totals.get("costUsdTicks")
        if isinstance(ticks, (int, float)) and ticks > 0:
            result["cost_usd"] = ticks / _GROK_COST_TICKS_PER_USD
        signals = _grok_json_file(session_dir / "signals.json")
        try:
            result["context_limit"] = int(signals.get("contextWindowTokens") or 0)
        except (TypeError, ValueError):
            pass
        try:
            used = int(signals.get("contextTokensUsed") or 0)
            # Current window occupancy beats last-turn input sum for the
            # context gauge (turn input sums count the whole prompt incl.
            # cache reads — they can exceed the window).
            if used:
                result["latest_input_tokens"] = used
        except (TypeError, ValueError):
            pass
        if not result["model"]:
            result["model"] = str(
                totals.get("primaryModelId") or signals.get("primaryModelId") or ""
            )
        return result
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
    signals = {}
    sig_p = session_dir / "signals.json"
    try:
        if sig_p.is_file():
            data = json.loads(sig_p.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                signals = data
    except (OSError, json.JSONDecodeError):
        signals = {}
    # Grok Build writes the display title as generated_title /
    # session_summary (session_summary is the field the TUI's roster shows);
    # `title`/`name` are the older spellings.
    title = str(
        summary.get("title") or summary.get("generated_title")
        or summary.get("session_summary") or summary.get("summary")
        or summary.get("name") or ""
    ).strip()
    model = str(
        summary.get("current_model_id") or summary.get("model")
        or summary.get("modelId") or summary.get("model_id") or ""
    ).strip()
    git_branch = str(summary.get("head_branch") or "").strip()
    reasoning_effort = str(summary.get("reasoning_effort") or "").strip()
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
    if not cwd:
        info = summary.get("info")
        cwd = str(
            (info or {}).get("cwd") if isinstance(info, dict) else ""
        ).strip() or str(summary.get("git_root_dir") or "").strip().rstrip("/")
    tools_used = signals.get("toolsUsed")
    tools_used = {str(t).lower() for t in tools_used} if isinstance(tools_used, list) else set()
    has_edit = bool(tools_used & {
        "search_replace", "write_file", "edit", "multiedit", "write", "patch",
    })
    try:
        has_commit = int(signals.get("gitCommitCount") or 0) > 0
        has_push = int(signals.get("prCreatedCount") or 0) > 0
    except (TypeError, ValueError):
        has_commit = has_push = False
    return {
        "id": session_dir.name,
        "cwd": cwd,
        "title": title,
        "model": model,
        "git_branch": git_branch,
        "reasoning_effort": reasoning_effort,
        "has_edit": has_edit,
        "has_commit": has_commit,
        "has_push": has_push,
        "created": created,
        "updated": updated,
        "archived": False,
        "jsonl_path": str(jsonl) if jsonl else "",
        "size": size,
        "first_user": first_user,
        "last_assistant": last_assistant,
    }


# session_dir -> (version, info-or-None). find_all_sessions runs this scan on
# every sessions-snapshot refresh (every ~2 s while tabs poll) and
# _grok_session_dir_info reads summary.json plus the first 200 transcript
# lines per dir: 0.8 to 0.9 s per call at 112 sessions, all of it repeat
# work. Mine once per (mtime_ns, size) of the files that feed the row.
_GROK_DIR_INFO_CACHE = {}
_GROK_DIR_INFO_FILES = ("summary.json", "updates.jsonl", "chat_history.jsonl",
                        "signals.json")


def _grok_session_dir_version(session_dir):
    parts = []
    for name in _GROK_DIR_INFO_FILES:
        try:
            st = (session_dir / name).stat()
            parts.append((st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append(None)
    return tuple(parts)


def _grok_session_dir_info_cached(session_dir, cwd):
    key = str(session_dir)
    version = (_grok_session_dir_version(session_dir), cwd)
    hit = _GROK_DIR_INFO_CACHE.get(key)
    if hit is None or hit[0] != version:
        hit = (version, _grok_session_dir_info(session_dir, cwd))
        _GROK_DIR_INFO_CACHE[key] = hit
    return dict(hit[1]) if hit[1] else None


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
    seen = set()
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
            seen.add(str(d))
            info = _grok_session_dir_info_cached(d, cwd)
            if not info:
                continue
            link = parent_map.get(info["id"]) or {}
            info["parent_session_id"] = str(link.get("parent") or "")
            info["subagent_name"] = str(link.get("name") or "")
            out.append(info)
    if len(_GROK_DIR_INFO_CACHE) != len(seen):
        for stale in [k for k in _GROK_DIR_INFO_CACHE if k not in seen]:
            _GROK_DIR_INFO_CACHE.pop(stale, None)
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
            "git_branch": s.get("git_branch") or "",
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
            "has_edit": bool(s.get("has_edit")),
            "has_commit": bool(s.get("has_commit")),
            "has_push": bool(s.get("has_push")),
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
            "reasoning_effort": s.get("reasoning_effort") or "",
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
    for key in ("command", "query", "target_file", "target_directory",
                "path", "file_path", "pattern", "url", "prompt",
                "description"):
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


def _grok_tool_meta(ev):
    """The ``_meta["x.ai/tool"]`` dict on a Grok tool_call (name, kind,
    label, namespace, read_only) or {} — Grok Build stamps it so clients can
    show a friendly label instead of the raw function name."""
    meta = ev.get("_meta")
    if not isinstance(meta, dict):
        return {}
    tool = meta.get("x.ai/tool")
    return tool if isinstance(tool, dict) else {}


# ACP tool ``kind`` → the Claude-style display name the client's grouping
# and summaries already understand (mirrors the live ACP path's
# _acp_tool_name). Grok Build kinds: read/edit/delete/move/search/execute/
# fetch/think/other/switch_mode.
_GROK_TOOL_KIND_NAMES = {
    "read": "Read",
    "delete": "Edit",
    "move": "Edit",
    "search": "Grep",
    "execute": "Bash",
    "fetch": "WebFetch",
}

# Spellings Grok uses for its shell tool when the ``kind`` field is absent
# (mirrors is_execute_tool_function_name in xai-grok-pager's acp/tracker.rs).
_GROK_EXECUTE_TOOL_NAMES = frozenset({
    "run_terminal_command", "run_terminal_cmd", "bash", "shell",
    "execute", "run_command", "terminal", "run_shell", "run_bash",
})

# Raw Grok Build function names (chat_history.jsonl rows and older
# updates.jsonl lines carry no ACP ``kind``/``_meta``) → Claude-style names.
_GROK_FUNCTION_NAME_MAP = {
    "read_file": "Read", "view_image": "Read", "list_dir": "Read",
    "search_replace": "Edit", "edit_file": "Edit", "apply_patch": "Edit",
    "write_file": "Write", "multi_edit": "MultiEdit",
    "grep": "Grep", "glob": "Grep", "search_files": "Grep",
    "web_search": "WebSearch", "x_search": "WebSearch",
    "web_fetch": "WebFetch", "fetch_url": "WebFetch",
}

# Tool calls the TUI never puts in scrollback — they drive its todo/tasks/
# goal panes instead. Mirrors the is_*_tool family in xai-grok-pager's
# acp/tracker.rs (all historical spellings included so replayed sessions
# from older builds stay clean).
_GROK_PLUMBING_TITLES = frozenset({
    # todo pane
    "todo_write", "todowrite", "updating plan",
    # subagent spawn — the subagent_spawned update carries the row
    "task", "spawn_subagent",
    # goal tracker
    "update_goal", "workflow",
    # background-task plumbing
    "get_command_or_subagent_output", "kill_command_or_subagent",
    "wait_commands_or_subagents",
    "get_task_output", "kill_task", "wait_tasks",
    "get_task_or_subagent_output", "kill_task_or_subagent",
    "wait_tasks_or_subagents",
    "awaitshell", "await",
})
_GROK_PLUMBING_PREFIXES = (
    "await:", "sleep ", "wait tasks:", "kill task:", "goal:", "scheduler_",
)
_GROK_PLUMBING_VARIANTS = frozenset({
    "todowrite", "task", "updategoal", "workflowsignal",
    "taskoutput", "killtask", "waittasks",
})


def _grok_tool_is_plumbing(title, kind, raw_input):
    """True for Grok Build internal plumbing tools the TUI keeps out of
    scrollback (they drive the todo/tasks/goal panes and subagent blocks
    instead). Mirrors xai-grok-pager acp/tracker.rs is_*_tool checks."""
    title_l = str(title or "").strip()
    inp = raw_input if isinstance(raw_input, dict) else {}
    variant = inp.get("variant")
    variant_l = variant.lower() if isinstance(variant, str) else ""
    if variant_l in _GROK_PLUMBING_VARIANTS or variant_l.startswith("scheduler"):
        return True
    if variant_l == "workflow" or title_l.lower() == "workflow":
        # validate_only calls stay visible — they don't mutate anything.
        validate_only = (
            title_l.startswith("Validating workflow")
            or bool(inp.get("validate_only"))
        )
        return not validate_only
    if title_l.lower() in _GROK_PLUMBING_TITLES:
        return True
    if title_l.lower().startswith(_GROK_PLUMBING_PREFIXES):
        return True
    # A bash call with is_background=true defers to the bg-task surface;
    # the task_backgrounded update is the row that should exist.
    looks_execute = (
        str(kind or "").lower() == "execute"
        or title_l.lower() in _GROK_EXECUTE_TOOL_NAMES
    )
    if looks_execute and (inp.get("is_background") is True
                          or inp.get("background") is True):
        return True
    return False


def _grok_tool_display_name(title, kind, meta):
    """Stable display name for a Grok tool call: ACP ``kind`` → Claude-style
    name first (client grouping/summaries key on these), then the x.ai meta
    label ("Read"), then the raw title."""
    k = str(kind or "").lower()
    t = str(title or "").strip()
    if k == "edit":
        return "Write" if t.lower().startswith("write") else "Edit"
    if k == "execute" or (not k and t.lower() in _GROK_EXECUTE_TOOL_NAMES):
        return "Bash"
    mapped = _GROK_TOOL_KIND_NAMES.get(k)
    if mapped:
        return mapped
    mapped = _GROK_FUNCTION_NAME_MAP.get(t.lower())
    if mapped:
        return mapped
    label = str((meta or {}).get("label") or "").strip()
    if label:
        return label
    # No stable category resolved — callers keep the raw title (create) or
    # the existing name (update); never promote a humanized update title
    # like "Read `/x`" into the name slot.
    return ""


def _grok_title_detail(name, title):
    """Humanized detail from an update's display title ("Read `/x/y`" →
    "/x/y" when the row already says "Read")."""
    t = str(title or "").strip().replace("`", "")
    if not t:
        return ""
    if name and t.lower().startswith(name.lower() + " "):
        t = t[len(name) + 1:].strip()
    return t[:200]


def _grok_tool_content_diff(ev):
    """First {type:'diff', path, oldText, newText} content block on a tool
    call/update — ACP edit calls carry the change inline (same shape the
    live ACP path extracts via _acp_tool_content_diff)."""
    for c in ev.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "diff":
            old = c.get("oldText")
            new = c.get("newText")
            return {
                "path": str(c.get("path") or ""),
                "oldText": old if isinstance(old, str) else "",
                "newText": new if isinstance(new, str) else "",
            }
    return None


def _grok_new_tool_block(ev):
    """(block, merge_state) for a Grok tool_call — or (None, None) when the
    call is internal plumbing the TUI suppresses. ``merge_state`` holds the
    fields a later tool_call_update can override (title, kind, meta,
    rawInput); the wire block itself stays clean."""
    title = str(
        ev.get("title") or ev.get("name") or ev.get("toolName") or ""
    ).strip()
    kind = str(ev.get("kind") or "")
    meta = _grok_tool_meta(ev)
    raw_input = (
        ev.get("rawInput") or ev.get("input") or ev.get("arguments") or {}
    )
    if not isinstance(raw_input, dict):
        raw_input = {}
    if _grok_tool_is_plumbing(title, kind or meta.get("kind"), raw_input):
        return None, None
    name = _grok_tool_display_name(title, kind, meta) or title or "tool"
    detail, command = _grok_tool_args_detail(raw_input)
    if not detail:
        detail = _grok_title_detail(name, title)
    block = {
        "kind": "tool_use",
        "name": name,
        "detail": detail,
        "id": str(ev.get("toolCallId") or ev.get("id") or ""),
        "command": command,
        "command_kind": None,
    }
    status = str(ev.get("status") or "").lower()
    if status in ("completed", "failed"):
        block["tool_status"] = status
    if raw_input:
        block["has_input"] = True
        block["input"] = _core._tool_input_payload(raw_input)
    diff = _grok_tool_content_diff(ev)
    if diff and name in ("Edit", "Write"):
        block["edit_input"] = {
            "old_string": diff["oldText"], "new_string": diff["newText"],
        }
    state = {"title": title, "kind": kind, "meta": meta, "raw_input": raw_input}
    return block, state


def _grok_apply_tool_update(state, block, ev):
    """Fold one tool_call_update into an emitted tool_use block (the TUI's
    merge_tool_call_update: update fields win over the create's)."""
    title = str(ev.get("title") or "").strip()
    kind = str(ev.get("kind") or "")
    if title:
        state["title"] = title
    if kind:
        state["kind"] = kind
    raw_input = ev.get("rawInput") or ev.get("input")
    if isinstance(raw_input, dict) and raw_input:
        state["raw_input"] = raw_input
    meta = _grok_tool_meta(ev) or state.get("meta") or {}
    status = str(ev.get("status") or "").lower()
    if status in ("completed", "failed"):
        block["tool_status"] = status
    name = _grok_tool_display_name(state["title"], state["kind"], meta)
    if name:
        block["name"] = name
    detail, command = _grok_tool_args_detail(state["raw_input"])
    if not detail:
        detail = _grok_title_detail(block["name"], state["title"])
    if detail:
        block["detail"] = detail
    if command:
        block["command"] = command
    if state["raw_input"]:
        block["has_input"] = True
        block["input"] = _core._tool_input_payload(state["raw_input"])
    diff = _grok_tool_content_diff(ev)
    if diff and block["name"] in ("Edit", "Write"):
        block["edit_input"] = {
            "old_string": diff["oldText"], "new_string": diff["newText"],
        }


def _grok_failed_hook_lines(ev):
    """One scrollback line per FAILED hook run — successes leave no trace,
    matching the TUI (xai-grok-pager failed_hook_line: "{event} hook ({name})
    failed, ignored: {error}"). `blocked` runs are denies the shell already
    annotates; skipped runs render nothing."""
    event_name = str(ev.get("event_name") or "").strip()
    lines = []
    for run in ev.get("runs") or []:
        if not isinstance(run, dict):
            continue
        status = run.get("status")
        if not isinstance(status, dict):
            continue
        if str(status.get("status") or "").lower() != "failed" or status.get("blocked"):
            continue
        name = str(run.get("name") or "").strip()
        short = name.split(":")[-1].split("[")[0].strip() if name else ""
        subject = f"{event_name} hook ({short})" if short else f"{event_name} hook"
        err = str(status.get("error") or "").splitlines()[0].strip() if status.get("error") else ""
        lines.append(f"{subject} failed, ignored" + (f": {err}" if err else ""))
    return lines


_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?<>=]*[a-zA-Z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)")


def _grok_decode_bytes(val):
    """Decode a Grok byte-array output field ([84, 114, ...] → text),
    stripping ANSI escapes — CLI tools colorize their stdout and CCC renders
    tool output as plain text."""
    if not (isinstance(val, list) and val):
        return ""
    if not all(isinstance(b, int) for b in val[:64]):
        return ""
    try:
        text = bytes(val).decode("utf-8", "replace")
    except (ValueError, OverflowError):
        return ""
    return _ANSI_OSC_RE.sub("", _ANSI_CSI_RE.sub("", text))


def _grok_raw_output_text(raw):
    """(text, is_error) for a Grok `rawOutput` envelope — the typed payloads
    look like {"type":"Bash","output":[bytes],"exit_code":0} or
    {"type":"ReadFile","FileContent":{"content":"..."}}. Unknown shapes fall
    back to compact JSON."""
    if isinstance(raw, str):
        return _ANSI_OSC_RE.sub("", _ANSI_CSI_RE.sub("", raw)), False
    if isinstance(raw, list):
        return _grok_decode_bytes(raw), False
    if not isinstance(raw, dict):
        return "", False
    is_error = False
    ec = raw.get("exit_code")
    if isinstance(ec, int) and ec != 0:
        is_error = True
    # Flat output fields first — {"type":"Bash","output":[bytes],...} keeps
    # them at the top level, next to scalar metadata like command/exit_code.
    for k in ("output", "stdout", "stderr"):
        text = _grok_decode_bytes(raw.get(k))
        if text.strip():
            return text, is_error
    # Typed variant payload: the sibling key of "type" (FileContent,
    # FileNotFound, EditsApplied, TodosUpdated, ...). Dicts carry fields;
    # bare strings are error messages.
    scalars = {"type", "command", "description", "current_dir", "workdir",
               "exit_code", "file_matches", "match_count", "output",
               "stdout", "stderr"}
    payload = None
    for k, v in raw.items():
        if k not in scalars:
            payload = v
            break
    if isinstance(payload, str):
        return payload, True
    if isinstance(payload, dict):
        for k in ("content", "summary_for_prompt", "text", "message"):
            if isinstance(payload.get(k), str) and payload[k].strip():
                return payload[k], is_error
        for k in ("output", "stdout", "stderr"):
            text = _grok_decode_bytes(payload.get(k))
            if text.strip():
                return text, is_error
        if {"old_string", "new_string"} <= set(payload):
            return "(edit applied)", is_error
    return "", is_error


def _grok_tool_result_text(ev):
    """(text, is_error) for a Grok tool_call_update event."""
    status = str(ev.get("status") or "").lower()
    is_error = bool(ev.get("error")) or status in ("failed", "error")
    error_text = _grok_content_text(ev.get("error"))
    if error_text:
        return error_text[:1600], True
    raw = ev.get("rawOutput")
    if raw is not None:
        text, raw_err = _grok_raw_output_text(raw)
        if text:
            return text[:1600], is_error or raw_err
        if raw_err:
            return "Tool call failed", True
    for key in ("output", "result", "content"):
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


def _grok_duration_s(ms):
    """'1m 15s'-style duration from milliseconds, '' when absent/invalid."""
    try:
        s = int(float(ms) / 1000.0)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def _grok_plan_entries(ev):
    """Normalized plan entries from a Grok `plan` update (whole-list
    replace, last-wins — same wire shape as ACP plan snapshots)."""
    entries = ev.get("entries")
    if not isinstance(entries, list):
        return []
    norm = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        content = str(e.get("content") or "").strip()
        if not content:
            continue
        norm.append({
            "content": content[:300],
            "status": str(e.get("status") or "pending"),
            "priority": str(e.get("priority") or ""),
        })
    return norm


def _grok_subagent_text(ev, finished=False):
    """One-line summary for subagent_spawned / subagent_finished."""
    if not finished:
        desc = str(ev.get("description") or "").strip()
        stype = str(ev.get("subagent_type") or "").strip()
        model = str(ev.get("model") or "").strip()
        tail = ", ".join(p for p in (stype, model) if p)
        return f"Subagent spawned: {desc or 'subagent'}" + (f" ({tail})" if tail else "")
    status = str(ev.get("status") or "finished").strip()
    stats = []
    try:
        n = int(ev.get("tool_calls") or 0)
        if n:
            stats.append(f"{n} tool call{'s' if n != 1 else ''}")
    except (TypeError, ValueError):
        pass
    try:
        n = int(ev.get("turns") or 0)
        if n:
            stats.append(f"{n} turn{'s' if n != 1 else ''}")
    except (TypeError, ValueError):
        pass
    dur = _grok_duration_s(ev.get("duration_ms"))
    if dur:
        stats.append(dur)
    err = str(ev.get("error") or "").strip()
    text = f"Subagent {status}"
    if stats:
        text += " — " + ", ".join(stats)
    if err:
        text += f": {err[:160]}"
    return text


def _parse_grok_updates_file(path):
    """CCC transcript events from a variant-A updates.jsonl (ACP
    session-update stream). Defensive by design: unknown update kinds are
    skipped and a malformed line never aborts the parse.

    Rendering mirrors the Grok Build TUI (xai-org/grok-build,
    xai-grok-pager's acp/tracker.rs + acp_handler/session_notification.rs):

    * tool_call + tool_call_update merge into ONE row per toolCallId — the
      update's humanized title ("Read `/path`") and terminal status fold
      into the emitted block; the completing update also emits a
      tool_result so output folds under the row and live tails still land.
    * Internal plumbing tools (todo/task/goal/scheduler/workflow/bg) never
      hit scrollback — the TUI routes them to dedicated panes.
    * hook_execution renders ONLY failed runs ("… failed, ignored: err");
      a batch of green hooks is spinner state, not transcript.
    * plan updates render as plan cards (deduped — the wire is last-wins),
      subagent/task/compaction lifecycle as compact system rows.
    * turn_completed carries token/cost usage — it feeds usage.json and
      _extract_grok_usage, not the transcript.
    """
    events = []
    line = 0
    tools = {}          # toolCallId -> {"event": dict, "suppressed": bool}
    orphan_updates = {}  # toolCallId -> [update dicts] seen before create
    prompt_index = {}    # prompt_id -> ordinal (for rewind markers)
    prev_plan_key = None
    prev_goal_key = None

    def _pidx(ev):
        pid = ev.get("prompt_id") or ev.get("promptId")
        if not pid:
            return None
        pid = str(pid)
        if pid not in prompt_index:
            prompt_index[pid] = len(prompt_index)
        return prompt_index[pid]

    def _emit(ev_dict, ts, pidx=None):
        nonlocal line
        line += 1
        ev_dict["line"] = line
        ev_dict["ts"] = ts
        if pidx is not None:
            ev_dict["_pidx"] = pidx
        events.append(ev_dict)

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
                pidx = _pidx(ev)
                if "user" in kind:
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    _emit({
                        "type": "user_text", "text": text, "images": [],
                    }, ts, pidx)
                elif "thought" in kind:
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    _emit({
                        "type": "assistant", "message_id": f"grok-{line + 1}",
                        "blocks": [{"kind": "thinking", "text": text}],
                    }, ts, pidx)
                elif "hook" in kind and "execution" in kind:
                    # TUI: one line per failed run, nothing for all-green.
                    for fail_line in _grok_failed_hook_lines(ev):
                        _emit({
                            "type": "system", "subtype": "grok_hook_execution",
                            "text": fail_line,
                        }, ts, pidx)
                elif kind == "image_dropped":
                    notes = ev.get("notes") or []
                    if isinstance(notes, str):
                        notes = [notes]
                    note_text = " ".join(str(n) for n in notes if n).strip()
                    if not note_text:
                        continue
                    _emit({
                        "type": "system", "subtype": "grok_note",
                        "text": note_text,
                    }, ts, pidx)
                elif kind == "retry_state":
                    reason = str(ev.get("reason") or "").strip()
                    if not reason:
                        continue
                    attempt = ev.get("attempt")
                    max_retries = ev.get("max_retries")
                    label = "Retrying"
                    if attempt is not None and max_retries is not None:
                        label += f" ({attempt}/{max_retries})"
                    _emit({
                        "type": "system", "subtype": "grok_retry",
                        "text": f"{label}: {reason}",
                    }, ts, pidx)
                elif kind == "plan":
                    norm = _grok_plan_entries(ev)
                    if not norm:
                        continue
                    key = json.dumps(norm, sort_keys=True)
                    if key == prev_plan_key:
                        continue
                    prev_plan_key = key
                    _emit({
                        "type": "assistant", "message_id": f"grok-{line + 1}",
                        "blocks": [{"kind": "plan", "entries": norm}],
                    }, ts, pidx)
                elif kind == "tool_call_update":
                    tid = str(ev.get("toolCallId") or ev.get("id") or "")
                    if not tid:
                        continue
                    tool = tools.get(tid)
                    if tool is None:
                        # Update before its create — stash for the merge the
                        # TUI performs when the late tool_call lands.
                        orphan_updates.setdefault(tid, []).append(ev)
                        continue
                    if tool.get("suppressed"):
                        continue
                    _grok_apply_tool_update(tool["state"], tool["block"], ev)
                    status = str(ev.get("status") or "").lower()
                    if status not in ("completed", "failed"):
                        continue
                    result_text, is_error = _grok_tool_result_text(ev)
                    if status == "failed":
                        is_error = True
                    if not result_text and not is_error:
                        continue
                    _emit({
                        "type": "tool_result",
                        "text": str(result_text)[:1600],
                        "tool_use_id": tid,
                        "is_error": is_error,
                    }, ts, pidx)
                elif kind == "tool_call":
                    tid = str(ev.get("toolCallId") or ev.get("id") or "")
                    block, state = _grok_new_tool_block(ev)
                    if block is None:
                        if tid:
                            tools[tid] = {"suppressed": True}
                        continue
                    for orphan in orphan_updates.pop(tid, []):
                        _grok_apply_tool_update(state, block, orphan)
                    ev_dict = {
                        "type": "assistant", "message_id": f"grok-{line + 1}",
                        "blocks": [block],
                    }
                    _emit(ev_dict, ts, pidx)
                    if tid:
                        tools[tid] = {
                            "event": ev_dict, "block": block, "state": state,
                        }
                elif kind == "subagent_spawned":
                    _emit({
                        "type": "system", "subtype": "grok_subagent",
                        "text": _grok_subagent_text(ev),
                    }, ts, pidx)
                elif kind == "subagent_finished":
                    _emit({
                        "type": "system", "subtype": "grok_subagent",
                        "text": _grok_subagent_text(ev, finished=True),
                    }, ts, pidx)
                elif kind == "task_backgrounded":
                    desc = str(ev.get("description") or "").strip()
                    cmd = str(ev.get("command") or "").splitlines()[0].strip()
                    detail = desc or cmd
                    _emit({
                        "type": "system", "subtype": "grok_task",
                        "text": "Backgrounded"
                        + (f": {detail[:160]}" if detail else " command"),
                    }, ts, pidx)
                elif kind == "task_completed":
                    snap = ev.get("task_snapshot")
                    snap = snap if isinstance(snap, dict) else {}
                    desc = str(
                        snap.get("description") or snap.get("command") or ""
                    ).splitlines()[0].strip()
                    _emit({
                        "type": "system", "subtype": "grok_task",
                        "text": "Task completed"
                        + (f": {desc[:160]}" if desc else ""),
                    }, ts, pidx)
                elif kind == "background_tasks":
                    tasks = ev.get("tasks")
                    n = len(tasks) if isinstance(tasks, list) else 0
                    if not n:
                        continue
                    _emit({
                        "type": "system", "subtype": "grok_task",
                        "text": f"{n} background task{'s' if n != 1 else ''} running",
                    }, ts, pidx)
                elif kind == "session_recap":
                    summary = str(ev.get("summary") or "").strip()
                    if not summary:
                        continue
                    _emit({
                        "type": "system", "subtype": "grok_recap",
                        "text": summary[:400],
                    }, ts, pidx)
                elif kind == "current_mode_update":
                    mode = str(ev.get("currentModeId") or "").strip()
                    if not mode:
                        continue
                    _emit({
                        "type": "system", "subtype": "grok_mode",
                        "text": f"Mode: {mode}",
                    }, ts, pidx)
                elif kind == "goal_updated":
                    objective = str(ev.get("objective") or "").strip()
                    if not objective:
                        continue
                    key = json.dumps([
                        objective,
                        ev.get("status"), ev.get("phase"),
                        ev.get("completed_deliverables"),
                        ev.get("total_deliverables"),
                    ])
                    if key == prev_goal_key:
                        continue
                    prev_goal_key = key
                    status = str(ev.get("status") or "").strip()
                    phase = str(ev.get("phase") or "").strip()
                    label = " · ".join(p for p in (status, phase) if p)
                    _emit({
                        "type": "system", "subtype": "grok_goal",
                        "text": f"Goal{(' ' + label) if label else ''}: {objective[:160]}",
                    }, ts, pidx)
                elif kind == "model_auto_switched":
                    prev = str(ev.get("previous_model_id") or "").strip()
                    new = str(ev.get("new_model_id") or "").strip()
                    reason = str(ev.get("reason") or "").strip()
                    _emit({
                        "type": "system", "subtype": "grok_mode",
                        "text": f"Model switched: {prev or '?'} → {new or '?'}"
                        + (f" ({reason[:120]})" if reason else ""),
                    }, ts, pidx)
                elif kind == "turn_completed":
                    # Usage lives in usage.json (fed to _extract_grok_usage);
                    # the transcript only marks abnormal turn ends, like the
                    # TUI's stop_cancelled marker.
                    stop = str(ev.get("stop_reason") or "").strip().lower()
                    if stop and stop not in ("end_turn", "stop", "stop_sequence",
                                             "completed", "success"):
                        dur = _grok_duration_s(ev.get("elapsed_ms"))
                        _emit({
                            "type": "system", "subtype": "grok_turn_end",
                            "text": f"Turn {stop}" + (f" after {dur}" if dur else ""),
                        }, ts, pidx)
                elif kind == "auto_compact_started":
                    pct = ev.get("percentage")
                    reason = str(ev.get("reason") or "").strip()
                    _emit({
                        "type": "system", "subtype": "grok_compact",
                        "text": "Compacting context"
                        + (f" ({pct}% full)" if pct is not None else "")
                        + (f" — {reason[:120]}" if reason else ""),
                    }, ts, pidx)
                elif kind == "auto_compact_completed":
                    before = ev.get("tokens_before")
                    after = ev.get("tokens_after")
                    span = ""
                    if before is not None and after is not None:
                        span = f" ({before}→{after} tokens)"
                    _emit({
                        "type": "system", "subtype": "grok_compact",
                        "text": f"Context compacted{span}",
                    }, ts, pidx)
                elif kind == "auto_compact_failed":
                    err = str(ev.get("error") or "").strip()
                    _emit({
                        "type": "system", "subtype": "grok_compact",
                        "text": "Context compaction failed"
                        + (f": {err[:160]}" if err else ""),
                    }, ts, pidx)
                elif kind == "compaction_checkpoint":
                    _emit({
                        "type": "system", "subtype": "grok_compact",
                        "text": "Context compacted",
                    }, ts, pidx)
                elif kind == "auto_recovery_started":
                    attempt = ev.get("attempt")
                    maxr = ev.get("max_retries")
                    err = str(ev.get("error") or "").strip()
                    label = "Auto-recovery"
                    if attempt is not None and maxr is not None:
                        label += f" ({attempt}/{maxr})"
                    _emit({
                        "type": "system", "subtype": "grok_retry",
                        "text": label + (f": {err[:160]}" if err else ""),
                    }, ts, pidx)
                elif kind == "auto_recovery_exhausted":
                    err = str(ev.get("error") or "").strip()
                    _emit({
                        "type": "system", "subtype": "grok_retry",
                        "text": "Auto-recovery exhausted"
                        + (f": {err[:160]}" if err else ""),
                    }, ts, pidx)
                elif kind == "scheduled_task_created":
                    sched = str(ev.get("human_schedule") or "").strip()
                    prompt = str(ev.get("prompt") or "").strip()
                    _emit({
                        "type": "system", "subtype": "grok_task",
                        "text": "Scheduled task"
                        + (f" ({sched})" if sched else "")
                        + (f": {prompt[:120]}" if prompt else ""),
                    }, ts, pidx)
                elif kind == "scheduled_task_fired":
                    prompt = str(ev.get("prompt") or "").strip()
                    _emit({
                        "type": "system", "subtype": "grok_task",
                        "text": "Scheduled task fired"
                        + (f": {prompt[:120]}" if prompt else ""),
                    }, ts, pidx)
                elif kind == "scheduled_task_deleted":
                    _emit({
                        "type": "system", "subtype": "grok_task",
                        "text": "Scheduled task removed",
                    }, ts, pidx)
                elif kind == "rewind_marker":
                    # updates.jsonl is append-only: rewinds branch the
                    # timeline — drop events past target_prompt_index (the
                    # TUI's replay does the same) and mark the boundary.
                    try:
                        target = int(ev.get("target_prompt_index"))
                    except (TypeError, ValueError):
                        continue
                    kept = [
                        e for e in events
                        if e.get("_pidx") is None or e["_pidx"] <= target
                    ]
                    if len(kept) != len(events):
                        kept_ids = {id(e) for e in kept}
                        for t in tools.values():
                            tev = t.get("event")
                            if tev is not None and id(tev) not in kept_ids:
                                t["suppressed"] = True
                        events[:] = kept
                        for pid in [p for p, i in prompt_index.items() if i > target]:
                            prompt_index.pop(pid, None)
                    _emit({
                        "type": "system", "subtype": "grok_rewind",
                        "text": f"Session rewound to prompt {target}",
                    }, ts, pidx)
                elif kind == "diff_review":
                    content = ev.get("content")
                    n = len(content) if isinstance(content, list) else 0
                    _emit({
                        "type": "system", "subtype": "grok_review",
                        "text": f"Diff review requested ({n} file{'s' if n != 1 else ''})",
                    }, ts, pidx)
                elif "agent" in kind or "assistant" in kind or "message" in kind:
                    text = _grok_content_text(ev.get("content")).strip()
                    if not text:
                        continue
                    _emit({
                        "type": "assistant", "message_id": f"grok-{line + 1}",
                        "blocks": [{"kind": "text", "text": text}],
                    }, ts, pidx)
                # Persist-only / status-plane kinds intentionally skipped:
                # hook_run_started and hook batches' successes are spinner
                # state; subagent_progress is rate-limited; session_status,
                # last_turn_summary, usage_update, session_info_update,
                # available_commands_update, config_option_update feed the
                # status bar; memory_*, hooks_changed, plugins_changed,
                # feedback_request, relay_sync_status, monitor_event,
                # session_recap_unavailable, auto_compact_cancelled and any
                # unknown future kinds carry no transcript text of their own.
    except OSError:
        pass
    for e in events:
        e.pop("_pidx", None)
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
                        args = tc.get("arguments") or tc.get("input") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {"arguments": args}
                        block, _state = _grok_new_tool_block({
                            "title": str(tc.get("name") or "").strip(),
                            "rawInput": args,
                            "toolCallId": str(tc.get("id") or ""),
                        })
                        if block is not None:
                            blocks.append(block)
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

