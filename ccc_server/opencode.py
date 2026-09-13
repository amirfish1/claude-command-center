# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""OpenCode engine adapter (session ingestion + CLI resolution).

Follows ccc_server/kilo.py — Kilo Code is OpenCode-derived and both keep
sessions in a SQLite store with the same session / message / part shape.
Names still living in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import shutil
import sqlite3
import subprocess
import time

from ccc_server import core as _core
from ccc_server import dbutil

# ---------------------------------------------------------------------------
# OpenCode session ingestion (read-only).
#
# OpenCode (the SST/anomalyco CLI) keeps its sessions in a SQLite DB at
# ~/.local/share/opencode/opencode.db (override: $OPENCODE_DB) with tables
# session / message / part. Reading it lets an externally-launched `opencode`
# appear on the board like a Claude or Codex session. The DB is WAL-mode and
# written live by the opencode daemon; a read-only-URI open can silently miss
# un-checkpointed WAL frames, so we open the file normally and immediately set
# PRAGMA query_only=1 — the standard WAL multi-reader path, which still cannot
# write to OpenCode's DB.
# ---------------------------------------------------------------------------

OPENCODE_LIVE_WINDOW_S = 180


def _opencode_db_path():
    env_db = (os.environ.get("OPENCODE_DB") or "").strip()
    if env_db:
        p = Path(env_db).expanduser()
    else:
        p = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
    return dbutil.path_if_exists(p)


def _opencode_connect():
    return dbutil.connect_readonly(_opencode_db_path())


def _resolve_opencode_bin():
    """Locate a usable OpenCode CLI binary.

    Priority order:
      1. $CCC_OPENCODE_BIN (env override) — if set and executable.
      2. `shutil.which("opencode")` — picks up Homebrew / npm-global.

    Returns a dict so the caller and the availability endpoint can share
    one shape:
      {available: True,  bin: "<abs path>", source: "env|path"}
      {available: False, reason: "<human readable>", bin: None}
    """
    env_bin = os.environ.get("CCC_OPENCODE_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return {"available": True, "bin": env_bin, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "opencode_unavailable",
            "reason": f"CCC_OPENCODE_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("opencode")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    return {
        "available": False,
        "bin": None,
        "code": "opencode_unavailable",
        "reason": (
            "OpenCode CLI not found. Install OpenCode, "
            "`npm install -g opencode-ai`, or set CCC_OPENCODE_BIN."
        ),
    }


def _opencode_model_str(raw):
    """OpenCode stores model as JSON {id, providerID, variant}; render a string."""
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


def _opencode_fetch_sessions(con, limit=None):
    """Return one dict per row of OpenCode's `session` table, newest first."""
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
                "model": _opencode_model_str(d.get("model")),
                "agent": d.get("agent") or "",
                "created": (d.get("time_created") or 0) / 1000.0,
                "updated": (d.get("time_updated") or 0) / 1000.0,
                "archived": bool(d.get("time_archived")),
            })
    except sqlite3.Error:
        return out
    return out


def _opencode_first_user_text(con, sid):
    """First user-message text for an OpenCode session (card preview)."""
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


def _opencode_last_assistant_text(con, sid):
    """Last assistant text-part for an OpenCode session (card subtitle/state)."""
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


def find_opencode_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    """Discover external OpenCode sessions from the opencode SQLite store."""
    con = _opencode_connect()
    if con is None:
        return []
    try:
        sessions = _opencode_fetch_sessions(con, limit=limit)
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
                _opencode_first_user_text(con, sid)
            ).strip()
            # OpenCode titles untouched conversations "New session - <iso>";
            # treat those as not-AI-summarised so the ✨ glyph doesn't show.
            opencode_ai_title = title if (title and not title.startswith("New session")) else None
            display_name = (
                name_overrides.get(sid)
                or _core._truncate_session_name(title if opencode_ai_title else "")
                or (first_message[:80] if first_message else None)
                or (title[:80] if title else "OpenCode session")
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
                folder_label = "OpenCode"
            _wt_worktree_label = None
            _wt_idx = folder_label.find("-wt-")
            if _wt_idx > 0:
                _wt_worktree_label = folder_label[_wt_idx + 4:]
                folder_label = folder_label[:_wt_idx]
            last_assistant_text = _opencode_last_assistant_text(con, sid)
            is_live = (now - modified) < OPENCODE_LIVE_WINDOW_S
            out.append({
                "id": sid,
                "session_id": sid,
                "source": "opencode",
                "engine": "opencode",
                "timestamp": "",
                "branch": "",
                "git_branch": "",
                "first_message": first_message[:200],
                "display_name": display_name,
                "ai_title": opencode_ai_title,
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


def _is_opencode_session(session_id):
    """Check if session_id corresponds to an OpenCode session.

    Matches both sessions CCC spawned (in-memory registry) and external
    sessions discovered in OpenCode's on-disk SQLite store — without the DB
    probe a historical or terminal-launched OpenCode session would be
    misclassified as Claude and routed to the wrong transcript parser. Kilo
    session ids share the `ses_` prefix, so the prefix alone is never enough:
    the id must exist in THIS engine's DB.
    """
    for s in _core._spawned_sessions:
        if s.get("engine") == "opencode" and (
            s.get("session_id") == session_id
            or s.get("resumed_sid") == session_id
            or s.get("name") == session_id
        ):
            return True
    if isinstance(session_id, str) and session_id.startswith("ses_"):
        con = _opencode_connect()
        if con is not None:
            try:
                row = con.execute(
                    "SELECT 1 FROM session WHERE id=? LIMIT 1", (session_id,)
                ).fetchone()
                if row:
                    return True
            except sqlite3.Error:
                pass
            finally:
                con.close()
    return False


def _opencode_session_cwd(session_id):
    """Return the session's working directory from the OpenCode DB, or None."""
    if not session_id:
        return None
    con = _opencode_connect()
    if con is None:
        return None
    try:
        row = con.execute(
            "SELECT directory FROM session WHERE id=? LIMIT 1", (session_id,)
        ).fetchone()
        if row and row["directory"]:
            return row["directory"]
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return None


def _parse_opencode_conversation(session_id, after_line=0):
    """Build a CCC transcript event list from an OpenCode session's parts."""
    con = _opencode_connect()
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
                        "message_id": f"opencode-{line}", "blocks": blocks,
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



def resume_session_opencode(session_id, text):
    """Resume an OpenCode session with a one-shot follow-up prompt.

    `opencode run --session <id> --auto "<text>"` continues an existing
    session non-interactively with full history; the new turn lands in the
    same SQLite store the transcript view already reads. Follows
    ccc_server/hermes.py's resume_session_hermes shape exactly.
    """
    text = _core._strip_ccc_session_state_instruction(text)
    if not session_id or not text:
        return {"ok": False, "error": "missing session_id or text"}
    resolved = _resolve_opencode_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    for s in _core._spawned_sessions:
        if s.get("engine") == "opencode" and s.get("resumed_sid") == session_id:
            try:
                if _core._poll_spawn_entry(s) is None:
                    queued = _core._apply_pending_input_operations(session_id, [{
                        "field": "resume", "action": "append_tail", "value": text,
                    }])
                    if not queued.get("ok"):
                        return {"ok": False, "error": "failed to persist queued OpenCode input"}
                    return {
                        "ok": True,
                        "queued": True,
                        "pid": s.get("pid"),
                        "via": "opencode-resume-queued",
                        "queued_reason": "waiting for the current OpenCode turn to finish",
                        "engine": "opencode",
                    }
            except Exception:
                pass
    spawned_ctx = _core._spawn_registry_entry_for_session(session_id, "opencode") or {}
    cwd = spawned_ctx.get("cwd") or _core.find_session_cwd(session_id) or str(Path.home())
    if not Path(cwd).is_dir():
        cwd = str(Path.home())
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-opencode-{str(session_id)[:8]}-{timestamp}.log"
    repo_for_logs = _core._git_toplevel_for_existing_dir(cwd) or cwd
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    cmd = [resolved["bin"], "run", "--session", str(session_id), "--auto"]
    # Per-session override (click-to-switch picker) wins; then the model the
    # session was spawned with; then the env-var default. With none known,
    # opencode reuses the session's own model.
    override = _core._get_session_override(session_id)
    model = (override or {}).get("model") if override else None
    if not model:
        model = spawned_ctx.get("model")
    if not model:
        model = os.environ.get("CCC_OPENCODE_MODEL")
    if model:
        cmd.extend(["--model", model])
    cmd.extend([text])
    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "via": "opencode-resume", "engine": "opencode"}
    entry = {
        "pid": proc.pid,
        "name": f"resume-opencode-{str(session_id)[:8]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
        "fifo": None,
        "stdin_fd": None,
        "engine": "opencode",
        "cwd": cwd,
        "repo_path": repo_for_logs,
        "model": model or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=entry["name"],
        log_path=log_path,
        cwd=cwd,
        spawned_at=timestamp,
        command_summary=text[:200],
        fifo=None,
        engine="opencode",
        session_id=session_id,
        repo_path=repo_for_logs,
        model=model,
    )
    return {
        "ok": True,
        "pid": proc.pid,
        "log": str(log_path),
        "resumed": True,
        "via": "opencode-resume",
        "engine": "opencode",
        "model": model or "",
    }


# ---------------------------------------------------------------------------
# OpenCode model catalog (provider/model pricing + limits).
#
# `opencode models --verbose` emits one `provider/id` header followed by a
# compact JSON object per model. We parse that stream into CCC's model-catalog
# shape so the new-session picker can show live per-token pricing, context
# limits, and provider availability from the installed CLI.
# ---------------------------------------------------------------------------

_OPENCODE_MODELS_TTL_S = 300.0
_OPENCODE_MODELS_FILE_TTL_S = 7 * 24 * 3600.0
_OPENCODE_MODELS_TIMEOUT_S = 30.0
_OPENCODE_MODELS_LOCK = None
_OPENCODE_MODELS_CACHE = {"ts": 0.0, "records": []}
_OPENCODE_MODELS_CACHE_FILE = (Path.home() / ".local" / "share" / "opencode" / "ccc-models-cache.json")
_OPENCODE_AUTH_PATH = (Path.home() / ".local" / "share" / "opencode" / "auth.json")


def _opencode_configured_providers():
    """Provider IDs that have a saved API key in OpenCode's auth.json."""
    try:
        raw = _OPENCODE_AUTH_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return set(data.keys()) if isinstance(data, dict) else set()
    except (OSError, ValueError, TypeError):
        return set()


def _opencode_read_cache_file():
    """Load a previously-persisted model list and its timestamp."""
    try:
        raw = _OPENCODE_MODELS_CACHE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        records = data.get("records") if isinstance(data, dict) else data
        fetched_at = (
            data.get("fetched_at", "1970-01-01T00:00:00+00:00")
            if isinstance(data, dict)
            else "1970-01-01T00:00:00+00:00"
        )
        if not isinstance(records, list):
            return None, 0.0
        try:
            dt = datetime.fromisoformat(fetched_at)
            ts = dt.timestamp()
        except (ValueError, TypeError):
            ts = 0.0
        return records, ts
    except (OSError, ValueError, TypeError):
        return None, 0.0


def _opencode_write_cache_file(records):
    """Persist a model list so the launchd service can serve it without shell."""
    try:
        _OPENCODE_MODELS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "records": records,
        }
        tmp = _OPENCODE_MODELS_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, _OPENCODE_MODELS_CACHE_FILE)
    except (OSError, ValueError, TypeError):
        pass


def _opencode_format_cost(cost):
    """Human-readable cost string from a `cost` object.

    OpenCode prices are expressed per-million-input/output tokens, matching the
    same units used by OpenRouter. We keep the summary short because the picker
    already has a separate cost_tier field for sorting.
    """
    if not isinstance(cost, dict):
        return ""
    input_price = cost.get("input")
    output_price = cost.get("output")
    if input_price == 0 and output_price == 0:
        return "free"
    parts = []
    if input_price == 0:
        parts.append("free input")
    elif isinstance(input_price, (int, float)) and input_price > 0:
        parts.append(f"${input_price:.2f} in / 1M")
    if output_price == 0:
        parts.append("free output")
    elif isinstance(output_price, (int, float)) and output_price > 0:
        parts.append(f"${output_price:.2f} out / 1M")
    return ", ".join(parts)


def _opencode_record_from_payload(header, payload, usable_providers):
    """Convert one parsed `opencode models --verbose` JSON block to a catalog row."""
    if not isinstance(payload, dict):
        return None

    mid = payload.get("id") or header
    provider = payload.get("providerID") or ""
    if not provider and "/" in header:
        provider = header.split("/", 1)[0]
    full_id = header if "/" in header else f"{provider}/{mid}" if provider else mid

    name = payload.get("name") or ""
    if not name and "/" in full_id:
        name = full_id.split("/")[-1]
    label = (name or full_id).strip()

    cost = payload.get("cost") or {}
    cost_summary = _opencode_format_cost(cost)
    cost_tier = 0.0
    if isinstance(cost, dict):
        input_price = cost.get("input") or 0
        output_price = cost.get("output") or 0
        if isinstance(input_price, (int, float)) and isinstance(output_price, (int, float)):
            cost_tier = float(input_price) + float(output_price)

    limits = payload.get("limit") or {}
    max_context = None
    max_output = None
    if isinstance(limits, dict):
        max_context = limits.get("context")
        max_output = limits.get("output")

    status = payload.get("status")
    available = status == "active" and (provider == "opencode" or provider in usable_providers)
    availability_reason = None
    if not available:
        if status != "active":
            availability_reason = f"status: {status}"
        elif provider not in usable_providers:
            availability_reason = f"provider {provider} not configured"

    variants = payload.get("variants") or {}
    reasoning_efforts = []
    for key in variants:
        key = str(key).lower()
        if key in ("none", "off", "minimal", "low", "medium", "high", "xhigh", "max"):
            reasoning_efforts.append(key)
    default_reasoning_effort = reasoning_efforts[0] if reasoning_efforts else None

    capabilities = payload.get("capabilities") or {}
    input_caps = capabilities.get("input") or {}
    output_caps = capabilities.get("output") or {}
    supports_vision = bool(capabilities.get("attachment")) or bool(input_caps.get("image"))
    supports_audio = bool(input_caps.get("audio")) or bool(output_caps.get("audio"))
    supports_tool_call = bool(capabilities.get("toolcall"))
    supports_reasoning = bool(capabilities.get("reasoning"))

    return {
        "id": full_id,
        "label": label,
        "source": "opencode-cli",
        "available": available,
        "availability_reason": availability_reason,
        "cost_tier": cost_tier,
        "cost_summary": cost_summary,
        "max_context_tokens": max_context,
        "max_output_tokens": max_output,
        "reasoning_efforts": reasoning_efforts,
        "default_reasoning_effort": default_reasoning_effort,
        "supports_vision": supports_vision,
        "supports_audio": supports_audio,
        "supports_tool_call": supports_tool_call,
        "supports_reasoning": supports_reasoning,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _opencode_model_catalog_records(force_refresh=False):
    """Return OpenCode's live provider/model list with cost and limit metadata."""
    global _OPENCODE_MODELS_LOCK
    import threading

    if _OPENCODE_MODELS_LOCK is None:
        _OPENCODE_MODELS_LOCK = threading.Lock()

    now = time.monotonic()
    with _OPENCODE_MODELS_LOCK:
        if (
            not force_refresh
            and _OPENCODE_MODELS_CACHE["records"]
            and now - _OPENCODE_MODELS_CACHE["ts"] < _OPENCODE_MODELS_TTL_S
        ):
            return list(_OPENCODE_MODELS_CACHE["records"])

    file_records, file_ts = _opencode_read_cache_file()
    if (
        not force_refresh
        and file_records
        and now - file_ts < _OPENCODE_MODELS_FILE_TTL_S
    ):
        with _OPENCODE_MODELS_LOCK:
            _OPENCODE_MODELS_CACHE["ts"] = now
            _OPENCODE_MODELS_CACHE["records"] = file_records
        return list(file_records)

    resolved = _resolve_opencode_bin()
    if not resolved.get("available"):
        with _OPENCODE_MODELS_LOCK:
            if file_records:
                _OPENCODE_MODELS_CACHE["ts"] = now
                _OPENCODE_MODELS_CACHE["records"] = file_records
                return list(file_records)
            return list(_OPENCODE_MODELS_CACHE["records"])

    configured = _opencode_configured_providers()
    usable_providers = configured | {"opencode"}

    raw = ""
    try:
        proc = subprocess.run(
            [resolved["bin"], "models", "--verbose"],
            capture_output=True,
            text=True,
            timeout=_OPENCODE_MODELS_TIMEOUT_S,
        )
        raw = proc.stdout or "" if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        raw = ""

    records = []
    if raw:
        current_header = None
        current_lines = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if not line.startswith((" ", "\t", "{")) and "/" in stripped:
                if current_header and current_lines:
                    try:
                        payload = json.loads("\n".join(current_lines))
                        record = _opencode_record_from_payload(current_header, payload, usable_providers)
                        if record:
                            records.append(record)
                    except (ValueError, TypeError):
                        pass
                current_header = stripped
                current_lines = []
                continue
            if current_header is not None:
                current_lines.append(line)

        if current_header and current_lines:
            try:
                payload = json.loads("\n".join(current_lines))
                record = _opencode_record_from_payload(current_header, payload, usable_providers)
                if record:
                    records.append(record)
            except (ValueError, TypeError):
                pass

    with _OPENCODE_MODELS_LOCK:
        if records:
            if file_records and len(records) < len(file_records) * 0.5:
                _OPENCODE_MODELS_CACHE["ts"] = now
                _OPENCODE_MODELS_CACHE["records"] = file_records
                return list(file_records)
            _opencode_write_cache_file(records)
            _OPENCODE_MODELS_CACHE["ts"] = now
            _OPENCODE_MODELS_CACHE["records"] = records
        elif file_records:
            _OPENCODE_MODELS_CACHE["ts"] = now
            _OPENCODE_MODELS_CACHE["records"] = file_records
            records = list(file_records)

    return records
