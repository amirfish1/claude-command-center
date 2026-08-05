# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Devin (Cognition) cloud session ingestion (read-only).

Devin is CCC's first cloud/API-based engine: there is no local session store,
so sessions are listed via Devin's REST API. This uses the v1 API
(https://api.devin.ai/v1) authenticated with a personal API key from the
DEVIN_API_KEY env var (CCC_DEVIN_API_KEY accepted as a fallback). v1 is
deprecated upstream but remains the only API personal keys can call; v3
(enterprise org-id + service-user RBAC) support can be added later.

Listing + transcript view only; no spawn / resume / steering. Everything here
degrades to empty results on a missing key, network failure, or an
unrecognized payload shape — the API key is never logged. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime
import json
import os
import time
import urllib.request

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
        sid = DEVIN_SESSION_PREFIX + raw_id
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
        display_name = (
            name_overrides.get(sid)
            or _core._truncate_session_name(title)
            or (first_message[:80] if first_message else None)
            or f"Devin session {raw_id[:8]}"
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
            "model": str(s.get("model") or ""),
            "reasoning_effort": "",
            # Cloud session link (https://app.devin.ai/sessions/...) — additive
            # field; no other engine exposes one yet.
            "session_url": str(s.get("url") or ""),
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
    if raw_id.startswith(DEVIN_SESSION_PREFIX):
        raw_id = raw_id[len(DEVIN_SESSION_PREFIX):]
    if not raw_id:
        return {"events": [], "last_line": 0}
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
