# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Devin (Cognition) engine integration — cloud API + local CLI.

Two backends share this module:

1. **Cloud API** (read-only): sessions listed via Devin's REST API (v1),
   authenticated with a personal API key from DEVIN_API_KEY. Listing +
   transcript view only; no spawn/resume/steering. Session IDs use the
   ``devin-`` prefix.

2. **Local CLI** (full parity): the ``devin`` terminal CLI stores sessions in
   a SQLite DB at ``~/.local/share/devin/cli/sessions.db``. CCC spawns it
   headless via ``devin -p "prompt"`` (one-shot, like gemini/cursor), resumes
   via ``devin --resume <id> -p "text"``, and reads transcripts from the DB.
   Session IDs use the ``devincli-`` prefix to avoid collision with cloud
   sessions.

Everything degrades to empty results on a missing key, missing binary, network
failure, or an unrecognized payload shape. Names still living in server.py are
reached via ``_core`` at call time."""

from __future__ import annotations

from datetime import datetime
import json
import os
import shlex
import shutil
import sqlite3
import time
import urllib.request
from pathlib import Path

from ccc_server import core as _core

DEVIN_API_BASE = "https://api.devin.ai/v1"
DEVIN_HTTP_TIMEOUT_S = 10
DEVIN_SESSION_PREFIX = "devin-"
# Board refreshes poll often; cache the session list and per-session details
# on disk so a refresh storm doesn't hammer the API.
DEVIN_SESSIONS_CACHE_TTL_S = 60
DEVIN_DETAIL_ACTIVE_TTL_S = 30
DEVIN_DETAIL_DONE_TTL_S = 7 * 24 * 3600
# A session counts as live while Devin reports it actively working.
_DEVIN_ACTIVE_STATUSES = frozenset({
    "working", "running", "blocked", "pending", "queued", "in_progress",
    "resumed", "claimed",
})

# ---------------------------------------------------------------------------
# Local CLI backend
# ---------------------------------------------------------------------------
# The Devin CLI (~/.local/bin/devin) stores sessions in a SQLite DB. CCC
# spawns it headless via `devin -p "prompt"` (one-shot, like gemini/cursor)
# and reads transcripts from the DB. Session IDs use the "devincli-" prefix
# to avoid collision with cloud "devin-" sessions.

DEVIN_CLI_SESSION_PREFIX = "devincli-"
DEVIN_CLI_HOME = Path.home() / ".local" / "share" / "devin" / "cli"
DEVIN_CLI_SESSIONS_DB = DEVIN_CLI_HOME / "sessions.db"
DEVIN_CLI_LOCKS_DIR = DEVIN_CLI_HOME / "session_locks"
_DEVIN_CLI_ID_CACHE = {"key": None, "ids": set(), "mtime": 0}


def _devin_api_key():
    """Personal Devin API key from the environment, or None when unset."""
    raw = (
        os.environ.get("DEVIN_API_KEY")
        or os.environ.get("CCC_DEVIN_API_KEY")
        or ""
    ).strip()
    return raw or None


def _devin_available():
    """(installed, detail) probe for the read-only engines inventory.

    Key presence only — no network call (telemetry and the First Flight tour
    must stay offline-cheap). The detail string names the env var, never the
    key itself."""
    if _devin_api_key():
        return True, "DEVIN_API_KEY"
    return False, ""


def _devin_sessions_cache_path():
    return _core.COMMAND_CENTER_STATE_DIR / "devin_sessions_cache.json"


def _devin_detail_cache_path(session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return _core.COMMAND_CENTER_STATE_DIR / f"devin_session_{safe}.json"


def _devin_read_cache(path, ttl_s):
    """Cached JSON payload younger than ttl_s, else None."""
    try:
        st = path.stat()
    except OSError:
        return None
    if ttl_s >= 0 and (time.time() - st.st_mtime) > ttl_s:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _devin_write_cache(path, payload):
    """Write the cache file only when the payload changed, so the file mtime
    stays a meaningful "sessions changed" signal for the archive corpus
    signature instead of flipping on every TTL refresh."""
    body = json.dumps(payload)
    try:
        if path.is_file() and path.read_text(encoding="utf-8", errors="replace") == body:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError:
        pass


def _devin_api_get(path):
    """GET <base><path> with Bearer auth; parsed JSON or None on any error.

    Never raises and never logs the key — a missing key, a 4xx/5xx, a
    timeout, or a non-JSON body all degrade to None."""
    key = _devin_api_key()
    if not key:
        return None
    url = DEVIN_API_BASE + path
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "claude-command-center",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=DEVIN_HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _devin_list_sessions():
    """Session dicts from GET /v1/sessions, disk-cached for
    DEVIN_SESSIONS_CACHE_TTL_S. Tolerates both the documented
    {"sessions": [...]} envelope and a bare list."""
    cache = _devin_sessions_cache_path()
    cached = _devin_read_cache(cache, DEVIN_SESSIONS_CACHE_TTL_S)
    if isinstance(cached, list):
        return cached
    data = _devin_api_get("/sessions")
    if data is None:
        # Network/key failure: fall back to a stale cache rather than
        # blanking the board.
        stale = _devin_read_cache(cache, -1)
        return stale if isinstance(stale, list) else []
    sessions = data.get("sessions") if isinstance(data, dict) else data
    if not isinstance(sessions, list):
        return []
    sessions = [s for s in sessions if isinstance(s, dict)]
    _devin_write_cache(cache, sessions)
    return sessions


def _devin_session_detail(session_id):
    """GET /v1/sessions/<id> with a status-dependent disk cache: active
    sessions re-fetch after DEVIN_DETAIL_ACTIVE_TTL_S, finished ones are
    cached for days (their transcripts no longer change)."""
    cache = _devin_detail_cache_path(session_id)
    cached = _devin_read_cache(cache, 0)  # existence probe only
    if isinstance(cached, dict):
        status = _devin_status(cached)
        ttl = (
            DEVIN_DETAIL_ACTIVE_TTL_S
            if status in _DEVIN_ACTIVE_STATUSES
            else DEVIN_DETAIL_DONE_TTL_S
        )
        fresh = _devin_read_cache(cache, ttl)
        if isinstance(fresh, dict):
            return fresh
    data = _devin_api_get(f"/sessions/{session_id}")
    if not isinstance(data, dict):
        stale = _devin_read_cache(cache, -1)
        return stale if isinstance(stale, dict) else None
    _devin_write_cache(cache, data)
    return data


def _devin_status(session):
    """Lowercased session status, from either v1 field spelling."""
    return str(
        session.get("status_enum") or session.get("status") or ""
    ).strip().lower()


def _devin_epoch(value):
    """Best-effort epoch seconds from a Devin timestamp (epoch s/ms or ISO)."""
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


def _devin_content_text(content):
    """Text out of a Devin message body: a plain string, a {text: ...} part,
    a list of parts, or a nested message dict. The v1 message schema is not
    firmly documented, so anything unrecognized yields ""."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        t = content.get("text")
        if isinstance(t, str) and t.strip():
            return t
        for key in ("content", "message"):
            nested = _devin_content_text(content.get(key))
            if nested:
                return nested
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            t = _devin_content_text(item)
            if t and t.strip():
                parts.append(t)
        if parts:
            return "\n".join(parts)
    return ""


def _devin_message_role_text(msg):
    """(role, text) for one Devin message — role is 'user' | 'assistant' | ''.

    Message shapes are guessed from the v1 docs: each message may spell its
    origin as type / event_type / origin and its body as message / text /
    content. Unknown shapes return ('', '') and are skipped by callers."""
    if not isinstance(msg, dict):
        return "", ""
    origin = str(
        msg.get("type") or msg.get("event_type") or msg.get("origin") or ""
    ).lower()
    text = _devin_content_text(
        msg.get("message") if "message" in msg
        else msg.get("text") if "text" in msg
        else msg.get("content")
    )
    if "user" in origin or "human" in origin:
        return "user", text
    if "devin" in origin or "assistant" in origin or "agent" in origin:
        return "assistant", text
    return "", ""


def _devin_message_ts(msg):
    if not isinstance(msg, dict):
        return ""
    raw = msg.get("timestamp") or msg.get("created_at") or msg.get("ts")
    if raw is None:
        return ""
    epoch = _devin_epoch(raw)
    if epoch:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    return str(raw)


def _is_devin_session(session_id):
    """Prefix probe only — engine detection runs per open and must never
    hit the network."""
    return isinstance(session_id, str) and session_id.startswith(DEVIN_SESSION_PREFIX)


def find_devin_conversations(
    repo_path=None,
    include_old=False,
    repo_only=False,
    progress=None,
    limit=None,
):
    """Discover Devin cloud sessions via the v1 API (DEVIN_API_KEY).

    Devin sessions have no local cwd, so they are repo-unbound: repo_only is
    accepted for signature parity with the other adapters but never filters
    rows out. No API key (or any API failure) → []."""
    if not _devin_api_key():
        return []
    sessions = _devin_list_sessions()
    if not sessions:
        return []
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
    sessions.sort(
        key=lambda s: _devin_epoch(s.get("updated_at") or s.get("created_at")),
        reverse=True,
    )
    if limit and limit > 0:
        sessions = sessions[: int(limit)]
    out = []
    for s in sessions:
        raw_id = str(s.get("session_id") or s.get("id") or "").strip()
        if not raw_id:
            continue
        # The v1 API already returns ids with the "devin-" prefix; only add
        # it when absent so we never produce "devin-devin-...".
        sid = raw_id if raw_id.startswith(DEVIN_SESSION_PREFIX) else DEVIN_SESSION_PREFIX + raw_id
        created = _devin_epoch(s.get("created_at"))
        modified = _devin_epoch(s.get("updated_at")) or created
        freshness = max(modified, last_interactions.get(sid) or 0)
        if not include_old and cutoff > 0 and freshness < cutoff:
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
        title = _core._strip_ccc_session_state_instruction(
            str(s.get("title") or "")
        ).strip()
        first_message = ""
        messages = s.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                role, text = _devin_message_role_text(msg)
                if role == "user" and text.strip():
                    first_message = text.strip()
                    break
        first_message = _core._strip_ccc_session_state_instruction(first_message).strip()
        # Web URLs use the id without the "devin-" prefix.
        url_slug = raw_id[len(DEVIN_SESSION_PREFIX):] if raw_id.startswith(DEVIN_SESSION_PREFIX) else raw_id
        display_name = (
            name_overrides.get(sid)
            or _core._truncate_session_name(title)
            or (first_message[:80] if first_message else None)
            or f"Devin session {url_slug[:8]}"
        )
        status = _devin_status(s)
        is_live = status in _DEVIN_ACTIVE_STATUSES
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "devin",
            "engine": "devin",
            "timestamp": "",
            "branch": "",
            "git_branch": "",
            "first_message": first_message[:200],
            "display_name": display_name,
            "ai_title": title or None,
            "name_overridden": bool(name_overrides.get(sid)),
            "last_prompt": first_message[:200],
            "size": 0,
            "modified": modified,
            "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)) if modified else "",
            "mtime": modified,
            "jsonl_path": "",
            "folder_label": "Devin",
            "folder_path": "",
            "worktree_label": None,
            "session_cwd": None,
            "session_cwd_exists": False,
            "session_cwd_is_worktree": False,
            "worktree_dirty": False,
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
            "last_assistant_text": "",
            "tail_issue_number": None,
            "tail_pr_number": None,
            "tail_pr_url": str((s.get("pull_request") or {}).get("url") or "") or None,
            "pr_state": None,
            "session_state": None,
            "archived": sid in archived_set,
            "trashed": sid in trashed_set,
            "verified": sid in verified_set,
            "pinned_repo": False,
            "last_interacted": last_interactions.get(sid),
            "is_live": is_live,
            "spawn_pid": None,
            "needs_approval": False,
            "needs_approval_message": "",
            "model": str(s.get("model") or ""),
            "reasoning_effort": "",
            # Cloud session link — the list payload has no url field, so
            # construct the app.devin.ai URL (id without the devin- prefix).
            # Additive field; no other engine exposes one yet.
            "session_url": str(s.get("url") or f"https://app.devin.ai/sessions/{url_slug}"),
        })
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    return out


def _parse_devin_conversation(session_id, after_line=0):
    """Build a CCC transcript event list from a Devin session's messages.

    line = number of messages consumed, matching the other adapters'
    after_line semantics. Unknown message shapes are skipped, never fatal."""
    if not _devin_api_key():
        return {"events": [], "last_line": 0}
    raw_id = str(session_id or "")
    if not raw_id:
        return {"events": [], "last_line": 0}
    # The detail endpoint accepts the full "devin-..." id (verified live).
    detail = _devin_session_detail(raw_id)
    if not isinstance(detail, dict):
        return {"events": [], "last_line": 0}
    messages = detail.get("messages")
    if not isinstance(messages, list):
        messages = []
    events = []
    line = 0
    for msg in messages:
        role, text = _devin_message_role_text(msg)
        text = text.strip()
        if not role or not text:
            continue
        ts = _devin_message_ts(msg)
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
                "message_id": f"devin-{line}",
                "blocks": [{"kind": "text", "text": text}],
            })
    if after_line and after_line > 0:
        visible = [e for e in events if e["line"] > after_line]
    else:
        visible = events
    return {"events": visible, "last_line": line}


# ---------------------------------------------------------------------------
# Local CLI backend — binary resolution, session discovery, transcript parse
# ---------------------------------------------------------------------------

def _resolve_devin_bin():
    """Locate a usable Devin CLI binary.

    Priority order mirrors the other engines:
      1. $CCC_DEVIN_BIN when set and executable.
      2. ``shutil.which("devin")``.
      3. ~/.local/bin/devin (common user-install location).
    """
    env_bin = os.environ.get("CCC_DEVIN_BIN")
    if env_bin:
        expanded = os.path.expanduser(env_bin)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return {"available": True, "bin": expanded, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "code": "devin_unavailable",
            "reason": f"CCC_DEVIN_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("devin")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    local_bin = Path.home() / ".local" / "bin" / "devin"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return {"available": True, "bin": str(local_bin), "source": "candidate"}
    return {
        "available": False,
        "bin": None,
        "code": "devin_unavailable",
        "reason": "Devin CLI not found. Install Devin CLI or set CCC_DEVIN_BIN.",
    }


def _devin_cli_db_path():
    """Path to the sessions DB, overridable for tests via CCC_DEVIN_DB."""
    override = os.environ.get("CCC_DEVIN_DB")
    if override:
        return Path(override).expanduser()
    return DEVIN_CLI_SESSIONS_DB


def _devin_cli_connect():
    """Open a read-only connection to the Devin CLI sessions DB, or None."""
    path = _devin_cli_db_path()
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    try:
        # uri=True + mode=ro prevents creating the DB if it vanished between
        # the is_file check and the connect.
        con = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=3,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


def _devin_cli_session_ids():
    """Cached set of raw session IDs from the Devin CLI SQLite DB.

    Cached by DB file mtime so repeated detection probes stay cheap. Returns
    an empty set when the DB is missing or unreadable — never raises.
    """
    path = _devin_cli_db_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0
    cache = _DEVIN_CLI_ID_CACHE
    if cache.get("key") == str(path) and cache.get("mtime") == mtime:
        return set(cache.get("ids") or set())
    ids = set()
    con = _devin_cli_connect()
    if con is not None:
        try:
            for row in con.execute("SELECT id FROM sessions"):
                sid = row["id"]
                if sid:
                    ids.add(str(sid))
        except sqlite3.Error:
            pass
        finally:
            con.close()
    cache["key"] = str(path)
    cache["mtime"] = mtime
    cache["ids"] = set(ids)
    return ids


def _is_devin_cli_session(session_id):
    """Prefix probe for local CLI sessions — never touches the DB."""
    return (
        isinstance(session_id, str)
        and session_id.startswith(DEVIN_CLI_SESSION_PREFIX)
    )


def _devin_cli_raw_id(session_id):
    """Strip the devincli- prefix to get the raw Devin CLI session ID."""
    if session_id and session_id.startswith(DEVIN_CLI_SESSION_PREFIX):
        return session_id[len(DEVIN_CLI_SESSION_PREFIX):]
    return str(session_id or "")


def _devin_cli_session_live(raw_id):
    """True when a lock file exists for the session (process is running)."""
    if not raw_id:
        return False
    lock = DEVIN_CLI_LOCKS_DIR / f"{raw_id}.lock"
    try:
        return lock.is_file()
    except OSError:
        return False


def find_devin_cli_conversations(
    repo_path=None,
    include_old=False,
    repo_only=False,
    progress=None,
    limit=None,
):
    """Discover local Devin CLI sessions from the SQLite DB.

    Sessions are repo-scoped (each has a working_directory). When repo_path is
    given, only sessions in that repo are returned. No DB (or any error) → [].
    """
    con = _devin_cli_connect()
    if con is None:
        return []
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

    # Resolve repo_path to a canonical string for comparison.
    repo_filter = None
    if repo_only and repo_path:
        try:
            repo_filter = str(Path(repo_path).resolve())
        except OSError:
            repo_filter = str(repo_path)

    rows = []
    try:
        query = (
            "SELECT id, working_directory, backend_type, model, agent_mode, "
            "created_at, last_activity_at, title, main_chain_id "
            "FROM sessions ORDER BY last_activity_at DESC"
        )
        for row in con.execute(query):
            raw_id = str(row["id"] or "").strip()
            if not raw_id:
                continue
            working_dir = str(row["working_directory"] or "").strip()
            if repo_filter:
                try:
                    wd_resolved = str(Path(working_dir).resolve())
                except OSError:
                    wd_resolved = working_dir
                if wd_resolved != repo_filter:
                    continue
            created = float(row["created_at"] or 0)
            modified = float(row["last_activity_at"] or 0) or created
            sid = DEVIN_CLI_SESSION_PREFIX + raw_id
            freshness = max(modified, last_interactions.get(sid) or 0)
            if not include_old and cutoff > 0 and freshness < cutoff:
                continue
            if not include_old and max_rows > 0 and len(rows) >= max_rows:
                continue
            title = _core._strip_ccc_session_state_instruction(
                str(row["title"] or "")
            ).strip()
            model = str(row["model"] or "")
            # Read first user message from prompt_history for first_message.
            first_message = ""
            try:
                ph_row = con.execute(
                    "SELECT content FROM prompt_history "
                    "WHERE session_id = ? AND is_shell = 0 "
                    "ORDER BY timestamp ASC LIMIT 1",
                    (raw_id,),
                ).fetchone()
                if ph_row:
                    first_message = str(ph_row["content"] or "").strip()
            except sqlite3.Error:
                pass
            first_message = _core._strip_ccc_session_state_instruction(
                first_message
            ).strip()
            display_name = (
                name_overrides.get(sid)
                or _core._truncate_session_name(title)
                or (first_message[:80] if first_message else None)
                or f"Devin session {raw_id[:12]}"
            )
            is_live = _devin_cli_session_live(raw_id)
            # Check if the working directory exists.
            wd_exists = False
            wd_is_worktree = False
            if working_dir:
                try:
                    wd_exists = Path(working_dir).is_dir()
                except OSError:
                    pass
            rows.append({
                "id": sid,
                "session_id": sid,
                "source": "devin-cli",
                "engine": "devin",
                "timestamp": "",
                "branch": "",
                "git_branch": "",
                "first_message": first_message[:200],
                "display_name": display_name,
                "ai_title": title or None,
                "name_overridden": bool(name_overrides.get(sid)),
                "last_prompt": first_message[:200],
                "size": 0,
                "modified": modified,
                "modified_human": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(modified)
                ) if modified else "",
                "mtime": modified,
                "jsonl_path": "",
                "folder_label": "Devin",
                "folder_path": working_dir or "",
                "worktree_label": None,
                "session_cwd": working_dir or None,
                "session_cwd_exists": wd_exists,
                "session_cwd_is_worktree": wd_is_worktree,
                "worktree_dirty": False,
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
                "last_assistant_text": "",
                "tail_issue_number": None,
                "tail_pr_number": None,
                "tail_pr_url": None,
                "pr_state": None,
                "session_state": None,
                "archived": sid in archived_set,
                "trashed": sid in trashed_set,
                "verified": sid in verified_set,
                "pinned_repo": False,
                "last_interacted": last_interactions.get(sid),
                "is_live": is_live,
                "spawn_pid": None,
                "needs_approval": False,
                "needs_approval_message": "",
                "model": model,
                "reasoning_effort": "",
                "session_url": None,
            })
    except sqlite3.Error:
        pass
    finally:
        con.close()
    rows.sort(
        key=lambda x: x.get("last_interacted") or x.get("modified") or 0,
        reverse=True,
    )
    if limit and limit > 0:
        rows = rows[:int(limit)]
    return rows


def _parse_devin_cli_conversation(session_id, after_line=0):
    """Build a CCC transcript event list from a Devin CLI session's messages.

    Reads user and assistant messages from the message_nodes table, deduped
    by content (the CLI rebuilds context each turn, producing duplicates).
    line = number of messages consumed, matching the other adapters'
    after_line semantics. Unknown shapes are skipped, never fatal."""
    raw_id = _devin_cli_raw_id(session_id)
    if not raw_id:
        return {"events": [], "last_line": 0}
    con = _devin_cli_connect()
    if con is None:
        return {"events": [], "last_line": 0}
    events = []
    line = 0
    seen = set()  # (role, content) dedup — the CLI duplicates context rebuilds
    try:
        for row in con.execute(
            "SELECT node_id, chat_message, created_at "
            "FROM message_nodes WHERE session_id = ? "
            "ORDER BY node_id",
            (raw_id,),
        ):
            try:
                msg = json.loads(row["chat_message"])
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").strip().lower()
            if role not in ("user", "assistant"):
                continue
            text = str(msg.get("content") or "").strip()
            if not text:
                continue
            # Skip system-injected user messages (context rebuilds) — only
            # keep actual user input. Assistant messages have no
            # is_user_input flag, so they pass through.
            if role == "user":
                meta = msg.get("metadata") or {}
                if not meta.get("is_user_input"):
                    continue
            dedup_key = (role, text)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            ts_raw = msg.get("metadata", {}).get("created_at") or row["created_at"]
            ts = _devin_epoch(ts_raw)
            ts_str = (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""
            )
            line += 1
            if role == "user":
                events.append({
                    "line": line, "ts": ts_str, "type": "user_text",
                    "text": text, "images": [],
                })
            else:
                events.append({
                    "line": line, "ts": ts_str, "type": "assistant",
                    "message_id": f"devincli-{line}",
                    "blocks": [{"kind": "text", "text": text}],
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
