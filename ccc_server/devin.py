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

from datetime import datetime, timezone
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import threading
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
DEVIN_MODEL_LIST_TIMEOUT_S = 15
DEVIN_MODEL_LIST_TTL_S = 300
_DEVIN_MODEL_LIST_CACHE = {"ts": 0.0, "data": None, "lock": threading.Lock()}
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

# In-memory incremental parse cache for local CLI sessions. Devin CLI stores
# every turn (including context rebuilds) in message_nodes, so a long session
# can have thousands of rows. Re-parsing the whole table on every SSE poll is
# O(n) and visibly slow. The cache stores the already-parsed events and a
# (role, content) dedup set so subsequent calls read only newly appended rows.
# Key: raw Devin CLI session id. Bounded and LRU-evicted by access time.
_DEVIN_CLI_PARSE_CACHE = {}
_DEVIN_CLI_PARSE_CACHE_LOCK = threading.Lock()
_DEVIN_CLI_PARSE_CACHE_MAX = 64

# In-memory session-list cache for the local CLI backend. Opening the Devin
# CLI sessions DB can take multiple seconds when the DB is large and the WAL
# is huge, so we avoid reconnecting on every sidebar refresh/poll. The cache
# invalidates whenever the DB (or its WAL/SHM sidecars), the lock files, or
# the lifecycle side-car files change.
_DEVIN_CLI_LIST_CACHE = {"key": None, "rows": None}
_DEVIN_CLI_LIST_CACHE_LOCK = threading.Lock()

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


def _devin_cloud_repo_folder(s, title, first_message, repo_name_map, pinned=None):
    """Best-effort repo folder for a Devin cloud session.

    Cloud sessions carry no local cwd. We infer a project from:
      1. an explicit repo pin (highest priority),
      2. a linked GitHub PR URL (parsed to owner/repo),
      3. the session title / first user message matching a known repo name.

    Returns (folder_path, folder_label, folder_label_chip, pinned_repo)."""
    if pinned:
        folder_path = str(pinned)
        return (
            folder_path,
            _core._resolve_dir_case(folder_path) or Path(folder_path).name,
            "",
            True,
        )

    matched_path = None
    matched_name = ""

    pr_url = str((s.get("pull_request") or {}).get("url") or "").strip()
    if pr_url:
        m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/\d+", pr_url)
        if m:
            repo_name = m.group(2)
            matched_path = repo_name_map.get(repo_name)
            if matched_path:
                matched_name = repo_name

    if not matched_path:
        text = f"{title or ''} {first_message or ''}".strip().lower()
        if text:
            # Longest repo-name first so multi-word names win over substrings.
            for name, path in sorted(
                repo_name_map.items(), key=lambda kv: len(kv[0]), reverse=True
            ):
                if re.search(r"\b" + re.escape(name.lower()) + r"\b", text):
                    matched_path = path
                    matched_name = name
                    break

        if not matched_path:
            # Prefix match: title words like "STRAMP" can map to "stramp-platform".
            words = re.findall(r"[a-z0-9]+(?:[._+\-][a-z0-9]+)*", text)
            for word in words:
                if len(word) < 3:
                    continue
                for name, path in sorted(
                    repo_name_map.items(), key=lambda kv: len(kv[0]), reverse=True
                ):
                    name_lower = name.lower()
                    if name_lower.startswith(word):
                        matched_path = path
                        matched_name = name
                        break
                if matched_path:
                    break

    if matched_path:
        return (
            matched_path,
            _core._resolve_dir_case(matched_path) or Path(matched_path).name,
            "",
            False,
        )
    return "", "Devin", "Devin", False


def find_devin_conversations(
    repo_path=None,
    include_old=False,
    repo_only=False,
    progress=None,
    limit=None,
):
    """Discover Devin cloud sessions via the v1 API (DEVIN_API_KEY).

    Cloud sessions carry no local cwd, so they are not naturally repo-bound. We
    try to put them in the right project folder by:
      1. an explicit repo pin,
      2. a linked GitHub PR URL,
      3. the session title matching a known repo name.
    Anything we can't place stays in a "Devin" engine bucket. No API key (or
    any API failure) → []."""
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
    try:
        repo_pins = _core._load_repo_pins()
    except Exception:
        repo_pins = {}

    if repo_only:
        try:
            repo_path = _core.resolve_repo_path(repo_path)
        except Exception:
            return []

    try:
        known_repos = list(_core._load_recent_repos()) + list(_core._load_custom_repos())
    except Exception:
        known_repos = []
    repo_name_map = {}
    for p in known_repos:
        name = str(Path(p).name)
        if name and name not in repo_name_map:
            repo_name_map[name] = p

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
        pinned = repo_pins.get(sid)
        folder_path, folder_label, folder_label_chip, pinned_repo = _devin_cloud_repo_folder(
            s, title, first_message, repo_name_map, pinned=pinned
        )
        if repo_only:
            if pinned:
                if pinned != repo_path:
                    continue
            elif not folder_path:
                continue
            else:
                try:
                    if Path(folder_path).resolve() != Path(repo_path).resolve():
                        continue
                except OSError:
                    if folder_path != repo_path:
                        continue
        created = _devin_epoch(s.get("created_at"))
        modified = _devin_epoch(s.get("updated_at")) or created
        freshness = max(modified, last_interactions.get(sid) or 0)
        if not include_old and cutoff > 0 and freshness < cutoff:
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
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
            "folder_label": folder_label,
            "folder_path": folder_path,
            "folder_label_chip": folder_label_chip,
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
            "pinned_repo": pinned_repo,
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


def _devin_cli_lock_pid(raw_id):
    """PID recorded in ``session_locks/<id>.lock``, or None."""
    if not raw_id:
        return None
    lock = DEVIN_CLI_LOCKS_DIR / f"{raw_id}.lock"
    try:
        text = lock.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    token = text.split()[0]
    try:
        pid = int(token)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _devin_cli_pid_alive(pid):
    """True when ``os.kill(pid, 0)`` succeeds."""
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, ValueError, TypeError):
        return False


def _devin_cli_session_live(raw_id):
    """True when the session lock's recorded pid is still alive.

    A leftover lock file after the CLI exits is not liveness — every Devin
    CLI row was showing as live because locks were never reaped.
    """
    pid = _devin_cli_lock_pid(raw_id)
    if not pid:
        return False
    return _devin_cli_pid_alive(pid)


def _devin_cli_raw_id_for_pid(pid):
    """Raw Devin CLI session id whose lock file holds ``pid``, or a child of it.

    CCC's spawn pid is the ``devin`` wrapper. The CLI records the ACP child
    pid in the lock file, so a direct pid match can miss.
    """
    try:
        want = int(pid)
    except (TypeError, ValueError):
        return None
    if want <= 0:
        return None
    lock_pids = {}
    try:
        for p in DEVIN_CLI_LOCKS_DIR.iterdir():
            if p.suffix != ".lock" or len(p.name) <= 5:
                continue
            raw_id = p.name[:-5]
            lock_pid = _devin_cli_lock_pid(raw_id)
            if lock_pid:
                lock_pids[lock_pid] = raw_id
    except OSError:
        return None
    if want in lock_pids:
        return lock_pids[want]
    if not lock_pids:
        return None
    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid="],
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            child_pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if ppid == want and child_pid in lock_pids:
            return lock_pids[child_pid]
    return None


def _devin_spawn_pid_by_session_id():
    """Map ``devincli-*`` session ids to the CCC spawn pid that owns them.

    Mirrors ``_gemini_spawn_pid_by_session_id`` / ``_cursor_spawn_pid_by_session_id``
    so the sidebar placeholder can swap onto the durable row. Resolves the
    native id from the CLI DB / lock file when the spawn registry still has
    ``session_id: null`` (Devin does not print its id on stdout).
    """
    out = {}
    entries = []
    seen_pids = set()
    for s in list(getattr(_core, "_spawned_sessions", []) or []):
        if not isinstance(s, dict):
            continue
        if str(s.get("engine") or "").lower() != "devin":
            continue
        entries.append(s)
        if s.get("pid") is not None:
            seen_pids.add(s.get("pid"))
    try:
        for s in _core._load_spawn_registry():
            if not isinstance(s, dict):
                continue
            if str(s.get("engine") or "").lower() != "devin":
                continue
            if s.get("pid") in seen_pids:
                continue
            entries.append(s)
    except Exception:
        pass
    for s in entries:
        sid = s.get("session_id") or s.get("resumed_sid")
        if not sid:
            try:
                sid = _core._devin_cli_session_id_for_spawn_entry(s)
            except Exception:
                sid = None
        if not sid:
            continue
        if not s.get("session_id"):
            s["session_id"] = sid
            try:
                _core._update_spawn_session_id_in_registry(s.get("pid"), sid)
            except Exception:
                pass
        if sid in out:
            continue
        alive = False
        try:
            alive = _core._poll_spawn_entry(s) is None
        except Exception:
            alive = False
        if not alive:
            alive = _devin_cli_session_live(_devin_cli_raw_id(sid))
        out[sid] = {
            "pid": s.get("pid"),
            "alive": alive,
            "log": s.get("log") or "",
            "cwd": s.get("cwd") or "",
            "repo_path": s.get("repo_path") or "",
        }
    return out


def _devin_cli_cache_key():
    """(mtime_ns, size) across the sessions DB + its WAL/SHM sidecars.

    Used as a cache key for the parse cache and the pre-serialized response
    bytes cache — same role as ``_hermes_db_cache_key`` for Hermes. Without
    this, Devin CLI sessions return ``(0, 0)`` from
    ``_conv_parse_jsonl_mtime`` and every cache layer bails out, so the
    SSE stream falls through to the file-based path (which 404s) and the
    conversation text never refreshes while a terminal writes in parallel.
    """
    mtime_ns = 0
    size = 0
    for p in (DEVIN_CLI_SESSIONS_DB,
              Path(str(DEVIN_CLI_SESSIONS_DB) + "-wal"),
              Path(str(DEVIN_CLI_SESSIONS_DB) + "-shm")):
        try:
            st = p.stat()
        except OSError:
            continue
        mtime_ns = max(mtime_ns, st.st_mtime_ns)
        size += st.st_size
    return (mtime_ns, size)


def _devin_cli_lock_set():
    """Set of raw session IDs that currently hold a Devin CLI lock file.

    Enumerating the lock directory once is cheaper than ``is_file()`` per row
    and also gives us a stable cache-key component for the session list."""
    try:
        return frozenset(
            p.name[:-5]
            for p in DEVIN_CLI_LOCKS_DIR.iterdir()
            if p.suffix == ".lock" and len(p.name) > 5
        )
    except OSError:
        return frozenset()


def _devin_cli_list_cache_key(repo_path, include_old, repo_only, limit):
    """Composite cache key for ``find_devin_cli_conversations``.

    Covers every input that can change the returned rows without touching the
    SQLite DB: the DB state itself, running-session lock files, and CCC-side
    lifecycle/name/verification/pin side-car files."""
    parts = [_devin_cli_cache_key(), _devin_cli_lock_set()]
    for f in (
        _core.SESSION_NAMES_FILE,
        _core.ARCHIVED_CONVERSATIONS_FILE,
        _core.TRASHED_CONVERSATIONS_FILE,
        _core.VERIFIED_CONVERSATIONS_FILE,
        _core.LAST_INTERACTIONS_FILE,
        _core.PINNED_CONVERSATIONS_FILE,
    ):
        try:
            parts.append(f.stat().st_mtime_ns)
        except OSError:
            parts.append(0)
    parts.append((repo_path, bool(include_old), bool(repo_only), limit))
    return tuple(parts)


def _devin_cli_profile_log(label, duration_s, detail=""):
    """Append a timing sample to the Devin CLI diagnostic profile log.

    Log lives in ``<COMMAND_CENTER_STATE_DIR>/devin_cli_profile.log`` so the
    user and agent can inspect it after a slow load. Writes are best-effort;
    profiling never raises."""
    try:
        log_path = _core.COMMAND_CENTER_STATE_DIR / "devin_cli_profile.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            f.write(f"{ts} {label} {duration_s:.6f}s {detail}\n")
    except Exception:
        pass


def _devin_cli_first_prompts_from_history(con, raw_ids):
    """Batch lookup of the first non-shell user prompt per session.

    Replaces the per-session prompt_history query that made session-list
    refresh O(number_of_sessions). Returns {raw_id: content}."""
    if not raw_ids:
        return {}
    placeholders = ",".join("?" * len(raw_ids))
    first_prompts = {}
    start = time.perf_counter()
    try:
        query = (
            f"SELECT session_id, content, MIN(timestamp) AS ts "
            f"FROM prompt_history WHERE session_id IN ({placeholders}) "
            f"AND is_shell = 0 GROUP BY session_id"
        )
        for row in con.execute(query, raw_ids):
            sid = str(row["session_id"] or "").strip()
            if sid:
                first_prompts[sid] = str(row["content"] or "").strip()
    except sqlite3.Error:
        pass
    _devin_cli_profile_log(
        "first_prompts_from_history",
        time.perf_counter() - start,
        f"raw_ids={len(raw_ids)} found={len(first_prompts)}",
    )
    return first_prompts


def _devin_cli_first_messages_from_nodes(con, raw_ids):
    """Fallback batch parse of the earliest user input per session.

    Imported/resumed sessions can have transcript turns in message_nodes but
    no prompt_history row. Parses in node_id order and stops at the first user
    message marked as actual user input for each session.
    Returns {raw_id: content}."""
    if not raw_ids:
        return {}
    placeholders = ",".join("?" * len(raw_ids))
    first_messages = {}
    rows_scanned = 0
    start = time.perf_counter()
    try:
        query = (
            f"SELECT session_id, chat_message FROM message_nodes "
            f"WHERE session_id IN ({placeholders}) ORDER BY session_id, node_id"
        )
        for row in con.execute(query, raw_ids):
            rows_scanned += 1
            sid = str(row["session_id"] or "").strip()
            if not sid or sid in first_messages:
                continue
            try:
                msg = json.loads(row["chat_message"])
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            if str(msg.get("role") or "").strip().lower() != "user":
                continue
            meta = msg.get("metadata") or {}
            if not meta.get("is_user_input"):
                continue
            text = str(msg.get("content") or "").strip()
            if text:
                first_messages[sid] = text
    except sqlite3.Error:
        pass
    _devin_cli_profile_log(
        "first_messages_from_nodes",
        time.perf_counter() - start,
        f"raw_ids={len(raw_ids)} rows_scanned={rows_scanned} found={len(first_messages)}",
    )
    return first_messages


def _devin_cli_latest_models_for_raw_ids(con, raw_ids):
    """Best-effort {raw_id: generation_model} from the latest assistant turn.

    Some Devin frontends (e.g. the Windsurf/Cursor integration) do not write
    the model into the ``sessions`` row, so CCC's row model stays empty. The
    latest assistant ``message_nodes`` row carries ``metadata.generation_model``,
    which is good enough for the model pill and usage estimates."""
    if not raw_ids or con is None:
        return {}
    placeholders = ",".join("?" * len(raw_ids))
    latest_models = {}
    qstart = time.perf_counter()
    try:
        query = (
            "WITH latest AS ("
            "  SELECT session_id, chat_message, "
            "         ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY row_id DESC) AS rn "
            "  FROM message_nodes "
            "  WHERE session_id IN ({}) "
            "    AND json_extract(chat_message, '$.role') = 'assistant' "
            ") "
            "SELECT session_id, chat_message FROM latest WHERE rn = 1"
        ).format(placeholders)
        for row in con.execute(query, raw_ids):
            try:
                msg = json.loads(row["chat_message"])
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            gm = (msg.get("metadata") or {}).get("generation_model")
            if gm:
                latest_models[str(row["session_id"] or "").strip()] = str(gm)
    except sqlite3.Error:
        pass
    _devin_cli_profile_log(
        "latest_models_for_raw_ids",
        time.perf_counter() - qstart,
        f"raw_ids={len(raw_ids)} found={len(latest_models)}",
    )
    return latest_models


def _devin_cli_subagent_meta_for_raw_ids(con, raw_ids):
    """Subagent counts + recent list from the ``tool_call_state`` table.

    Devin stores ``run_subagent`` tool calls in ``tool_call_state``.
    The update row tells us whether the subagent is still running."""
    if not raw_ids or con is None:
        return {}
    placeholders = ",".join("?" * len(raw_ids))
    by_sid = {}
    qstart = time.perf_counter()
    try:
        query = (
            "SELECT session_id, tool_call_json, tool_call_update_json "
            "FROM tool_call_state "
            "WHERE session_id IN ({}) "
            "  AND json_extract(tool_call_json, '$._meta.\"cognition.ai/inferenceToolName\"') = 'run_subagent' "
            "ORDER BY session_id, rowid"
        ).format(placeholders)
        for row in con.execute(query, raw_ids):
            sid = str(row["session_id"] or "").strip()
            try:
                call = json.loads(row["tool_call_json"] or "{}")
                update = json.loads(row["tool_call_update_json"] or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(call, dict):
                continue
            raw = call.get("rawInput") or {}
            title = (
                str(raw.get("title") or "").strip()
                or str(call.get("title") or "").strip()
                or str(raw.get("task") or "").strip()
            )[:80]
            subagent_type = str(raw.get("profile") or "").strip()[:40]
            status = str(update.get("status") or "").strip().lower()
            if status == "completed":
                status = "done"
            elif status == "failed":
                status = "failed"
            else:
                status = "in-flight"
            entry = {
                "description": title or "(unnamed subagent)",
                "subagent_type": subagent_type,
                "status": status,
            }
            by_sid.setdefault(sid, []).append(entry)
    except sqlite3.Error:
        pass
    _devin_cli_profile_log(
        "subagent_meta_for_raw_ids",
        time.perf_counter() - qstart,
        f"raw_ids={len(raw_ids)} sids_with_subagents={len(by_sid)}",
    )
    out = {}
    for sid, entries in by_sid.items():
        out[sid] = {
            "subagent_count": len(entries),
            "subagent_in_flight_count": sum(
                1 for e in entries if e.get("status") == "in-flight"
            ),
            "subagent_recent": entries[-8:],
        }
    return out


def _devin_cli_last_assistant_text_for_raw_ids(con, raw_ids):
    """{raw_id: latest assistant message text} for the session list.

    Uses a window-function query so the DB does the work instead of parsing
    every row in Python. Returns at most one text per session.
    """
    if not raw_ids or con is None:
        return {}
    placeholders = ",".join("?" * len(raw_ids))
    out = {}
    qstart = time.perf_counter()
    try:
        # SQLite 3.25+ supports window functions.
        query = (
            "WITH ranked AS ("
            "  SELECT session_id, chat_message,"
            "    ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY node_id DESC) AS rn"
            "  FROM message_nodes"
            "  WHERE session_id IN ({})"
            "    AND json_extract(chat_message, '$.role') = 'assistant'"
            ")"
            "SELECT session_id, chat_message FROM ranked WHERE rn = 1"
        ).format(placeholders)
        for row in con.execute(query, raw_ids):
            sid = str(row["session_id"] or "").strip()
            try:
                msg = json.loads(row["chat_message"] or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            text = str(msg.get("content") or "").strip()
            if text:
                out[sid] = text
    except sqlite3.Error:
        pass
    _devin_cli_profile_log(
        "last_assistant_text_for_raw_ids",
        time.perf_counter() - qstart,
        f"raw_ids={len(raw_ids)} found={len(out)}",
    )
    return out


def _devin_cli_ship_flags_for_raw_ids(con, raw_ids):
    """{raw_id: {'has_commit': bool, 'has_push': bool, 'has_pr': bool}}.

    A cheap, single-pass LIKE scan of all messages in the session set.
    """
    if not raw_ids or con is None:
        return {}
    placeholders = ",".join("?" * len(raw_ids))
    out = {}
    qstart = time.perf_counter()
    try:
        query = (
            "SELECT session_id,"
            "  MAX(CASE WHEN chat_message LIKE '%git%commit%' THEN 1 ELSE 0 END) AS has_commit,"
            "  MAX(CASE WHEN chat_message LIKE '%git%push%' THEN 1 ELSE 0 END) AS has_push,"
            "  MAX(CASE WHEN chat_message LIKE '%gh%pr%create%' THEN 1 ELSE 0 END) AS has_pr"
            " FROM message_nodes"
            " WHERE session_id IN ({})"
            "   AND (json_extract(chat_message, '$.role') = 'assistant' OR"
            "        json_extract(chat_message, '$.role') = 'tool')"
            " GROUP BY session_id"
        ).format(placeholders)
        for row in con.execute(query, raw_ids):
            sid = str(row["session_id"] or "").strip()
            out[sid] = {
                "has_commit": bool(row["has_commit"]),
                "has_push": bool(row["has_push"]),
                "has_pr": bool(row["has_pr"]),
            }
    except sqlite3.Error:
        pass
    _devin_cli_profile_log(
        "ship_flags_for_raw_ids",
        time.perf_counter() - qstart,
        f"raw_ids={len(raw_ids)} found={len(out)}",
    )
    return out


def _devin_model_list_json():
    """Run ``devin models list --format json`` and cache the parsed result.

    The cache is process-local, bounded, and degrades to an empty catalog when
    the binary is missing or the command fails."""
    now = time.monotonic()
    with _DEVIN_MODEL_LIST_CACHE["lock"]:
        cached = _DEVIN_MODEL_LIST_CACHE["data"]
        if cached is not None and now - _DEVIN_MODEL_LIST_CACHE["ts"] < DEVIN_MODEL_LIST_TTL_S:
            return cached
    resolved = _resolve_devin_bin()
    if not resolved.get("available"):
        return {"families": [], "uid_to_family": {}, "family_to_uids": {}}
    try:
        proc = subprocess.run(
            [resolved["bin"], "models", "list", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=DEVIN_MODEL_LIST_TIMEOUT_S,
        )
        if proc.returncode != 0:
            return {"families": [], "uid_to_family": {}, "family_to_uids": {}}
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError, TypeError):
        return {"families": [], "uid_to_family": {}, "family_to_uids": {}}
    families = data.get("families") or []
    uid_to_family = {}
    family_to_uids = {}
    for fam in families:
        family_uid = fam.get("family_uid") or fam.get("slug")
        if not family_uid:
            continue
        family_to_uids[family_uid] = []
        for alias in fam.get("aliases") or []:
            uid_to_family[str(alias).strip().lower()] = family_uid
        for variant in fam.get("variants") or []:
            model_uid = str(variant.get("model_uid") or "").strip().lower()
            if not model_uid:
                continue
            uid_to_family[model_uid] = family_uid
            family_to_uids[family_uid].append(model_uid)
    result = {
        "families": families,
        "uid_to_family": uid_to_family,
        "family_to_uids": family_to_uids,
    }
    with _DEVIN_MODEL_LIST_CACHE["lock"]:
        _DEVIN_MODEL_LIST_CACHE["ts"] = now
        _DEVIN_MODEL_LIST_CACHE["data"] = result
    return result


def _devin_family_levels():
    """Map family_uid -> {effort_level: model_uid} using the live Devin catalog.

    Effort levels are encoded in the model uid (``claude-opus-5-max``,
    ``gpt-5-6-sol-low``, etc.). The base variant of a family is also assigned
    an effort from its label when the label is unambiguous (e.g. ``SWE-1.7 Max``
    means the base variant is the ``max`` effort)."""
    data = _devin_model_list_json()
    families = data.get("families") or []
    known = {"low", "medium", "high", "xhigh", "max", "none", "minimal"}
    levels = {}
    for fam in families:
        family_uid = fam.get("family_uid") or fam.get("slug")
        if not family_uid:
            continue
        norm_family = str(family_uid).lower().replace(".", "-")
        fam_levels = {}
        for variant in fam.get("variants") or []:
            model_uid = str(variant.get("model_uid") or "").strip().lower()
            if not model_uid:
                continue
            tokens = model_uid.split("-")
            flags = set()
            while tokens and tokens[-1] in ("fast", "priority"):
                flags.add(tokens.pop())
            level = ""
            if tokens and tokens[-1] in known:
                level = tokens.pop()
            base = "-".join(tokens)
            if not level and base == norm_family:
                label = str(variant.get("label") or "").lower()
                words = re.split(r"[\s\-]+", label)
                for w in reversed(words):
                    if w in known:
                        level = w
                        break
            if not level or flags:
                continue
            fam_levels[level] = model_uid
        levels[family_uid] = fam_levels
    return levels


def _devin_resolve_model(model, effort=None):
    """Translate a Devin model + reasoning effort into a concrete model uid.

    Devin does not have a separate ``--effort`` flag; the effort is part of the
    model name. CCC exposes the same effort ladder as Claude, and this helper
    resolves it to the right Devin model uid. If the effort cannot be satisfied
    (e.g. ``adaptive`` has no levels), the original model is returned so the
    spawn still succeeds with the family's default."""
    model = _core._clean_spawn_default_model(model) or ""
    if not model:
        return ""
    effort = (effort or "").strip().lower()
    if not effort:
        return model
    data = _devin_model_list_json()
    uid_to_family = data.get("uid_to_family") or {}
    family_to_uids = data.get("family_to_uids") or {}
    family = uid_to_family.get(model)
    if not family:
        if model in family_to_uids:
            family = model
        else:
            return model
    levels = _devin_family_levels()
    chosen = levels.get(family, {}).get(effort)
    if chosen:
        return chosen
    return model


def _devin_model_catalog_records(allowed_families=None):
    """Return Devin model catalog records for the engine picker.

    The catalog is built from ``devin models list --format json``. To keep the
    picker usable, fast/priority variants are omitted; users can still type them
    as custom models because ``_ENGINE_SUPPORTS_CUSTOM_MODELS["devin"]`` is
    True."""
    data = _devin_model_list_json()
    families = data.get("families") or []
    if allowed_families is None:
        allowed_families = set(data.get("family_to_uids", {}).keys())
    else:
        allowed_families = set(allowed_families or ())
    records = []
    for fam in families:
        family_uid = fam.get("family_uid") or fam.get("slug")
        if family_uid not in allowed_families:
            continue
        for variant in fam.get("variants") or []:
            model_uid = str(variant.get("model_uid") or "").strip()
            if not model_uid:
                continue
            if re.search(r"-(fast|priority)$", model_uid, re.I):
                continue
            records.append({
                "id": model_uid,
                "label": str(variant.get("label") or model_uid),
                "source": "devin-cli",
                "oneM": (variant.get("max_context_tokens") or 0) >= 1_000_000,
            })
    return records


def find_devin_cli_conversations(
    repo_path=None,
    include_old=False,
    repo_only=False,
    progress=None,
    limit=None,
):
    """Discover local Devin CLI sessions from the SQLite DB.

    Sessions are repo-scoped (each has a working_directory). When repo_path is
    given, only sessions in that repo (or a subdirectory of it) are returned.
    No DB (or any API failure) → [].

    The result is cached in memory: opening the Devin CLI sessions DB can take
    multiple seconds when the DB is large, so we avoid reconnecting on every
    sidebar refresh. The cache invalidates when the DB/WAL/SHM, lock files, or
    CCC lifecycle side-car files change."""
    start = time.perf_counter()
    cache_key = _devin_cli_list_cache_key(repo_path, include_old, repo_only, limit)
    with _DEVIN_CLI_LIST_CACHE_LOCK:
        cached = _DEVIN_CLI_LIST_CACHE
        if cached.get("key") == cache_key:
            rows = cached.get("rows")
            if rows is not None:
                elapsed = time.perf_counter() - start
                _devin_cli_profile_log(
                    "find_devin_cli_conversations",
                    elapsed,
                    f"rows={len(rows)} repo_only={repo_only} cached",
                )
                return list(rows)

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
    try:
        repo_pins = _core._load_repo_pins()
    except Exception:
        repo_pins = {}

    con = _devin_cli_connect()
    if con is None:
        _devin_cli_profile_log("find_devin_cli_conversations", 0, "no_db")
        return []

    cutoff = _core._session_scan_cutoff_ts(include_old)
    max_rows = _core._session_scan_file_limit(include_old)

    resolved_repo_path = None
    repo_path_obj = None
    git_top_cache = {}
    if repo_only:
        try:
            resolved_repo_path = _core.resolve_repo_path(repo_path)
            repo_path_obj = Path(resolved_repo_path)
        except Exception:
            con.close()
            return []

    rows = []
    try:
        spawn_by_sid = _devin_spawn_pid_by_session_id()
        query = (
            "SELECT id, working_directory, backend_type, model, agent_mode, "
            "created_at, last_activity_at, title, main_chain_id "
            "FROM sessions ORDER BY last_activity_at DESC"
        )
        qstart = time.perf_counter()
        total_sessions_scanned = 0
        for row in con.execute(query):
            total_sessions_scanned += 1
            raw_id = str(row["id"] or "").strip()
            if not raw_id:
                continue
            working_dir = str(row["working_directory"] or "").strip()
            sid = DEVIN_CLI_SESSION_PREFIX + raw_id
            pinned = repo_pins.get(sid)
            pinned_repo = False
            if repo_only:
                if pinned and pinned != resolved_repo_path:
                    continue
                if pinned == resolved_repo_path:
                    pinned_repo = True
                elif not _core._codex_cwd_matches_repo(
                    working_dir, resolved_repo_path, git_top_cache
                ):
                    continue

            created = float(row["created_at"] or 0)
            modified = float(row["last_activity_at"] or 0) or created
            freshness = max(modified, last_interactions.get(sid) or 0)
            if not include_old and cutoff > 0 and freshness < cutoff:
                continue
            if not include_old and max_rows > 0 and len(rows) >= max_rows:
                continue
            title = _core._strip_ccc_session_state_instruction(
                str(row["title"] or "")
            ).strip()
            model = str(row["model"] or "")
            # first_message/display_name are filled in after the loop via one
            # batched prompt_history query (plus a message_nodes fallback).
            first_message = ""
            display_name = ""
            spawn_info = spawn_by_sid.get(sid) or {}
            is_live = _devin_cli_session_live(raw_id) or bool(spawn_info.get("alive"))

            # Resolve the session's folder the same way other CLI engines do:
            # honor a repo pin, walk up to the git root for the label, and split
            # out a sibling worktree suffix so the UI badge renders correctly.
            session_cwd = pinned or working_dir or None
            folder_path = pinned or working_dir or ""
            git_root = ""
            if folder_path:
                try:
                    git_root = _core._find_git_root(folder_path) or ""
                except Exception:
                    git_root = ""
            if git_root:
                folder_path = git_root
                folder_label = _core._resolve_dir_case(git_root) or Path(git_root).name
            elif folder_path:
                folder_label = _core._resolve_dir_case(folder_path) or Path(folder_path).name
            else:
                folder_label = "Devin"
            worktree_label = None
            wt_idx = folder_label.find("-wt-")
            if wt_idx > 0:
                worktree_label = folder_label[wt_idx + 4:]
                folder_label = folder_label[:wt_idx]
            folder_label_chip = "Devin" if not folder_path else ""

            # Check if the working directory exists.
            wd_exists = False
            if session_cwd:
                try:
                    wd_exists = Path(session_cwd).is_dir()
                except OSError:
                    pass
            rows.append({
                "_raw_id": raw_id,
                "_title": title,
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
                "folder_label": folder_label,
                "folder_path": folder_path,
                "folder_label_chip": folder_label_chip,
                "worktree_label": worktree_label,
                "session_cwd": session_cwd,
                "session_cwd_exists": wd_exists,
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
                "tail_pr_url": None,
                "pr_state": None,
                "session_state": None,
                "archived": sid in archived_set,
                "trashed": sid in trashed_set,
                "verified": sid in verified_set,
                "pinned_repo": pinned_repo,
                "last_interacted": last_interactions.get(sid),
                "is_live": is_live,
                "spawn_pid": spawn_info.get("pid"),
                "needs_approval": False,
                "needs_approval_message": "",
                "model": model,
                "reasoning_effort": "",
                "session_url": None,
                "subagent_count": 0,
                "subagent_in_flight_count": 0,
                "subagent_recent": [],
            })
        qelapsed = time.perf_counter() - qstart
        _devin_cli_profile_log(
            "find_devin_cli_sessions_query",
            qelapsed,
            f"scanned={total_sessions_scanned} kept={len(rows)}",
        )

        # Batch the first-message lookup instead of issuing one query per
        # session. The helper uses SQLite's bare-column-in-aggregate behavior
        # to return the content from the row that achieved MIN(timestamp).
        fmstart = time.perf_counter()
        if rows:
            raw_ids = [r["_raw_id"] for r in rows]
            first_prompts = _devin_cli_first_prompts_from_history(con, raw_ids)
            missing_ids = [rid for rid in raw_ids if rid not in first_prompts]
            first_messages = _devin_cli_first_messages_from_nodes(con, missing_ids)
            latest_models = _devin_cli_latest_models_for_raw_ids(con, raw_ids)
            subagent_meta = _devin_cli_subagent_meta_for_raw_ids(con, raw_ids)
            last_assistant_texts = _devin_cli_last_assistant_text_for_raw_ids(con, raw_ids)
            ship_flags = _devin_cli_ship_flags_for_raw_ids(con, raw_ids)
            for r in rows:
                raw_id = r.pop("_raw_id", "")
                title = r.pop("_title", "")
                sid = r["session_id"]
                first_message = (
                    first_prompts.get(raw_id, "") or first_messages.get(raw_id, "")
                )
                first_message = _core._strip_ccc_session_state_instruction(
                    first_message
                ).strip()
                display_name = (
                    name_overrides.get(sid)
                    or _core._truncate_session_name(title)
                    or (first_message[:80] if first_message else None)
                    or f"Devin session {raw_id[:12]}"
                )
                r["first_message"] = first_message[:200]
                r["display_name"] = display_name
                r["last_prompt"] = first_message[:200]
                r["last_assistant_text"] = (
                    _core._strip_ccc_session_state_instruction(
                        last_assistant_texts.get(raw_id, "")
                    ).strip()[:2000]
                )
                flags = ship_flags.get(raw_id, {})
                r["has_commit"] = bool(flags.get("has_commit"))
                r["has_push"] = bool(flags.get("has_push"))
                r["has_pr"] = bool(flags.get("has_pr"))
                if r["has_commit"]:
                    r["last_commit_pos"] = 1
                if r["has_push"]:
                    r["last_push_pos"] = 1
                if r["has_pr"]:
                    r["tail_pr_number"] = 1
                if not r.get("model"):
                    r["model"] = latest_models.get(raw_id, "")
                sm = subagent_meta.get(raw_id)
                if sm:
                    r["subagent_count"] = sm["subagent_count"]
                    r["subagent_in_flight_count"] = sm["subagent_in_flight_count"]
                    r["subagent_recent"] = sm["subagent_recent"]
        _devin_cli_profile_log(
            "find_devin_cli_first_messages",
            time.perf_counter() - fmstart,
            f"prompts={len(first_prompts)} fallback={len(first_messages)} "
            f"missing={len(missing_ids)}",
        )
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
    with _DEVIN_CLI_LIST_CACHE_LOCK:
        _DEVIN_CLI_LIST_CACHE["key"] = cache_key
        _DEVIN_CLI_LIST_CACHE["rows"] = rows
    elapsed = time.perf_counter() - start
    _devin_cli_profile_log(
        "find_devin_cli_conversations",
        elapsed,
        f"rows={len(rows)} repo_only={repo_only}",
    )
    return rows


def _devin_cli_parse_message_row(chat_message, created_at, seen):
    """Parse one message_nodes row into (role, text, ts_str) or None.

    Reuses the existing dedup ``seen`` set so incremental parses stay
    consistent with a full parse. Unknown / skipped shapes return None."""
    try:
        msg = json.loads(chat_message)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None
    role = str(msg.get("role") or "").strip().lower()
    if role not in ("user", "assistant"):
        return None
    text = str(msg.get("content") or "").strip()
    if not text:
        return None
    # Skip system-injected user messages (context rebuilds) — only keep
    # actual user input. Assistant messages have no is_user_input flag.
    if role == "user":
        meta = msg.get("metadata") or {}
        if not meta.get("is_user_input"):
            return None
    dedup_key = (role, text)
    if dedup_key in seen:
        return None
    seen.add(dedup_key)
    ts_raw = msg.get("metadata", {}).get("created_at") or created_at
    ts = _devin_epoch(ts_raw)
    ts_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""
    return role, text, ts_str


def _parse_devin_cli_conversation(session_id, after_line=0):
    """Build a CCC transcript event list from a Devin CLI session's messages.

    Reads user and assistant messages from the message_nodes table, deduped
    by content (the CLI rebuilds context each turn, producing duplicates).
    Uses an in-memory incremental cache so only newly appended SQLite rows
    are parsed on subsequent calls — long sessions no longer re-parse the
    entire message_nodes table on every SSE poll.
    line = number of messages consumed, matching the other adapters'
    after_line semantics. Unknown shapes are skipped, never fatal."""
    start = time.perf_counter()
    raw_id = _devin_cli_raw_id(session_id)
    if not raw_id:
        _devin_cli_profile_log("parse_devin_cli_conversation", 0, "no_raw_id")
        return {"events": [], "last_line": 0}
    con = _devin_cli_connect()
    if con is None:
        _devin_cli_profile_log("parse_devin_cli_conversation", 0, "no_db")
        return {"events": [], "last_line": 0}
    events = []
    line = 0
    seen = set()
    max_row_id = 0
    db_key = _devin_cli_cache_key()
    was_incremental = False

    def _append_rows(rows):
        nonlocal line, max_row_id
        for row in rows:
            parsed = _devin_cli_parse_message_row(
                row["chat_message"], row["created_at"], seen
            )
            if parsed is None:
                continue
            role, text, ts_str = parsed
            line += 1
            max_row_id = max(max_row_id, row["row_id"])
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

    try:
        # Try to resume from the incremental cache. row_id is append-only on
        # normal SQLite tables, so MAX(row_id) is a cheap per-session marker.
        with _DEVIN_CLI_PARSE_CACHE_LOCK:
            cached = _DEVIN_CLI_PARSE_CACHE.get(raw_id)

        incremental = False
        if cached and cached.get("max_row_id") and cached.get("db_key") == db_key:
            row = con.execute(
                "SELECT MAX(row_id) FROM message_nodes WHERE session_id = ?",
                (raw_id,),
            ).fetchone()
            current_max_row_id = row[0] or 0
            if current_max_row_id >= cached["max_row_id"]:
                verify = con.execute(
                    "SELECT 1 FROM message_nodes "
                    "WHERE session_id = ? AND row_id = ?",
                    (raw_id, cached["max_row_id"]),
                ).fetchone()
                if verify:
                    events = list(cached["events"])
                    seen = set(cached["seen"])
                    line = cached["last_line"]
                    max_row_id = cached["max_row_id"]
                    incremental = True
                    was_incremental = True

        if incremental:
            rows = con.execute(
                "SELECT row_id, chat_message, created_at FROM message_nodes "
                "WHERE session_id = ? AND row_id > ? ORDER BY row_id",
                (raw_id, max_row_id),
            )
        else:
            rows = con.execute(
                "SELECT row_id, chat_message, created_at FROM message_nodes "
                "WHERE session_id = ? ORDER BY row_id",
                (raw_id,),
            )
        _append_rows(rows)

        with _DEVIN_CLI_PARSE_CACHE_LOCK:
            _DEVIN_CLI_PARSE_CACHE[raw_id] = {
                "events": events,
                "seen": seen,
                "last_line": line,
                "max_row_id": max_row_id,
                "db_key": db_key,
                "accessed": time.time(),
            }
            if len(_DEVIN_CLI_PARSE_CACHE) > _DEVIN_CLI_PARSE_CACHE_MAX:
                oldest = min(
                    _DEVIN_CLI_PARSE_CACHE.items(),
                    key=lambda kv: kv[1].get("accessed", 0),
                )[0]
                _DEVIN_CLI_PARSE_CACHE.pop(oldest, None)
    except sqlite3.Error:
        # Fallback when row_id isn't available or the incremental probe fails:
        # do a simple full parse ordered by node_id. Clears the stale cache.
        events = []
        line = 0
        seen = set()
        max_row_id = 0
        try:
            for row in con.execute(
                "SELECT chat_message, created_at FROM message_nodes "
                "WHERE session_id = ? ORDER BY node_id",
                (raw_id,),
            ):
                parsed = _devin_cli_parse_message_row(
                    row["chat_message"], row["created_at"], seen
                )
                if parsed is None:
                    continue
                role, text, ts_str = parsed
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
            with _DEVIN_CLI_PARSE_CACHE_LOCK:
                _DEVIN_CLI_PARSE_CACHE.pop(raw_id, None)
        except sqlite3.Error:
            pass
    finally:
        con.close()
    if after_line and after_line > 0:
        visible = [e for e in events if e["line"] > after_line]
    else:
        visible = events
    elapsed = time.perf_counter() - start
    _devin_cli_profile_log(
        "parse_devin_cli_conversation",
        elapsed,
        f"sid={raw_id} incremental={was_incremental} events={len(events)} "
        f"last_line={line} after_line={after_line}",
    )
    return {"events": visible, "last_line": line}


DEVIN_CLI_CONTEXT_LIMIT = 200_000


def _extract_devin_cli_usage(session_id):
    """Token usage for a Devin CLI session, read from the SQLite DB.

    Assistant messages carry ``metadata.metrics`` with input/output/cache
    token counts. We sum across all assistant turns and track the peak
    input window — same shape as ``_extract_gemini_usage`` etc.
    """
    empty = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "context_limit": DEVIN_CLI_CONTEXT_LIMIT,
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
    }
    raw_id = _devin_cli_raw_id(session_id)
    if not raw_id:
        return empty
    con = _devin_cli_connect()
    if con is None:
        return empty
    latest = 0
    peak = 0
    total_in = 0
    total_out = 0
    total_cache_read = 0
    total_cache_creation = 0
    model = ""
    try:
        for row in con.execute(
            "SELECT chat_message FROM message_nodes "
            "WHERE session_id = ? ORDER BY node_id",
            (raw_id,),
        ):
            try:
                msg = json.loads(row["chat_message"])
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            if str(msg.get("role") or "").lower() != "assistant":
                continue
            meta = msg.get("metadata") or {}
            metrics = meta.get("metrics") or {}
            if not isinstance(metrics, dict):
                continue
            in_tok = int(metrics.get("input_tokens") or 0)
            out_tok = int(metrics.get("output_tokens") or 0)
            cache_read = int(metrics.get("cache_read_tokens") or 0)
            cache_creation = int(metrics.get("cache_creation_tokens") or 0)
            # The context window is non-cached input + cached read + newly
            # created cached prefix. input_tokens alone is the non-cached
            # portion, so the live/peak bars must add cache to match the
            # model's real window.
            window = in_tok + cache_read + cache_creation
            if window:
                latest = window
                peak = max(peak, window)
            total_in += in_tok
            total_out += out_tok
            total_cache_read += cache_read
            total_cache_creation += cache_creation
            gm = meta.get("generation_model")
            if gm:
                model = gm
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return {
        **empty,
        "latest_input_tokens": latest,
        "peak_input_tokens": peak,
        "total_output_tokens": total_out,
        "total_input_tokens": total_in,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_creation_tokens": total_cache_creation,
        "model": model,
        "override": _core._get_session_override(session_id),
    }


# Regexes for the session-activity strip. Keep them in sync with the
# timeline patterns in ccc_server/morning_launch.py where possible.
_DEVIN_CLI_COMMIT_RE = re.compile(r"\bgit\s+(?:-\w+\s+\S+\s+)*commit\b")
_DEVIN_CLI_COMMIT_MSG_RE = re.compile(r"-m\s+['\"]([^'\"]{1,200})['\"]")
_DEVIN_CLI_PUSH_RE = re.compile(r"\bgit\s+(?:-\w+\s+\S+\s+)*push\b")
_DEVIN_CLI_PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")
_DEVIN_CLI_PR_TITLE_RE = re.compile(r"--title\s+['\"]([^'\"]{1,200})['\"]")
_DEVIN_CLI_COMMIT_RESULT_RE = re.compile(
    r"\[[^\]]+\s+([0-9a-f]{7,40})\]\s*(.+)"
)
_DEVIN_CLI_COMMIT_LINE_RE = re.compile(
    r"^\s*Commit:\s+([0-9a-f]{7,40})\s*[-—]\s*(.+)$", re.MULTILINE
)


def _extract_devin_cli_timeline(session_id):
    """Build a commit/push/PR activity strip from a Devin CLI session.

    Reads the message_nodes table, counting assistant messages as turns and
    matching git commands/output. Returns the same shape as
    `extract_session_timeline`.
    """
    raw_id = _devin_cli_raw_id(session_id)
    if not raw_id:
        return {"events": [], "total_turns": 0}
    con = _devin_cli_connect()
    if con is None:
        return {"events": [], "total_turns": 0}

    events = []
    seen = set()
    turn = 0

    def add_event(kind, subject, text, sha=None, pr_number=None):
        if turn == 0:
            return
        key = (kind, sha, subject)
        if key in seen:
            return
        seen.add(key)
        lower = text.lower()
        success = None
        if "fatal:" in lower or "error:" in lower or "exited with code" in lower and "code 0" not in lower:
            success = False
        elif kind in ("commit", "push"):
            success = True
        event = {
            "kind": kind,
            "turn": turn,
            "ts": ts,
            "subject": subject,
            "success": success,
        }
        if sha:
            event["sha"] = sha
        if pr_number:
            event["pr_number"] = pr_number
        events.append(event)

    try:
        for row in con.execute(
            "SELECT chat_message, created_at FROM message_nodes "
            "WHERE session_id = ? ORDER BY node_id",
            (raw_id,),
        ):
            try:
                msg = json.loads(row["chat_message"])
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").lower()
            if role == "assistant":
                turn += 1
            if role not in ("assistant", "tool"):
                continue

            created = row["created_at"]
            if created:
                ts = (
                    datetime.fromtimestamp(float(created), tz=timezone.utc)
                    .isoformat()
                )
            else:
                ts = ""

            content = msg.get("content")
            if not isinstance(content, str) or not content:
                continue

            # Commit commands and output lines.
            for m in _DEVIN_CLI_COMMIT_LINE_RE.finditer(content):
                add_event("commit", m.group(2).strip(), content, sha=m.group(1))
            for m in _DEVIN_CLI_COMMIT_RESULT_RE.finditer(content):
                add_event("commit", m.group(2).strip(), content, sha=m.group(1))
            if _DEVIN_CLI_COMMIT_RE.search(content):
                subject = ""
                m = _DEVIN_CLI_COMMIT_MSG_RE.search(content)
                if m:
                    subject = m.group(1)
                add_event("commit", subject, content)
            if _DEVIN_CLI_PUSH_RE.search(content):
                add_event("push", "", content)
            if _DEVIN_CLI_PR_CREATE_RE.search(content):
                subject = ""
                m = _DEVIN_CLI_PR_TITLE_RE.search(content)
                if m:
                    subject = m.group(1)
                add_event("pr", subject, content)
    except sqlite3.Error:
        pass
    finally:
        con.close()

    return {"events": events, "total_turns": turn}


def _extract_files_from_devin_cli_conversation(session_id):
    """Extract file-like paths from a Devin CLI transcript for the Files panel.

    Walks message_nodes and runs the same path/URL extraction used for
    JSONL sessions, then groups by file category.
    """
    raw_id = _devin_cli_raw_id(session_id)
    if not raw_id:
        return {"count": 0, "truncated": False, "groups": {}}
    con = _devin_cli_connect()
    if con is None:
        return {"count": 0, "truncated": False, "groups": {}}

    seen = {}
    truncated = False
    line = 0
    try:
        for row in con.execute(
            "SELECT chat_message FROM message_nodes "
            "WHERE session_id = ? ORDER BY node_id",
            (raw_id,),
        ):
            line += 1
            try:
                msg = json.loads(row["chat_message"])
            except (ValueError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "").lower()
            if role == "system":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content:
                continue
            for target, kind in _core._ffc_iter_targets(content):
                # Drop skill definition files injected into the system context.
                if (
                    ("/.claude/skills/" in target
                     or "/.config/devin/skills/" in target
                     or "/.agents/skills/" in target)
                    and target.rsplit("/", 1)[-1] == "SKILL.md"
                ):
                    continue
                truncated = _core._ffc_consider_file_target(
                    seen, target, kind, line, truncated
                )
    except sqlite3.Error:
        pass
    finally:
        con.close()

    groups = {}
    for row in seen.values():
        cat = row["category"]
        groups.setdefault(cat, []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: r["first_line"])

    return {"count": len(seen), "truncated": truncated, "groups": groups}


def _devin_cli_session_detail(session_id):
    """Lightweight drill-in for /api/session/<id> for a Devin CLI session.

    Returns the same shape as `compute_session_detail`: state, last assistant
    text, and the last few turns from the SQLite transcript.
    """
    raw_id = _devin_cli_raw_id(session_id)
    if not raw_id:
        return {"ok": False, "error": "session not found", "session_id": session_id}, 404

    parsed = _parse_devin_cli_conversation(session_id)
    events = parsed.get("events", [])
    last_assistant_text = ""
    turns = []
    for ev in reversed(events):
        if ev.get("type") == "assistant" and not last_assistant_text:
            blocks = ev.get("blocks")
            if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict):
                last_assistant_text = blocks[0].get("text", "")
            else:
                last_assistant_text = ev.get("text", "")
        turns.insert(0, ev)
        if len(turns) >= 3:
            break
    turns = turns[:3][::-1] if turns else []

    con = _devin_cli_connect()
    mtime = None
    cwd = ""
    if con:
        try:
            row = con.execute(
                "SELECT working_directory, last_activity_at FROM sessions "
                "WHERE id = ?",
                (raw_id,),
            ).fetchone()
            if row:
                cwd = row["working_directory"] or ""
                mtime = row["last_activity_at"]
        finally:
            con.close()

    row = {
        "session_id": session_id,
        "last_assistant_text": last_assistant_text,
        "is_live": _devin_cli_session_live(raw_id),
    }
    return {
        "ok": True,
        "session_id": session_id,
        "state": _core._stamp_session_state(row),
        "ended_blocked": _core._session_ended_blocked(row),
        "is_live": row["is_live"],
        "mtime": mtime,
        "folder_label": _core._slug_to_label(Path(cwd).name) if cwd else None,
        "sidecar_in_flight": False,
        "question_text": "",
        "soft_block": None,
        "last_assistant_text": (last_assistant_text or "")[:2000],
        "turns": turns,
    }, 200
