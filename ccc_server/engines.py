"""Extracted from server.py (originally lines 33541-40939).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

# Rebound on every importlib.reload; background threads capture it at birth
# and exit on mismatch (see _start_headless_staleness_watcher).
_MODULE_GEN = object()

from datetime import datetime, timedelta, timezone, time as datetime_time
from pathlib import Path
import collections
import copy
import fcntl
import json
import math
import os
import queue
import re
import select
import shlex
import signal
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Installed-engines inventory (First Flight tour welcome chips).
#
# Backs GET /api/engines/installed: one cheap, never-throwing probe per
# engine so the onboarding tour can show which engines already exist on this
# machine. Spawnable engines reuse the exact bin resolvers behind the
# spawn-<engine>/availability endpoints; read-only engines count as
# installed when their session store exists on disk. Short TTL cache —
# detection touches PATH + a handful of well-known dirs, and the tour may
# replay several times in one sitting.
# ---------------------------------------------------------------------------

_ENGINES_INSTALLED_CACHE = {"ts": 0.0, "data": None}
_ENGINES_INSTALLED_TTL_SEC = 60.0


def _spawn_engine_installed(resolver):
    """(installed, detail) from a spawn-bin resolver dict; never raises."""
    try:
        info = resolver() or {}
        if info.get("available"):
            return True, str(info.get("bin") or "")
    except Exception:
        pass
    return False, ""


def _readonly_store_installed(candidates):
    """(installed, detail): True when any candidate path exists on disk."""
    for candidate in candidates:
        try:
            if candidate.exists():
                return True, str(candidate)
        except OSError:
            continue
    return False, ""


def _detect_engines_installed():
    """Uncached per-engine probes; _engines_installed adds the TTL cache."""
    engines = []
    for engine, label, resolver in (
        ("claude", "Claude Code", _core._resolve_claude_bin),
        ("codex", "Codex", _core._resolve_codex_bin),
        ("gemini", "Gemini", _core._resolve_gemini_bin),
        ("cursor", "Cursor", _core._resolve_cursor_bin),
        ("antigravity", "Antigravity", _core._resolve_antigravity_bin),
        ("kilo", "Kilo Code", _core._resolve_kilo_bin),
        ("opencode", "OpenCode", _core._resolve_opencode_bin),
        ("kimi", "Kimi Code", _core._resolve_kimi_bin),
        ("hermes", "Hermes", _core._resolve_hermes_bin),
        ("devin", "Devin", _core._resolve_devin_bin),
        ("grok", "Grok", _core._resolve_grok_bin),
    ):
        installed, detail = _spawn_engine_installed(resolver)
        engines.append({
            "engine": engine,
            "label": label,
            "installed": installed,
            "kind": "spawn",
            "detail": detail,
        })

    readonly = []
    try:
        home = _core._copilot_home()
        readonly.append((
            "copilot", "Copilot",
            _readonly_store_installed(
                [home / "session-store.db", home / "session-state"]
            ),
        ))
    except Exception:
        readonly.append(("copilot", "Copilot", (False, "")))
    try:
        chat_dirs = _core._copilotchat_chat_dirs()
        result = (True, str(chat_dirs[0])) if chat_dirs else (False, "")
        readonly.append(("copilotchat", "Copilot Chat", result))
    except Exception:
        readonly.append(("copilotchat", "Copilot Chat", (False, "")))

    for engine, label, (installed, detail) in readonly:
        engines.append({
            "engine": engine,
            "label": label,
            "installed": installed,
            "kind": "readonly",
            "detail": detail,
        })
    return {"engines": engines}


def _engines_installed():
    now = time.monotonic()
    if (
        _ENGINES_INSTALLED_CACHE["data"] is not None
        and now - _ENGINES_INSTALLED_CACHE["ts"] < _ENGINES_INSTALLED_TTL_SEC
    ):
        return copy.deepcopy(_ENGINES_INSTALLED_CACHE["data"])
    payload = _core._detect_engines_installed()
    _ENGINES_INSTALLED_CACHE["ts"] = now
    _ENGINES_INSTALLED_CACHE["data"] = payload
    return copy.deepcopy(payload)


def _copilotchat_workspace_cwd(chat_dir):
    """Workspace folder for a workspaceStorage chatSessions dir, decoded from
    the sibling workspace.json's file:// URI. "" for empty-window stores."""
    ws_json = chat_dir.parent / "workspace.json"
    try:
        data = json.loads(ws_json.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    uri = str(data.get("folder") or "").strip()
    if not uri:
        return ""
    try:
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme != "file":
            return ""
        return urllib.parse.unquote(parsed.path)
    except Exception:
        return ""


def _copilotchat_epoch_ms(value):
    """Epoch seconds from a VS Code timestamp (epoch ms, sometimes s)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    v = float(value)
    if v <= 0:
        return 0.0
    return v / 1000.0 if v > 1e12 else v


def _copilotchat_ts_label(value):
    """ISO-8601 label for an epoch-ms request timestamp ("" when absent)."""
    ts = _copilotchat_epoch_ms(value)
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _copilotchat_message_text(message):
    """User text from a request's message: {text} or joined {parts} text."""
    if not isinstance(message, dict):
        return ""
    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    parts = message.get("parts")
    if isinstance(parts, list):
        chunks = []
        for part in parts:
            if isinstance(part, str) and part.strip():
                chunks.append(part.strip())
            elif isinstance(part, dict):
                t = part.get("text")
                if not isinstance(t, str):
                    t = part.get("value")
                if isinstance(t, str) and t.strip():
                    chunks.append(t.strip())
        if chunks:
            return "\n".join(chunks)
    return ""


def _copilotchat_response_texts(response):
    """(assistant_text, tool_names) from a request's response part list.
    MarkdownString-ish parts contribute text; tool-invocation-ish parts
    contribute names. Shapes are guessed — anything unrecognized is skipped."""
    texts = []
    tools = []
    if not isinstance(response, list):
        return "", tools
    for part in response:
        if isinstance(part, str):
            if part.strip():
                texts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        kind = str(part.get("kind") or "").lower()
        tool_name = (
            part.get("toolName") or part.get("toolId") or part.get("tool")
        )
        if "tool" in kind or tool_name:
            if tool_name:
                tools.append(str(tool_name))
            elif part.get("name"):
                tools.append(str(part["name"]))
            continue
        value = part.get("value")
        if value is None:
            value = part.get("content")
        if isinstance(value, str) and value.strip():
            texts.append(value)
        elif isinstance(value, dict):  # nested MarkdownString-ish
            nested = value.get("value")
            if isinstance(nested, str) and nested.strip():
                texts.append(nested)
    return "\n".join(texts), tools


def _copilotchat_tool_round_names(result):
    """Tool names from result.metadata.toolCallRounds[].toolCalls[].name."""
    names = []
    if not isinstance(result, dict):
        return names
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        return names
    rounds = metadata.get("toolCallRounds")
    if not isinstance(rounds, list):
        return names
    for rnd in rounds:
        if not isinstance(rnd, dict):
            continue
        calls = rnd.get("toolCalls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if isinstance(call, dict):
                name = call.get("name") or call.get("toolName")
                if name:
                    names.append(str(name))
    return names


def _copilotchat_flat_requests(path):
    """(requests, creationDate) from a flat ISerializableChatData .json
    file. utf-8-sig tolerates a BOM; anything unreadable yields ([], None)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return [], None
    if not isinstance(data, dict):
        return [], None
    reqs = data.get("requests")
    return (
        [r for r in reqs if isinstance(r, dict)] if isinstance(reqs, list) else []
    ), data.get("creationDate")


def _copilotchat_journal_records(path):
    """Parsed dict records from a .jsonl operation journal. A BOM, blank
    lines, and a truncated final line are all tolerated; non-dict lines and
    unparseable lines are skipped."""
    records = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for raw in f:
                raw = raw.strip().lstrip("\ufeff")
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        pass
    return records


def _copilotchat_replay_journal(records):
    """Replay a ChatSessionOperationLog journal into a request list.

    Snapshot records (carrying a `requests` list) replace state; append-ish
    ops carrying a single request-shaped object extend it. The op vocabulary
    is internal and churning, so unknown shapes are skipped — never crash."""
    requests = []
    for rec in records:
        reqs = rec.get("requests")
        if isinstance(reqs, list):
            requests = [r for r in reqs if isinstance(r, dict)]
            continue
        for key in ("request", "value", "data", "entry", "op"):
            v = rec.get(key)
            if isinstance(v, dict) and ("message" in v or "response" in v):
                requests.append(v)
                break
            if isinstance(v, dict) and isinstance(v.get("requests"), list):
                requests = [r for r in v["requests"] if isinstance(r, dict)]
                break
    return requests


def _copilotchat_best_effort_requests(records):
    """Last-ditch fallback when journal replay yields nothing: treat each
    record carrying role + text-ish content as a bare turn."""
    out = []
    for rec in records:
        role = str(rec.get("role") or "").lower()
        text = ""
        for key in ("text", "content", "message"):
            v = rec.get(key)
            if isinstance(v, str) and v.strip():
                text = v.strip()
                break
        if not text:
            continue
        ts = rec.get("timestamp") or rec.get("creationDate")
        if role == "user":
            out.append({"message": {"text": text}, "timestamp": ts})
        else:
            out.append({"response": [{"value": text}], "timestamp": ts})
    return out


def _copilotchat_load_requests(path):
    """(requests, creationDate) from either chatSessions file format."""
    if path.suffix == ".jsonl":
        records = _copilotchat_journal_records(path)
        requests = _copilotchat_replay_journal(records)
        if not requests:
            requests = _copilotchat_best_effort_requests(records)
        return requests, None
    return _copilotchat_flat_requests(path)


def _copilotchat_mine_texts(requests):
    """(first_user_text, last_assistant_text) from a request list."""
    first_user = ""
    last_assistant = ""
    for req in requests:
        if not first_user:
            t = _copilotchat_message_text(req.get("message"))
            if t:
                first_user = t
        text, _tools = _copilotchat_response_texts(req.get("response"))
        if text.strip():
            last_assistant = text.strip()
    return first_user, last_assistant


def _copilotchat_sessions_from_files(limit=None):
    """Listing dicts for every session found under the scanned User dirs.
    No stores on disk → []."""
    out = []
    for chat_dir in _core._copilotchat_chat_dirs():
        cwd = _copilotchat_workspace_cwd(chat_dir)
        for sid, path in _core._copilotchat_scan_dir(chat_dir).items():
            try:
                st = path.stat()
            except OSError:
                continue
            requests, creation = _copilotchat_load_requests(path)
            created = _copilotchat_epoch_ms(creation)
            updated = 0.0
            for req in requests:
                ts = _copilotchat_epoch_ms(req.get("timestamp"))
                if ts:
                    if not created or ts < created:
                        created = ts
                    if ts > updated:
                        updated = ts
            if not updated:
                updated = float(st.st_mtime)
            if not created:
                created = updated
            first_user, last_assistant = _copilotchat_mine_texts(requests)
            out.append({
                "id": sid,
                "cwd": cwd,
                "created": created,
                "updated": updated,
                "jsonl_path": str(path),
                "size": st.st_size,
                "first_user": first_user,
                "last_assistant": last_assistant,
            })
    out.sort(key=lambda s: s.get("updated") or 0, reverse=True)
    if limit and limit > 0:
        out = out[: int(limit)]
    return out


def find_copilotchat_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    """Discover VS Code Copilot Chat sessions from the scanned User dirs.

    chatSessions files are authoritative (the state.vscdb index is skipped —
    it can disagree with the files). No VS Code user-data dir → []."""
    sessions = _copilotchat_sessions_from_files(limit=limit)
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
        first_message = _core._strip_ccc_session_state_instruction(
            s.get("first_user") or ""
        ).strip()
        last_assistant_text = s.get("last_assistant") or ""
        display_name = (
            name_overrides.get(sid)
            or (first_message[:80] if first_message else None)
            or "Copilot Chat session"
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
            folder_label = "Copilot Chat"
        _wt_worktree_label = None
        _wt_idx = folder_label.find("-wt-")
        if _wt_idx > 0:
            _wt_worktree_label = folder_label[_wt_idx + 4:]
            folder_label = folder_label[:_wt_idx]
        is_live = (now - modified) < _core.COPILOTCHAT_LIVE_WINDOW_S
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "copilotchat",
            "engine": "copilotchat",
            "timestamp": "",
            "branch": "",
            "git_branch": "",
            "first_message": first_message[:200],
            "display_name": display_name,
            "ai_title": None,
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
            "archived": sid in archived_set,
            "trashed": sid in trashed_set,
            "verified": sid in verified_set,
            "pinned_repo": pinned_repo,
            "last_interacted": last_interactions.get(sid),
            "is_live": is_live,
            "spawn_pid": None,
            "needs_approval": False,
            "needs_approval_message": "",
            "model": "",
            "reasoning_effort": "",
        })
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    return out


def _copilotchat_events_from_file(path):
    """CCC transcript events from one chatSessions file (flat .json or a
    replayed .jsonl journal). Defensive by design: unrecognized part shapes
    are skipped and a malformed file never aborts the parse."""
    requests, _creation = _copilotchat_load_requests(path)
    events = []
    line = 0
    for req in requests:
        if not isinstance(req, dict):
            continue
        ts = _copilotchat_ts_label(req.get("timestamp"))
        user_text = _copilotchat_message_text(req.get("message"))
        if user_text:
            line += 1
            events.append({
                "line": line, "ts": ts, "type": "user_text",
                "text": user_text, "images": [],
            })
        blocks = []
        text, part_tools = _copilotchat_response_texts(req.get("response"))
        if text.strip():
            blocks.append({"kind": "text", "text": text.strip()})
        tool_names = list(part_tools)
        for name in _copilotchat_tool_round_names(req.get("result")):
            # toolCallRounds often repeats a tool already seen as a response
            # part — don't render it twice.
            if name not in tool_names:
                tool_names.append(name)
        for name in tool_names:
            blocks.append({
                "kind": "tool_use",
                "name": name,
                "detail": "",
                "id": "",
                "command": None,
                "command_kind": None,
            })
        if blocks:
            line += 1
            events.append({
                "line": line, "ts": ts, "type": "assistant",
                "message_id": f"copilotchat-{line}", "blocks": blocks,
            })
    return events, line


def _parse_copilotchat_conversation(session_id, after_line=0):
    """Build a CCC transcript event list for a VS Code Copilot Chat session:
    the flat .json snapshot or the replayed .jsonl operation journal. A
    session whose file yields no requests returns an empty transcript — the
    row still lists (title/timestamps came from the index/file metadata)."""
    path = _core._copilotchat_session_file(session_id)
    if path is None:
        return {"events": [], "last_line": 0}
    events, line = _copilotchat_events_from_file(path)
    if after_line and after_line > 0:
        visible = [e for e in events if e["line"] > after_line]
    else:
        visible = events
    return {"events": visible, "last_line": line}


def _antigravity_unquote(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    for _ in range(2):
        if not text:
            break
        if text[0] in ("'", '"', "[", "{"):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                break
            if isinstance(parsed, str):
                text = parsed.strip()
                continue
            return parsed
        break
    return text.strip().strip("`")


_ANTIGRAVITY_USER_REQUEST_RE = re.compile(
    r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>",
    re.IGNORECASE | re.DOTALL,
)
_ANTIGRAVITY_TAG_RE = re.compile(r"<[^>\n]+>")
_ANTIGRAVITY_FILE_URL_RE = re.compile(r"file://[^\s`'\"<>)\]]+")
_UUID_TEXT_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_ANTIGRAVITY_CLI_CONVERSATION_RE = re.compile(
    r"(?:Created conversation|conversation=|Streaming conversation|to conversation)\s*("
    + _UUID_TEXT_RE
    + r")",
    re.IGNORECASE,
)
_ANTIGRAVITY_CLI_WORKSPACE_RE = re.compile(
    r"Initializing CLI store manager for workspace ([^\n]+)"
)
_ANTIGRAVITY_MODEL_SELECTION_RE = re.compile(
    r"Model Selection`\s+from\s+.*?\s+to\s+(.+?)(?:\.\s+No need|\n|</USER_SETTINGS_CHANGE>|$)",
    re.IGNORECASE | re.DOTALL,
)
_ANTIGRAVITY_MODEL_LABEL_RE = re.compile(
    r"Propagating selected model override to backend:\s+label=\"([^\"]+)\"",
    re.IGNORECASE,
)
_ANTIGRAVITY_PRINT_MODEL_RE = re.compile(
    r"Print mode:\s+starting\s+\([^\n)]*model=\"([^\"]*)\"",
    re.IGNORECASE,
)
_ANTIGRAVITY_APP_LS_URL_RE = re.compile(r"https://127\.0\.0\.1:(\d+)/")
_ANTIGRAVITY_APP_LS_TOKEN_RE = re.compile(
    r"--csrf_token\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
_ANTIGRAVITY_META_VERSION = 3
# An Antigravity transcript writes every assistant tool call / message, so if
# the file moved within this window we treat the session as live. Antigravity
# has no hook system that CCC could listen to (the way Claude Code does via
# sidecar markers), so an mtime gate is the cheapest "is the agent actually
# doing something right now" signal we can derive without polling processes.
_ANTIGRAVITY_LIVE_WINDOW_S = 180


def _antigravity_read_log_tail(path, max_bytes=64000):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(-max_bytes, os.SEEK_END)
            raw = fh.read()
        return raw.decode("utf-8", "replace")
    except OSError:
        return ""


def _antigravity_app_ls_candidates():
    text = _core._antigravity_read_log_tail(_core.ANTIGRAVITY_MAIN_LOG, max_bytes=512000)
    tokens = _ANTIGRAVITY_APP_LS_TOKEN_RE.findall(text or "")
    latest_token = tokens[-1] if tokens else ""
    candidates = []
    current_token = ""
    for line in (text or "").splitlines():
        token_match = _ANTIGRAVITY_APP_LS_TOKEN_RE.search(line)
        if token_match:
            current_token = token_match.group(1)
        url_match = _ANTIGRAVITY_APP_LS_URL_RE.search(line)
        if url_match and current_token:
            candidates.append({"port": url_match.group(1), "token": current_token})

    if latest_token:
        try:
            lsof = subprocess.run(
                ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            for line in (lsof.stdout or "").splitlines():
                if "language_" not in line:
                    continue
                match = re.search(r"127\.0\.0\.1:(\d+)\s+\(LISTEN\)", line)
                if match:
                    candidates.append({"port": match.group(1), "token": latest_token})
        except (OSError, subprocess.SubprocessError):
            pass

    seen = set()
    out = []
    for candidate in reversed(candidates):
        port = str(candidate.get("port") or "")
        token = str(candidate.get("token") or "")
        if not port.isdigit() or not token:
            continue
        key = (port, token)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "base_url": f"https://127.0.0.1:{port}",
            "token": token,
            "port": int(port),
        })
    return out


def _antigravity_app_rpc(method, payload, timeout=8):
    method = (method or "").strip().strip("/")
    if not method:
        return {"ok": False, "error": "missing Antigravity RPC method"}
    candidates = _antigravity_app_ls_candidates()
    if not candidates:
        return {
            "ok": False,
            "error": "Antigravity app language server is not running. Open Antigravity, then retry.",
            "code": "antigravity_app_unavailable",
            "via": "antigravity-app",
        }
    data = json.dumps(payload or {}).encode("utf-8")
    last_error = ""
    ctx = ssl._create_unverified_context()
    for candidate in candidates:
        url = f"{candidate['base_url']}/{_core.ANTIGRAVITY_APP_LS_SERVICE}/{method}"
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-codeium-csrf-token": candidate["token"],
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw}
            return {
                "ok": True,
                "response": body,
                "via": "antigravity-app",
                "port": candidate["port"],
            }
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")
            except OSError:
                pass
            error = raw.strip() or str(exc)
            try:
                parsed = json.loads(raw) if raw else {}
                error = parsed.get("message") or parsed.get("error") or error
                code = parsed.get("code") or "antigravity_app_rpc_error"
            except json.JSONDecodeError:
                code = "antigravity_app_rpc_error"
            if exc.code in (404, 405):
                last_error = error
                continue
            return {
                "ok": False,
                "error": error,
                "code": code,
                "status": exc.code,
                "via": "antigravity-app",
            }
        except (OSError, TimeoutError) as exc:
            last_error = str(exc)
            continue
    return {
        "ok": False,
        "error": last_error or "Antigravity app language server is unreachable. Open Antigravity, then retry.",
        "code": "antigravity_app_unavailable",
        "via": "antigravity-app",
    }


def _antigravity_user_config_has_model(config):
    if not isinstance(config, dict):
        return False
    planner = config.get("plannerConfig")
    if not isinstance(planner, dict):
        return False
    return bool(planner.get("requestedModel") or planner.get("planModel"))


def _antigravity_latest_user_config(session_id):
    """Find the most recent userConfig with a model in the App's trajectory.

    Returns a dict with either {"ok": True, "config": <cfg>} on success,
    {"ok": False, "rpc": <rpc-result>} when the RPC itself failed (app
    not running, session not loaded, etc.), or {"ok": False, "rpc": None}
    when the RPC succeeded but the trajectory has no usable model — the
    session is loaded but the user never picked one. The caller uses the
    distinction to surface the right error.
    """
    rpc_result = _core._antigravity_app_rpc(
        "GetCascadeTrajectory",
        {"cascadeId": session_id},
        timeout=5,
    )
    if not rpc_result.get("ok"):
        return {"ok": False, "rpc": rpc_result}
    trajectory = (rpc_result.get("response") or {}).get("trajectory") or {}
    steps = trajectory.get("steps") or []
    for step in reversed(steps):
        user_input = (step or {}).get("userInput") or {}
        for key in ("userConfig", "lastUserConfig"):
            config = user_input.get(key)
            if _antigravity_user_config_has_model(config):
                return {"ok": True, "config": copy.deepcopy(config)}
    return {"ok": False, "rpc": None}


_antigravity_cli_log_meta_cache = {}


def _antigravity_cli_log_meta(path):
    # Pure function of the log file's content, but the archive scan calls it
    # once per antigravity row (~780×) and each call re-reads the tail and runs
    # several regexes — uncached, this was the dominant antigravity cost on
    # every poll. Memoise by (mtime, size); a stale/rewritten log invalidates.
    try:
        _st = os.stat(path)
        _key = (_st.st_mtime_ns, _st.st_size)
    except OSError:
        _key = None
    if _key is not None:
        _cached = _antigravity_cli_log_meta_cache.get(str(path))
        if _cached is not None and _cached[0] == _key:
            return _cached[1]
    text = _core._antigravity_read_log_tail(path)
    if not text:
        if _key is not None:
            _antigravity_cli_log_meta_cache[str(path)] = (_key, {})
        return {}
    session_id = ""
    for match in _ANTIGRAVITY_CLI_CONVERSATION_RE.finditer(text):
        session_id = match.group(1)
    workspace = ""
    for match in _ANTIGRAVITY_CLI_WORKSPACE_RE.finditer(text):
        workspace = match.group(1).strip().strip('"')
    model = _antigravity_model_from_text(text)
    result = {"session_id": session_id, "cwd": workspace, "model": model}
    if _key is not None:
        _antigravity_cli_log_meta_cache[str(path)] = (_key, result)
    return result


def _antigravity_model_from_text(text):
    if not isinstance(text, str) or not text:
        return ""

    def _normalize(candidate):
        # Antigravity logs sometimes carry its internal placeholder IDs
        # (MODEL_PLACEHOLDER_M16, _M47, _M50, …) instead of a human-readable
        # name. Those IDs leak into the row's model chip and read as gibberish
        # to the user. Drop them so the chip stays blank — better empty than
        # noise — and the renderer falls back to the engine label.
        if candidate.startswith("MODEL_PLACEHOLDER_"):
            return ""
        return candidate

    model = ""
    for match in _ANTIGRAVITY_MODEL_SELECTION_RE.finditer(text):
        candidate = _normalize(" ".join((match.group(1) or "").split()))
        if candidate:
            model = candidate
    for match in _ANTIGRAVITY_MODEL_LABEL_RE.finditer(text):
        candidate = _normalize(" ".join((match.group(1) or "").split()))
        if candidate:
            model = candidate
    for match in _ANTIGRAVITY_PRINT_MODEL_RE.finditer(text):
        candidate = _normalize(" ".join((match.group(1) or "").split()))
        if candidate:
            model = candidate
    return model


def _antigravity_spawn_pid_by_session_id():
    out = {}
    entries = list(_core._spawned_sessions)
    try:
        entries.extend(_core._load_spawn_registry())
    except Exception:
        pass
    for s in entries:
        if s.get("engine") != "antigravity":
            continue
        meta = {}
        log = s.get("log") or ""
        if log:
            meta = _antigravity_cli_log_meta(str(log) + ".agy.log")
            if not (meta.get("session_id") or meta.get("cwd") or meta.get("model")):
                meta = _antigravity_cli_log_meta(log)
        log_sid = meta.get("session_id") or ""
        sid = s.get("session_id") or s.get("resumed_sid")
        if log_sid and sid != log_sid:
            sid = log_sid
            s["session_id"] = log_sid
            _core._update_spawn_session_id_in_registry(s.get("pid"), log_sid)
        elif not sid:
            sid = log_sid
        if sid:
            if not s.get("session_id"):
                s["session_id"] = sid
                _core._update_spawn_session_id_in_registry(s.get("pid"), sid)
            if sid not in out:
                try:
                    alive = _core._poll_spawn_entry(s) is None
                except Exception:
                    alive = False
                out[sid] = {
                    "pid": s.get("pid"),
                    "alive": alive,
                    "log": log,
                    "cwd": meta.get("cwd") or s.get("cwd") or "",
                    "repo_path": s.get("repo_path") or "",
                    "spawned_at": s.get("started") or "",
                    "prompt": s.get("prompt") or "",
                    "model": meta.get("model") or s.get("model") or "",
                    "parent_session_id": s.get("parent_session_id") or "",
                }
    return out


def _antigravity_cli_log_paths(repo_path=None):
    paths = []
    seen = set()

    def add_path(path):
        key = str(path)
        if key in seen or not path.is_file():
            return
        seen.add(key)
        paths.append(path)

    repo_log_roots = []
    if repo_path:
        repo_log_roots.append(repo_path)
    else:
        repo_log_roots.append(str(Path.cwd()))
        repo_log_roots.extend(_core._load_recent_repos())
        repo_log_roots.extend(_core._load_custom_repos())
    for root_path in repo_log_roots:
        try:
            log_dir = _core.repo_log_dir(root_path)
            for pattern in ("spawn-antigravity-*.log.agy.log", "resume-antigravity-*.log.agy.log"):
                for path in log_dir.glob(pattern):
                    add_path(path)
        except OSError:
            pass
    cli_log_dir = _core.ANTIGRAVITY_CLI_HOME / "log"
    if cli_log_dir.is_dir():
        try:
            for path in cli_log_dir.glob("*.log"):
                add_path(path)
        except OSError:
            pass
    try:
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        paths.sort(key=lambda p: str(p), reverse=True)
    return paths


def _antigravity_cli_log_meta_for_session(session_id, repo_path=None):
    if not session_id or not _core._SESSION_UUID_RE.match(str(session_id)):
        return {}
    for path in _antigravity_cli_log_paths(repo_path):
        meta = _antigravity_cli_log_meta(path)
        if meta.get("session_id") == session_id:
            return {**meta, "log_path": str(path)}
    return {}


def _antigravity_log_display_name(path):
    name = Path(path).name
    name = re.sub(r"\.agy\.log$", "", name)
    name = re.sub(r"^(spawn|resume)-antigravity-", "", name)
    name = re.sub(r"-\d{8}T\d{6}\.log$", "", name)
    label = name.replace("-", " ").strip()
    return label or "Antigravity CLI session"


def _antigravity_cli_log_diagnostic(text):
    if not text:
        return ""
    patterns = (
        r"RESOURCE_EXHAUSTED.*?Resets in [^\n.]+",
        r"trajectory not found[^\n]*",
        r"Error: [^\n]+",
    )
    for pattern in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if matches:
            return matches[-1].group(0).strip()
    match = re.search(r"Print mode: conversation=([0-9a-f-]+)", text, re.IGNORECASE)
    if match:
        return f"AGY print mode created conversation {match.group(1)}, but returned no stdout."
    return ""


def _parse_antigravity_cli_log_conversation(session_id, after_line=0):
    meta = _antigravity_cli_log_meta_for_session(session_id)
    log_path = meta.get("log_path")
    if not log_path:
        return {"events": [], "last_line": 0}
    events = []
    line_num = 0
    prompt_label = ""
    log_name = Path(log_path).name
    if log_name.startswith("spawn-antigravity-"):
        prompt_label = _antigravity_log_display_name(log_path)
    if prompt_label:
        line_num += 1
        if line_num > after_line:
            events.append({
                "line": line_num,
                "ts": "",
                "type": "user_text",
                "text": prompt_label,
                "images": [],
            })
    stdout_path = str(log_path)
    if stdout_path.endswith(".agy.log"):
        stdout_path = stdout_path[:-8]
    stdout_text = ""
    if stdout_path and os.path.exists(stdout_path):
        stdout_text = _core._antigravity_read_log_tail(stdout_path, max_bytes=24000).strip()
    if stdout_text:
        line_num += 1
        if line_num > after_line:
            events.append({
                "line": line_num,
                "ts": "",
                "type": "assistant",
                "message_id": f"antigravity-cli-{line_num}",
                "blocks": [{"kind": "text", "text": stdout_text}],
            })
    debug_text = _core._antigravity_read_log_tail(log_path, max_bytes=24000)
    diagnostic = _antigravity_cli_log_diagnostic(debug_text)
    if stdout_text and diagnostic.startswith("AGY print mode created"):
        diagnostic = ""
    if diagnostic:
        line_num += 1
        if line_num > after_line:
            events.append({
                "line": line_num,
                "ts": "",
                "type": "assistant",
                "message_id": f"antigravity-cli-{line_num}",
                "blocks": [{"kind": "text", "text": diagnostic}],
            })
    return {"events": events, "last_line": line_num}


def _antigravity_user_text(content):
    if not isinstance(content, str):
        return ""
    match = _ANTIGRAVITY_USER_REQUEST_RE.search(content)
    if match:
        text = match.group(1)
    else:
        # USER_INPUT often wraps the prompt in metadata tags. If no explicit
        # request block exists, drop tag lines and keep the human-readable text.
        text = _ANTIGRAVITY_TAG_RE.sub("", content)
    return _core._strip_ccc_session_state_instruction(text).strip()


def _antigravity_event_epoch(ev):
    return _core._iso_ts_epoch((ev or {}).get("created_at"))


def _antigravity_event_timestamp(ev):
    return (ev or {}).get("created_at") or ""


def _antigravity_tool_name(call):
    if not isinstance(call, dict):
        return "tool"
    return (call.get("name") or "tool").rsplit(".", 1)[-1]


def _antigravity_tool_args(call):
    args = call.get("args") if isinstance(call, dict) else {}
    return args if isinstance(args, dict) else {}


def _antigravity_arg(args, *keys):
    for key in keys:
        if key in args:
            value = _antigravity_unquote(args.get(key))
            if isinstance(value, str):
                return value
    return ""


def _antigravity_tool_command(call):
    args = _antigravity_tool_args(call)
    cmd = _antigravity_arg(args, "CommandLine", "command", "Command")
    return cmd if isinstance(cmd, str) else ""


def _antigravity_tool_cwd(call):
    args = _antigravity_tool_args(call)
    cwd = _antigravity_arg(args, "Cwd", "cwd", "WorkingDirectory")
    return cwd if isinstance(cwd, str) else ""


def _antigravity_tool_detail(call):
    args = _antigravity_tool_args(call)
    cmd = _antigravity_tool_command(call)
    if cmd:
        return _core._shell_command_activity_label(cmd, max_len=1200)
    for key in (
        "TargetFile", "AbsolutePath", "DirectoryPath", "SearchPath", "File",
        "Path", "Query", "Prompt", "Message", "Description", "Instruction",
        "toolSummary", "toolAction",
    ):
        value = _antigravity_arg(args, key)
        if isinstance(value, str) and value:
            return _core._prompt_fragment(value, 240)
    return ""


def _antigravity_embedded_system_message(content):
    """True when Antigravity smuggles an internal Message into model text."""
    if not isinstance(content, str):
        return False
    m = _core._ANTIGRAVITY_EMBEDDED_MESSAGE_RE.match(content.strip())
    if not m:
        return False
    sender = m.group("sender") or ""
    priority = m.group("priority") or ""
    body = (m.group("content") or "").strip()
    if sender == "system":
        return True
    if priority.startswith("MESSAGE_PRIORITY_") and body.startswith("[Task "):
        return True
    return False


def _antigravity_normalize_path(value):
    raw = _antigravity_unquote(value)
    if not isinstance(raw, str):
        return ""
    raw = raw.strip().strip("`").rstrip(".,;:")
    if not raw:
        return ""
    if raw.startswith("file://"):
        try:
            parsed = urllib.parse.urlsplit(raw)
            raw = urllib.parse.unquote(parsed.path or "")
        except ValueError:
            raw = raw[len("file://"):]
    raw = urllib.parse.unquote(raw)
    if raw.startswith("~/") or raw == "~":
        raw = os.path.expanduser(raw)
    if not os.path.isabs(raw):
        return ""
    return raw


_antigravity_projects_cache = {"mtime": None, "paths": []}


def _antigravity_known_project_paths():
    path = _core.GEMINI_HOME / "projects.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if _antigravity_projects_cache.get("mtime") == mtime:
        return list(_antigravity_projects_cache.get("paths") or [])
    paths = []
    try:
        data = json.loads(path.read_text())
        projects = data.get("projects") if isinstance(data, dict) else {}
        if isinstance(projects, dict):
            for raw in projects.keys():
                try:
                    p = Path(raw).expanduser().resolve()
                except (OSError, ValueError, RuntimeError):
                    continue
                if p.is_dir():
                    paths.append(p)
    except (OSError, json.JSONDecodeError, ValueError):
        paths = []
    paths.sort(key=lambda p: len(str(p)), reverse=True)
    _antigravity_projects_cache.update({"mtime": mtime, "paths": paths})
    return list(paths)


def _antigravity_path_base(path_text):
    normalized = _antigravity_normalize_path(path_text)
    if not normalized:
        return None
    p = Path(normalized)
    try:
        if p.exists():
            return p if p.is_dir() else p.parent
    except OSError:
        pass
    suffix = p.suffix
    return p.parent if suffix else p


def _antigravity_infer_cwd_from_candidates(candidates):
    fallback = ""
    known_projects = _antigravity_known_project_paths()
    try:
        home_path = Path.home().resolve()
    except (OSError, ValueError, RuntimeError):
        home_path = None

    for raw in candidates or []:
        base = _antigravity_path_base(raw)
        if not base:
            continue
        try:
            base_resolved = base.expanduser().resolve(strict=False)
        except (OSError, ValueError, RuntimeError):
            base_resolved = base

        if home_path:
            if base_resolved == home_path:
                continue
            skip = False
            for sys_dir in (
                home_path / ".claude",
                home_path / ".gemini",
                home_path / ".config",
                home_path / ".cache",
                Path("/tmp"),
                Path("/private/tmp"),
            ):
                if base_resolved == sys_dir or sys_dir in base_resolved.parents:
                    skip = True
                    break
            if skip:
                continue

        if not fallback:
            fallback = str(base_resolved)

        matched_project = None
        for project in known_projects:
            try:
                p_res = project.resolve()
                if p_res != home_path and (base_resolved == p_res or p_res in base_resolved.parents):
                    matched_project = p_res
                    break
            except (OSError, RuntimeError):
                continue

        if matched_project and matched_project != home_path:
            return str(matched_project)

        git_root = _core._find_git_root(str(base_resolved))
        if git_root and Path(git_root).resolve() != home_path:
            try:
                return str(Path(git_root).resolve())
            except (OSError, ValueError, RuntimeError):
                return git_root

        if matched_project and matched_project != home_path:
            return str(matched_project)

        marked = _core._nearest_marked_repo_dir(str(base_resolved))
        if marked and Path(marked).resolve() != home_path:
            return marked

    if fallback and home_path:
        try:
            f_res = Path(fallback).resolve()
            if f_res != home_path:
                for sys_dir in (
                    home_path / ".claude",
                    home_path / ".gemini",
                    home_path / ".config",
                    home_path / ".cache",
                    Path("/tmp"),
                    Path("/private/tmp"),
                ):
                    if f_res == sys_dir or sys_dir in f_res.parents:
                        return ""
                return fallback
        except (OSError, ValueError, RuntimeError):
            pass
    return ""


def _antigravity_event_path_candidates(ev):
    candidates = []
    content = ev.get("content")
    if isinstance(content, str):
        for m in _ANTIGRAVITY_FILE_URL_RE.finditer(content):
            candidates.append(m.group(0))
    for call in ev.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        args = _antigravity_tool_args(call)
        for key in (
            "Cwd", "cwd", "DirectoryPath", "SearchPath", "AbsolutePath",
            "TargetFile", "File", "Path",
        ):
            value = args.get(key)
            if isinstance(value, str):
                candidates.append(value)
    return candidates


_antigravity_tail_resume = {}  # path -> {offset, pos, pending_tool, cwd_candidates, meta, meta_version}


def _extract_antigravity_tail_meta(path_or_session_id):
    path = Path(path_or_session_id) if isinstance(path_or_session_id, (str, Path)) else None
    if path and not path.is_file():
        path = _core._antigravity_transcript_path(path_or_session_id)
    if not path:
        return {}
    try:
        st = path.stat()
    except OSError:
        return {}
    mtime = st.st_mtime
    size = st.st_size
    spath = str(path)
    cached = _core._conv_meta_cache.get(spath)
    if (
        cached
        and cached.get("mtime") == mtime
        and cached.get("engine") == "antigravity"
        and cached.get("meta_version") == _ANTIGRAVITY_META_VERSION
    ):
        return cached

    # Incremental resume from the last byte offset — antigravity transcripts
    # are append-only JSONL. See _codex_tail_resume for the rationale.
    with _core._conv_meta_cache_lock:
        resume = _core._antigravity_tail_resume.get(spath)
    if (
        resume
        and resume.get("meta_version") == _ANTIGRAVITY_META_VERSION
        and size >= resume.get("offset", 0)
    ):
        meta = resume["meta"]
        cwd_candidates = resume["cwd_candidates"]
        pending_tool = resume["pending_tool"]
        pos = resume["pos"]
        start_offset = resume["offset"]
    else:
        meta = {
            "engine": "antigravity",
            "meta_version": _ANTIGRAVITY_META_VERSION,
            "mtime": mtime,
            "first_message": None,
            "last_prompt": None,
            "last_assistant_text": None,
            "last_event_type": None,
            "last_meaningful_ts": 0,
            "pending_tool": None,
            "pending_file": None,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_edit_pos": 0,
            "last_commit_pos": 0,
            "last_push_pos": 0,
            "tail_pr_number": None,
            "tail_pr_url": None,
            "tail_branch": None,
            "tail_worktree_path": None,
            "has_external_cd": False,
            "cwd": None,
            "model": None,
            "latest_input_tokens": 0,
            "context_limit": 0,
        }
        cwd_candidates = []
        pending_tool = False
        pos = 0
        start_offset = 0
    pr_url_re = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d{1,7})")
    try:
        with open(path, "rb") as f:
            if start_offset:
                f.seek(start_offset)
            while True:
                raw = f.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    break
                start_offset += len(raw)
                pos += 1
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_epoch = _antigravity_event_epoch(ev)
                if ts_epoch:
                    meta["last_meaningful_ts"] = ts_epoch
                event_cwd = ev.get("cwd")
                if isinstance(event_cwd, str) and event_cwd.strip():
                    meta["cwd"] = event_cwd.strip()
                cwd_candidates.extend(_antigravity_event_path_candidates(ev))
                ev_type = ev.get("type") or ""
                source = ev.get("source") or ""
                content_text = ev.get("content") if isinstance(ev.get("content"), str) else ""
                selected_model = _antigravity_model_from_text(content_text)
                if selected_model:
                    meta["model"] = selected_model
                if ev_type == "USER_INPUT" and source == "USER_EXPLICIT":
                    text = _antigravity_user_text(ev.get("content") or "")
                    if text:
                        meta["first_message"] = meta["first_message"] or text
                        meta["last_prompt"] = text
                    meta["last_event_type"] = "user"
                    meta["pending_tool"] = None
                    meta["pending_file"] = None
                    pending_tool = False
                    continue
                if ev_type == "PLANNER_RESPONSE":
                    content = (ev.get("content") or "").strip()
                    if content:
                        meta["last_assistant_text"] = content
                        meta.update(_core._extract_codex_summary_signals(content, pr_url_re))
                    meta["last_event_type"] = "assistant"
                    calls = [c for c in (ev.get("tool_calls") or []) if isinstance(c, dict)]
                    for call in calls:
                        name = _antigravity_tool_name(call)
                        detail = _antigravity_tool_detail(call)
                        meta["pending_tool"] = name
                        meta["pending_file"] = detail[:80] if isinstance(detail, str) else None
                        pending_tool = True
                        lname = name.lower()
                        if lname in ("write_to_file", "replace_file_content", "multi_replace_file_content"):
                            meta["has_edit"] = True
                            meta["last_edit_pos"] = pos
                        cmd = _antigravity_tool_command(call)
                        if cmd:
                            signals = _core._codex_command_signals(
                                cmd,
                                base_cwd=_antigravity_tool_cwd(call) or meta.get("cwd"),
                            )
                            if signals["edit"]:
                                meta["has_edit"] = True
                                meta["last_edit_pos"] = pos
                            if signals["commit"]:
                                meta["has_commit"] = True
                                meta["last_commit_pos"] = pos
                            if signals["push"]:
                                meta["has_push"] = True
                                meta["last_push_pos"] = pos
                            if signals["external_cd"]:
                                meta["has_external_cd"] = True
                            if signals.get("worktree_path"):
                                meta["tail_worktree_path"] = signals["worktree_path"]
                            if signals.get("worktree_branch"):
                                meta["tail_branch"] = signals["worktree_branch"]
                    continue
                if ev_type in ("CONVERSATION_HISTORY", "SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE", "CHECKPOINT"):
                    continue
                if pending_tool:
                    meta["pending_tool"] = None
                    meta["pending_file"] = None
                    pending_tool = False
                if ev_type == "CODE_ACTION":
                    meta["has_edit"] = True
                    meta["last_edit_pos"] = pos
                content = content_text
                if isinstance(content, str):
                    if pr_url_re.search(content):
                        meta.update(_core._extract_codex_summary_signals(content, pr_url_re))
                meta["last_event_type"] = "result"
            end_offset = start_offset
    except OSError:
        return {}

    meta["mtime"] = mtime
    meta["cwd"] = meta.get("cwd") or _antigravity_infer_cwd_from_candidates(cwd_candidates)
    if not meta.get("last_meaningful_ts"):
        meta["last_meaningful_ts"] = mtime
    with _core._conv_meta_cache_lock:
        _core._conv_meta_cache[spath] = meta
        _core._antigravity_tail_resume[spath] = {
            "meta_version": _ANTIGRAVITY_META_VERSION,
            "offset": end_offset,
            "pos": pos,
            "pending_tool": pending_tool,
            "cwd_candidates": cwd_candidates,
            "meta": meta,
        }
        _core._conv_meta_cache_dirty = True
    return meta


def _extract_antigravity_cwd(session_id):
    path = _core._antigravity_transcript_path(session_id)
    if path and path.is_file():
        tail = _core._extract_antigravity_tail_meta(path) or {}
        if tail.get("cwd"):
            return tail.get("cwd") or ""
    meta = _antigravity_cli_log_meta_for_session(session_id)
    return meta.get("cwd") or ""


def _antigravity_activity_fields_from_tail(tail, live):
    fields = {
        "sidecar_status": None,
        "sidecar_has_writes": False,
        "sidecar_tool": None,
        "sidecar_file": None,
        "sidecar_ts": 0,
        "sidecar_in_flight": False,
        "question_waiting": False,
        "question_text": "",
        "question_header": "",
        "question_preamble": "",
        "question_options": [],
        "question_option_details": [],
    }
    if not live:
        return fields
    ts = tail.get("last_meaningful_ts") or int(time.time()) if tail else int(time.time())
    pending_tool = tail.get("pending_tool") if tail else None
    if pending_tool:
        fields.update({
            "sidecar_status": "active",
            "sidecar_tool": pending_tool,
            "sidecar_file": tail.get("pending_file"),
            "sidecar_ts": ts,
            "sidecar_in_flight": True,
        })
        return fields
    if not tail or tail.get("last_event_type") in (None, "user", "assistant"):
        fields.update({
            "sidecar_status": "active",
            "sidecar_tool": "Thinking",
            "sidecar_file": None,
            "sidecar_ts": ts,
            "sidecar_in_flight": True,
        })
    return fields


def find_antigravity_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    paths = _core._antigravity_transcript_paths()
    repo_path_obj = None
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
    spawn_by_sid = _antigravity_spawn_pid_by_session_id()
    summary_titles = _core._load_antigravity_summary_titles()
    git_top_cache = {}
    # Build a session_id → cli-log meta map ONCE for this scan.
    # The old per-row `_antigravity_cli_log_meta_for_session` re-iterated every
    # cli log file and re-parsed each one looking for the session_id — for 15
    # missing-cwd rows × ~10 logs that was ~1.0s per /api/sessions poll. One
    # pass collapses that to a single sweep, ~70ms.
    cli_meta_by_sid = {}
    try:
        cli_log_paths = _antigravity_cli_log_paths(repo_path if repo_only else None)
    except Exception:
        cli_log_paths = []
    for _log_path in cli_log_paths:
        try:
            _meta = _antigravity_cli_log_meta(_log_path)
        except Exception:
            continue
        _msid = _meta.get("session_id") or ""
        if not _msid or _msid in cli_meta_by_sid:
            continue
        cli_meta_by_sid[_msid] = {**_meta, "log_path": str(_log_path)}
    out = []
    scanned = 0
    seen_sids = set()
    for path in paths:
        if limit and scanned >= int(limit):
            break
        sid = path.parent.parent.parent.name
        if not sid:
            continue
        seen_sids.add(sid)
        scanned += 1
        try:
            st = path.stat()
        except OSError:
            continue
        tail = _core._extract_antigravity_tail_meta(path) or {}
        spawn_info = spawn_by_sid.get(sid) or {}
        cli_meta = {}
        cwd = tail.get("cwd") or spawn_info.get("cwd") or ""
        if not cwd or not tail.get("model"):
            cli_meta = cli_meta_by_sid.get(sid, {})
            cwd = cwd or cli_meta.get("cwd") or spawn_info.get("cwd") or ""
        pinned = repo_pins.get(sid)
        pinned_repo = False
        if repo_only:
            if pinned and pinned != repo_path:
                continue
            if pinned == repo_path:
                pinned_repo = True
            else:
                cli_cwd = (cli_meta or cli_meta_by_sid.get(sid, {})).get("cwd") or ""
                spawn_cwd = spawn_info.get("cwd") or ""
                spawn_repo = spawn_info.get("repo_path") or ""
                matched = (
                    _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache)
                    or _core._codex_cwd_matches_repo(cli_cwd, repo_path_obj, git_top_cache)
                    or _core._codex_cwd_matches_repo(spawn_cwd, repo_path_obj, git_top_cache)
                    or _core._codex_cwd_matches_repo(spawn_repo, repo_path_obj, git_top_cache)
                )
                if not matched:
                    continue
                if not _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                    cwd = (
                        (cli_cwd if _core._codex_cwd_matches_repo(cli_cwd, repo_path_obj, git_top_cache) else "")
                        or (spawn_cwd if _core._codex_cwd_matches_repo(spawn_cwd, repo_path_obj, git_top_cache) else "")
                        or (spawn_repo if _core._codex_cwd_matches_repo(spawn_repo, repo_path_obj, git_top_cache) else "")
                        or cwd
                    )
        spawn_pid = spawn_info.get("pid")
        spawn_alive = bool(spawn_info.get("alive"))
        modified = tail.get("last_meaningful_ts") or st.st_mtime
        is_live = spawn_alive if spawn_pid else (time.time() - modified) < _ANTIGRAVITY_LIVE_WINDOW_S
        if spawn_info.get("log"):
            try:
                modified = max(modified, Path(spawn_info["log"]).stat().st_mtime)
            except OSError:
                pass
        freshness = max(modified, last_interactions.get(sid) or 0)
        if not include_old and sid not in spawn_by_sid and cutoff > 0 and freshness < cutoff:
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
        first_message = (tail.get("first_message") or "").strip()
        ai_title = summary_titles.get(sid)
        display_name = (
            name_overrides.get(sid)
            or ai_title
            or (first_message[:80] if first_message else None)
            or ((spawn_info.get("prompt") or "").strip()[:80] if spawn_info.get("prompt") else None)
            or "Antigravity session"
        )
        tail_worktree_path = tail.get("tail_worktree_path") or ""
        effective_cwd = tail_worktree_path or cwd
        try:
            cwd_exists = bool(effective_cwd and Path(effective_cwd).is_dir())
        except OSError:
            cwd_exists = False
        folder_path = pinned or cwd or effective_cwd or ""
        if folder_path:
            _git_root = _core._find_git_root(folder_path)
            folder_label = _core._resolve_dir_case(_git_root or folder_path)
        else:
            folder_label = "Antigravity"
        _wt_worktree_label = None
        _wt_idx = folder_label.find("-wt-")
        if _wt_idx > 0:
            _wt_worktree_label = folder_label[_wt_idx + 4:]
            folder_label = folder_label[:_wt_idx]
        branch = tail.get("tail_branch") or _core._git_branch_for_cwd(effective_cwd)
        ag_latest, ag_limit = _antigravity_list_context_usage(sid, is_live=is_live)
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "antigravity",
            "engine": "antigravity",
            "timestamp": "",
            "branch": branch,
            "git_branch": branch,
            "first_message": first_message[:200],
            "display_name": display_name,
            "ai_title": ai_title or None,
            "name_overridden": bool(name_overrides.get(sid)),
            "last_prompt": (tail.get("last_prompt") or "")[:200],
            "size": st.st_size,
            "modified": modified,
            "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)),
            "mtime": modified,
            "jsonl_path": str(path),
            "folder_label": folder_label,
            "folder_path": folder_path,
            "worktree_label": _wt_worktree_label,
            "session_cwd": effective_cwd,
            "session_cwd_exists": cwd_exists,
            "session_cwd_is_worktree": bool(
                tail_worktree_path or (effective_cwd and (Path(effective_cwd) / ".git").is_file())
            ),
            "worktree_dirty": (
                _core._worktree_dirty_cached(effective_cwd, modified)
                if resolve_worktree_dirty and effective_cwd else False
            ),
            "effective_branch": tail.get("tail_branch") or None,
            "effective_kind": "worktree" if tail_worktree_path else None,
            "has_edit": tail.get("has_edit", False),
            "has_commit": tail.get("has_commit", False),
            "has_push": tail.get("has_push", False),
            "last_edit_pos": tail.get("last_edit_pos", 0),
            "last_commit_pos": tail.get("last_commit_pos", 0),
            "last_push_pos": tail.get("last_push_pos", 0),
            "last_event_type": tail.get("last_event_type"),
            "pending_tool": tail.get("pending_tool"),
            "pending_file": tail.get("pending_file"),
            "last_assistant_text": tail.get("last_assistant_text"),
            "tail_issue_number": None,
            "tail_pr_number": tail.get("tail_pr_number"),
            "tail_pr_url": tail.get("tail_pr_url"),
            "pr_state": None,
            "session_state": _core._parse_session_state(tail.get("last_assistant_text")),
            "archived": sid in archived_set,
            "trashed": sid in trashed_set,
            "verified": sid in verified_set,
            "pinned_repo": pinned_repo,
            "last_interacted": last_interactions.get(sid),
            "is_live": is_live,
            "spawn_pid": spawn_pid,
            "parent_session_id": spawn_info.get("parent_session_id") or "",
            "can_headless_resume": bool(
                _core._antigravity_cli_conversation_path(sid)
                and _core._antigravity_cli_conversation_path(sid).is_file()
            ),
            "can_app_resume": bool(_core._antigravity_app_conversation_path(sid)),
            **_antigravity_activity_fields_from_tail(tail, is_live),
            "needs_approval": False,
            "needs_approval_message": "",
            "model": tail.get("model") or cli_meta.get("model") or spawn_info.get("model") or "",
            "reasoning_effort": "",
            "latest_input_tokens": ag_latest,
            "context_limit": ag_limit,
        })
    # Synthesize stub rows for live AGY spawns whose JSONL transcript hasn't
    # materialized on disk yet. AGY may need several seconds to expose its own
    # conversation ID and brain transcript. Without this,
    # an agent that spawned a sibling AGY session would not see the new row in
    # the conv list until that file landed — defeating the spawn_registry_count
    # ping that's supposed to surface spawns within ~5s.
    for sid, spawn_info in spawn_by_sid.items():
        if sid in seen_sids:
            continue
        if not spawn_info.get("alive"):
            continue
        cwd = spawn_info.get("cwd") or ""
        pinned = repo_pins.get(sid)
        pinned_repo = False
        if repo_only:
            if pinned and pinned != repo_path:
                continue
            if pinned == repo_path:
                pinned_repo = True
            elif not _core._codex_cwd_matches_repo(cwd, repo_path_obj, git_top_cache):
                continue
        try:
            modified = Path(spawn_info["log"]).stat().st_mtime if spawn_info.get("log") else time.time()
        except OSError:
            modified = time.time()
        prompt = (spawn_info.get("prompt") or "").strip()
        ai_title = summary_titles.get(sid)
        display_name = (
            name_overrides.get(sid)
            or ai_title
            or (prompt[:80] if prompt else None)
            or "Antigravity session"
        )
        folder_path = pinned or cwd or ""
        try:
            cwd_exists = bool(cwd and Path(cwd).is_dir())
        except OSError:
            cwd_exists = False
        if folder_path:
            _git_root = _core._find_git_root(folder_path)
            folder_label = _core._resolve_dir_case(_git_root or folder_path)
        else:
            folder_label = "Antigravity"
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "antigravity",
            "engine": "antigravity",
            "timestamp": "",
            "branch": _core._git_branch_for_cwd(cwd) if cwd else "",
            "git_branch": _core._git_branch_for_cwd(cwd) if cwd else "",
            "first_message": prompt[:200],
            "display_name": display_name,
            "ai_title": ai_title or None,
            "name_overridden": bool(name_overrides.get(sid)),
            "last_prompt": prompt[:200],
            "size": 0,
            "modified": modified,
            "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)),
            "mtime": modified,
            "jsonl_path": "",
            "folder_label": folder_label,
            "folder_path": folder_path,
            "worktree_label": None,
            "session_cwd": cwd,
            "session_cwd_exists": cwd_exists,
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
            "last_assistant_text": None,
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
            "is_live": True,
            "spawn_pid": spawn_info.get("pid"),
            "parent_session_id": spawn_info.get("parent_session_id") or "",
            "can_headless_resume": bool(
                _core._antigravity_cli_conversation_path(sid)
                and _core._antigravity_cli_conversation_path(sid).is_file()
            ),
            "can_app_resume": bool(_core._antigravity_app_conversation_path(sid)),
            "needs_approval": False,
            "needs_approval_message": "",
            "model": spawn_info.get("model") or "",
            "reasoning_effort": "",
            "pending_spawn": True,
        })
    if resolve_pr_states:
        _core._prime_pr_states(c.get("tail_pr_url") for c in out)
        for c in out:
            if c.get("tail_pr_url"):
                c["pr_state"] = _core._get_pr_state(c["tail_pr_url"])
    out.sort(key=lambda x: x.get("last_interacted") or x.get("modified") or 0, reverse=True)
    if progress:
        progress(
            "antigravity",
            state="done",
            count=len(out),
            total=scanned,
            detail=f"{len(out)} Antigravity session card(s) ready.",
        )
    return out


def _parse_antigravity_event(ev, line_num, usage_map=None):
    ev_type = ev.get("type") or ""
    source = ev.get("source") or ""
    ts = _antigravity_event_timestamp(ev)
    if ev_type == "USER_INPUT" and source == "USER_EXPLICIT":
        text = _antigravity_user_text(ev.get("content") or "")
        if text:
            return {"line": line_num, "ts": ts, "type": "user_text", "text": text, "images": []}
        return None
    if ev_type == "PLANNER_RESPONSE":
        blocks = []
        thinking = (ev.get("thinking") or "").strip()
        signature = (ev.get("signature") or "").strip()
        if thinking:
            preview = thinking[:300] + ("..." if len(thinking) > 300 else "")
            blocks.append({"kind": "thinking", "text": preview, "signature_only": False})
        elif signature:
            blocks.append({"kind": "thinking", "text": "", "signature_only": True})
        content = (ev.get("content") or "").strip()
        if _antigravity_embedded_system_message(content):
            content = ""
        if content:
            blocks.append({"kind": "text", "text": content})
        for call in ev.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            detail = _antigravity_tool_detail(call)
            if isinstance(detail, str) and len(detail) > 1200:
                detail = detail[:1200] + "..."
            block = {
                "kind": "tool_use",
                "name": _antigravity_tool_name(call),
                "detail": detail or "",
            }
            command_text = _antigravity_tool_command(call)
            if command_text:
                redacted_command = _core._redacted_shell_command_text(command_text, max_len=12000)
                if redacted_command and (
                    "\n" in redacted_command
                    or len(redacted_command) > 160
                    or re.sub(r"\s+", " ", redacted_command).strip() != (detail or "")
                ):
                    block["command"] = redacted_command
                    here = _core._extract_shell_heredoc(command_text)
                    block["command_kind"] = _core._shell_script_label(here.get("head", "")) if here else "Shell command"
            blocks.append(block)
        if blocks:
            out = {
                "line": line_num,
                "ts": ts,
                "type": "assistant",
                "message_id": f"antigravity-{line_num}",
                "blocks": blocks,
            }
            # Look up per-step token usage by the transcript's step_index, so
            # each assistant turn can render its own "in | out | thinking"
            # chip mirroring Antigravity's own UI.
            if usage_map:
                step_idx = ev.get("step_index")
                try:
                    step_int = int(step_idx) if step_idx is not None else None
                except (TypeError, ValueError):
                    step_int = None
                if step_int is not None and step_int in usage_map:
                    usage = usage_map[step_int]
                    out["tokens_in"] = usage.get("in", 0)
                    out["tokens_out"] = usage.get("out", 0)
                    out["tokens_thinking"] = usage.get("thinking", 0)
                    out["tokens_cached"] = usage.get("cache_read", 0)
                    out["model"] = usage.get("model") or ""
                    out["token_usage"] = {
                        "input_tokens": usage.get("in", 0),
                        "cache_read_input_tokens": usage.get("cache_read", 0),
                        "cache_creation_input_tokens": usage.get("cache_create", 0),
                        "output_tokens": usage.get("out", 0),
                        "reasoning_output_tokens": usage.get("thinking", 0),
                    }
            return out
        return None
    if ev_type in ("CONVERSATION_HISTORY", "SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE", "CHECKPOINT"):
        return None
    content = ev.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    output = content.strip()
    if len(output) > 800:
        output = output[:800] + "\n..."
    return {
        "line": line_num,
        "ts": ts,
        "type": "tool_result",
        "text": output,
        "tool_use_id": str(ev.get("step_index") or ""),
        "is_error": ev.get("status") == "ERROR" or ev_type == "ERROR_MESSAGE",
    }


def _parse_antigravity_conversation(session_id, after_line=0):
    path = _core._antigravity_transcript_path(session_id)
    events = []
    line_num = 0
    if not path:
        return _parse_antigravity_cli_log_conversation(session_id, after_line=after_line)
    # One RPC call per parse — the trajectory carries per-step token usage
    # that the transcript.jsonl itself doesn't. If the Antigravity app isn't
    # running the map is just empty and the parser degrades to its old shape.
    try:
        usage_map = _antigravity_step_usage_map(session_id)
    except Exception:  # pragma: no cover — RPC layer is best-effort
        usage_map = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_num += 1
                if line_num <= after_line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed = _core._parse_antigravity_event(ev, line_num, usage_map=usage_map)
                if parsed:
                    events.append(parsed)
    except OSError:
        pass
    return {"events": events, "last_line": line_num}


def _antigravity_usage_to_int(val):
    if isinstance(val, bool):
        return 0
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.isdigit():
        return int(val)
    return 0


# Field-name fallbacks for the trajectory step's modelUsage entry. Antigravity
# (and downstream models) have shipped these under slightly different keys —
# thinking/reasoning/thought all refer to the same internal scratchpad tokens
# that aren't part of the user-visible output. Reading all of them keeps the
# per-turn chips honest even when the schema drifts.
_ANTIGRAVITY_USAGE_KEYS = {
    "in":           ("inputTokens", "input_tokens"),
    "out":          ("outputTokens", "output_tokens"),
    "thinking":     ("thinkingTokens", "thinking_tokens",
                     "reasoningTokens", "reasoning_tokens",
                     "thoughtTokens", "thought_tokens"),
    "cache_read":   ("cacheReadTokens", "cache_read_tokens"),
    "cache_create": ("cacheCreationTokens", "cache_creation_tokens"),
}


def _antigravity_step_usage(usage):
    """Pull a normalized per-step usage dict out of a modelUsage payload."""
    if not isinstance(usage, dict):
        return None
    out = {"model": usage.get("model") or ""}
    saw_any = False
    for key, candidates in _ANTIGRAVITY_USAGE_KEYS.items():
        value = 0
        for cand in candidates:
            if cand in usage:
                value = _antigravity_usage_to_int(usage.get(cand))
                if value:
                    break
        out[key] = value
        if value:
            saw_any = True
    if not saw_any and not out["model"]:
        return None
    return out


def _proto_bytes_to_str(val):
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8", "ignore")
        except Exception:
            return ""
    if isinstance(val, str):
        return val
    return ""


def _parse_proto(data):
    fields = {}
    pos = 0
    while pos < len(data):
        try:
            # Decode varint for key
            val = 0
            shift = 0
            while True:
                b = data[pos]
                val |= (b & 0x7f) << shift
                pos += 1
                shift += 7
                if not (b & 0x80):
                    break
            key = val
        except IndexError:
            break
        wire_type = key & 0x7
        field_num = key >> 3
        
        if wire_type == 0:  # Varint
            try:
                val = 0
                shift = 0
                while True:
                    b = data[pos]
                    val |= (b & 0x7f) << shift
                    pos += 1
                    shift += 7
                    if not (b & 0x80):
                        break
                fields.setdefault(field_num, []).append(val)
            except IndexError:
                break
        elif wire_type == 1:  # 64-bit
            if pos + 8 > len(data):
                break
            val = data[pos:pos+8]
            pos += 8
            fields.setdefault(field_num, []).append(val)
        elif wire_type == 2:  # Length-delimited
            try:
                val = 0
                shift = 0
                while True:
                    b = data[pos]
                    val |= (b & 0x7f) << shift
                    pos += 1
                    shift += 7
                    if not (b & 0x80):
                        break
                length = val
            except IndexError:
                break
            if pos + length > len(data):
                break
            val = data[pos:pos+length]
            pos += length
            
            sub = None
            if len(val) > 0:
                try:
                    sub = _parse_proto(val)
                except Exception:
                    pass
            if sub and len(sub) > 0 and max(sub.keys()) < 1000:
                fields.setdefault(field_num, []).append(sub)
            else:
                fields.setdefault(field_num, []).append(val)
        elif wire_type == 5:  # 32-bit
            if pos + 4 > len(data):
                break
            val = data[pos:pos+4]
            pos += 4
            fields.setdefault(field_num, []).append(val)
        else:
            break
    return fields


def _antigravity_db_path(session_id):
    if not session_id:
        return None
    sid = str(session_id).strip()
    if not _core._SESSION_UUID_RE.match(sid):
        return None
    for folder in (_core.ANTIGRAVITY_CLI_CONVERSATIONS, _core.ANTIGRAVITY_CONVERSATIONS):
        candidate = folder / f"{sid}.db"
        if candidate.is_file():
            return candidate
    return None


def _antigravity_db_trajectory_steps(session_id):
    db_path = _antigravity_db_path(session_id)
    if not db_path:
        return []
    
    steps = []
    conn = None
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        cursor = conn.cursor()
        
        # 1. Load gen_metadata mapping
        cursor.execute("SELECT idx, data FROM gen_metadata;")
        gen_map = {}
        for gen_idx, gen_data in cursor.fetchall():
            if gen_data:
                try:
                    gen_map[gen_idx] = _parse_proto(gen_data)
                except Exception:
                    pass
                    
        # 2. Walk through steps table
        cursor.execute("SELECT idx, metadata FROM steps;")
        for idx, metadata_blob in cursor.fetchall():
            if not metadata_blob:
                continue
            try:
                parsed_step_meta = _parse_proto(metadata_blob)
            except Exception:
                continue
                
            model_usage = None
            
            # Look up field 20 -> field 3 (gen_metadata index)
            field_20 = parsed_step_meta.get(20)
            if field_20 and isinstance(field_20, list) and isinstance(field_20[0], dict):
                sub_20 = field_20[0]
                gen_idx_list = sub_20.get(3)
                if gen_idx_list:
                    gen_idx = gen_idx_list[0]
                    if gen_idx in gen_map:
                        gen_data = gen_map[gen_idx]
                        
                        # Extract model name
                        model_name = ""
                        for fnum in (21, 19):
                            m_list = gen_data.get(fnum)
                            if m_list:
                                model_name = _proto_bytes_to_str(m_list[0])
                                if model_name:
                                    break
                                    
                        # Extract token counts
                        tokens_in = 0
                        tokens_out = 0
                        tokens_thinking = 0
                        cache_read = 0
                        cache_create = 0
                        
                        field_1 = gen_data.get(1)
                        if field_1 and isinstance(field_1, list) and isinstance(field_1[0], dict):
                            sub_1 = field_1[0]
                            field_4 = sub_1.get(4)
                            if field_4 and isinstance(field_4, list) and isinstance(field_4[0], dict):
                                usage = field_4[0]
                                tokens_in = usage.get(2, [0])[0]
                                tokens_out = usage.get(3, [0])[0]
                                tokens_thinking = usage.get(10, [0])[0]
                                cache_read = usage.get(5, [0])[0]
                                cache_create = usage.get(9, [0])[0]
                                
                        model_usage = {
                            "model": model_name,
                            "inputTokens": tokens_in,
                            "outputTokens": tokens_out,
                            "thinkingTokens": tokens_thinking,
                            "cacheReadTokens": cache_read,
                            "cacheCreationTokens": cache_create,
                        }
            
            step_entry = {
                "stepIndex": idx,
                "metadata": {}
            }
            if model_usage:
                step_entry["metadata"]["modelUsage"] = model_usage
            steps.append(step_entry)
            
    except Exception:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
                
    return steps


def _antigravity_trajectory_steps(session_id):
    """Fetch GetCascadeTrajectory and return its raw `steps` list (or [])."""
    result = _core._antigravity_app_rpc(
        "GetCascadeTrajectory",
        {"cascadeId": session_id},
        timeout=5,
    )
    if not result.get("ok"):
        return _antigravity_db_trajectory_steps(session_id)
    trajectory = (result.get("response") or {}).get("trajectory") or {}
    steps = trajectory.get("steps") or []
    return steps if isinstance(steps, list) else []


def _antigravity_step_usage_map(session_id, steps=None):
    """Build a {transcript step_index -> per-step usage dict} map.

    Antigravity's trajectory steps each carry a `stepIndex` (or `step_index`)
    that matches the `step_index` field on transcript.jsonl events. We use it
    to look up per-turn token counts at render time. When the trajectory
    doesn't expose an explicit index, we fall back to positional order so the
    map is still useful as a per-PLANNER_RESPONSE walking index.
    """
    if steps is None:
        steps = _antigravity_trajectory_steps(session_id)
    usage_map = {}
    for pos, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        metadata = step.get("metadata")
        if not isinstance(metadata, dict):
            continue
        usage = _antigravity_step_usage(metadata.get("modelUsage"))
        if not usage:
            continue
        idx = step.get("stepIndex")
        if idx is None:
            idx = step.get("step_index")
        if idx is None:
            idx = pos
        try:
            idx_int = int(idx)
        except (TypeError, ValueError):
            continue
        usage_map[idx_int] = usage
    return usage_map


def _extract_antigravity_usage(session_id):
    empty = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_thinking_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "context_limit": 1_000_000,
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
    }
    path = _core._antigravity_transcript_path(session_id)
    tail = _core._extract_antigravity_tail_meta(path) if path else {}
    model = (tail or {}).get("model") or ""

    latest = 0
    peak = 0
    total_in = 0
    total_cached_read = 0
    total_cached_create = 0
    total_out = 0
    total_thinking = 0
    # Per-turn tail for the status-rail column graph — one entry per
    # trajectory step that carried usage, same raw-count shape Claude's
    # turn_series uses. Steps don't carry a timestamp, so ts stays "".
    turn_series = collections.deque(maxlen=_core.USAGE_TURN_SERIES_MAX)

    steps = _antigravity_trajectory_steps(session_id)
    for step in steps:
        if not isinstance(step, dict):
            continue
        metadata = step.get("metadata")
        if not isinstance(metadata, dict):
            continue
        usage = _antigravity_step_usage(metadata.get("modelUsage"))
        if not usage:
            continue

        if usage.get("model"):
            model = usage["model"]

        in_tokens = usage["in"]
        out_tokens = usage["out"]
        cr_tokens = usage["cache_read"]
        cc_tokens = usage["cache_create"]
        thinking_tokens = usage["thinking"]

        window = in_tokens + cr_tokens + cc_tokens
        if window:
            latest = window
            if window > peak:
                peak = window

        total_in += in_tokens
        total_cached_read += cr_tokens
        total_cached_create += cc_tokens
        total_out += out_tokens
        total_thinking += thinking_tokens

        turn_out = out_tokens + thinking_tokens
        if window or turn_out:
            turn_series.append({
                "ts": "",
                "tokens_in": window,
                "tokens_cached": cr_tokens,
                "tokens_out": turn_out,
            })

    return {
        **empty,
        "latest_input_tokens": latest,
        "peak_input_tokens": peak,
        "total_output_tokens": total_out,
        "total_input_tokens": total_in,
        "total_thinking_tokens": total_thinking,
        "total_cache_creation_tokens": total_cached_create,
        "total_cache_read_tokens": total_cached_read,
        "model": model,
        "engine": "antigravity",
        "override": _core._get_session_override(session_id),
        "turn_series": list(turn_series),
    }


_antigravity_list_usage_cache = {}
_antigravity_list_usage_cache_lock = threading.Lock()


def _antigravity_list_context_usage(session_id, *, is_live=False):
    """Context % for archive/list rows. Trajectory RPC is live-only + cached."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0, 1_000_000
    now = time.time()
    with _antigravity_list_usage_cache_lock:
        cached = _antigravity_list_usage_cache.get(sid)
        if cached and now - cached.get("ts", 0) < 30:
            return cached.get("latest_input_tokens", 0), cached.get("context_limit", 1_000_000)
    if not is_live:
        return 0, 1_000_000
    try:
        usage = _extract_antigravity_usage(sid)
        latest = int(usage.get("latest_input_tokens") or 0)
        limit = int(usage.get("context_limit") or 1_000_000) or 1_000_000
    except Exception:
        latest, limit = 0, 1_000_000
    with _antigravity_list_usage_cache_lock:
        _antigravity_list_usage_cache[sid] = {
            "ts": now,
            "latest_input_tokens": latest,
            "context_limit": limit,
        }
    return latest, limit


def _extract_antigravity_timeline(session_id):
    path = _core._antigravity_transcript_path(session_id)
    if not path:
        return {"events": [], "total_turns": 0}
    events = []
    turn = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _antigravity_event_timestamp(ev)
                if ev.get("type") == "PLANNER_RESPONSE" and ev.get("content"):
                    turn += 1
                if ev.get("type") != "PLANNER_RESPONSE":
                    continue
                for call in ev.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    cmd = _antigravity_tool_command(call)
                    if not cmd:
                        continue
                    kind = None
                    subject = ""
                    if _core._TIMELINE_PR_CREATE_RE.search(cmd):
                        kind = "pr"
                        m = _core._TIMELINE_PR_TITLE_RE.search(cmd)
                        if m:
                            subject = m.group(1)
                    elif _core._TIMELINE_PUSH_RE.search(cmd):
                        kind = "push"
                    elif _core._TIMELINE_COMMIT_RE.search(cmd):
                        kind = "commit"
                        m = _core._TIMELINE_COMMIT_MSG_RE.search(cmd)
                        if m:
                            subject = m.group(1)
                    if kind:
                        events.append({
                            "kind": kind,
                            "turn": max(turn, 1),
                            "ts": ts,
                            "subject": subject,
                            "success": ev.get("status") != "ERROR",
                        })
    except OSError:
        pass
    return {"events": events, "total_turns": turn}


def _antigravity_consider_text_targets(text, consider, line_num):
    if not isinstance(text, str) or not text:
        return
    for match in _ANTIGRAVITY_FILE_URL_RE.finditer(text):
        target = _antigravity_normalize_path(match.group(0))
        if target:
            consider(target, "path", line_num)
    for target, kind in _core._ffc_iter_targets(text):
        consider(target, kind, line_num)


def _extract_files_from_antigravity_conversation(session_id):
    path = _core._antigravity_transcript_path(session_id)
    if not path:
        return {"count": 0, "truncated": False, "groups": {}}
    seen = {}
    truncated = False

    def consider(target, kind, line):
        nonlocal truncated
        truncated = _core._ffc_consider_file_target(seen, target, kind, line, truncated)

    line_num = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line_num += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                _antigravity_consider_text_targets(ev.get("content"), consider, line_num)
                for call in ev.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    for raw in _core._ffc_flatten_strings(_antigravity_tool_args(call)):
                        unquoted = _antigravity_unquote(raw)
                        if isinstance(unquoted, str):
                            normalized = _antigravity_normalize_path(unquoted)
                            if normalized:
                                consider(normalized, "path", line_num)
                            _antigravity_consider_text_targets(unquoted, consider, line_num)
    except OSError:
        pass

    groups = {}
    for row in seen.values():
        groups.setdefault(row["category"], []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda r: r["first_line"])
    return {"count": len(seen), "truncated": truncated, "groups": groups}


def resume_session_gemini(session_id, text):
    """Resume a Gemini session with a one-shot headless prompt."""
    text = _core._strip_ccc_session_state_instruction(text)
    if not text:
        return {"ok": False, "error": "missing text"}
    resolved = _core._resolve_gemini_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    for s in _core._spawned_sessions:
        if s.get("engine") == "gemini" and s.get("resumed_sid") == session_id:
            try:
                if _core._poll_spawn_entry(s) is None:
                    with _core._pending_resume_lock:
                        _core._pending_resume_queue.setdefault(session_id, []).append(text)
                    _core._save_pending_inputs()
                    return {

                        "ok": True,
                        "queued": True,
                        "pid": s.get("pid"),
                        "via": "gemini-resume-queued",
                    }
            except Exception:
                pass
    spawned_ctx = _core._spawn_registry_entry_for_session(session_id, "gemini") or {}
    cwd = spawned_ctx.get("cwd") or _core.find_session_cwd(session_id) or str(Path.cwd())
    if not Path(cwd).is_dir():
        cwd = str(Path.cwd())
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-gemini-{session_id[:8]}-{timestamp}.log"
    repo_for_logs = _core._git_toplevel_for_existing_dir(cwd) or cwd
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    cmd = [resolved["bin"], "--approval-mode", os.environ.get("CCC_GEMINI_APPROVAL_MODE", "yolo"),
           "--output-format", "stream-json", "--resume", session_id]
    # Per-session override (set via the click-to-switch picker) wins over
    # the env-var default. Without an override, fall back to env-var or
    # let the CLI pick its own default.
    override = _core._get_session_override(session_id)
    model = (override or {}).get("model") if override else None
    if not model:
        model = os.environ.get("CCC_GEMINI_MODEL")
    if model:
        cmd.extend(["--model", model])
    cmd.extend(["-p", text])
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
        return {"ok": False, "error": str(e), "via": "gemini-resume"}
    entry = {
        "pid": proc.pid,
        "name": f"resume-gemini-{session_id[:8]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
        "fifo": None,
        "stdin_fd": None,
        "engine": "gemini",
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
        engine="gemini",
        session_id=session_id,
        repo_path=repo_for_logs,
        model=model,
    )
    return {"ok": True, "pid": proc.pid, "log": str(log_path), "resumed": True, "via": "gemini-resume"}


def spawn_session_gemini(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None):
    """Spawn a headless Gemini CLI run and return tracking info."""
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    resolved = _core._resolve_gemini_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-gemini-{session_name}-{timestamp}.log"
    if model:
        _core._set_session_model(log_filename[:-4], model, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    cmd = [
        resolved["bin"],
        "--approval-mode", os.environ.get("CCC_GEMINI_APPROVAL_MODE", "yolo"),
        "--output-format", "stream-json",
    ]
    model_to_use = model or os.environ.get("CCC_GEMINI_MODEL")
    if model_to_use:
        cmd.extend(["--model", model_to_use])
    if worktree:
        cmd.extend(["--worktree", session_name])
    cmd.extend(["-p", prompt])
    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=spawn_cwd,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "via": "gemini-spawn"}
    failure = _spawn_early_failure_payload(
        proc, log_path, log_fh, engine="gemini", via="gemini-spawn",
    )
    if failure:
        return failure
    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "fifo": None,
        "stdin_fd": None,
        "engine": "gemini",
        "cwd": spawn_cwd,
        "repo_path": repo_for_logs,
        "model": model_to_use or "",
        "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=None,
        engine="gemini",
        repo_path=repo_for_logs,
        model=model_to_use,
        parent_session_id=parent_session_id,
    )
    return _finalize_spawn_response(
        {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path)},
        entry,
        ctx,
    )


def spawn_session_devin(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None, reasoning_effort=None):
    """Spawn a headless Devin CLI run and return tracking info.

    Uses `devin -p "prompt" --permission-mode dangerous` (one-shot, like
    gemini). The session ID is assigned by the Devin CLI and discovered
    later from its SQLite DB via find_devin_cli_conversations().

    Devin encodes reasoning effort in the model uid (claude-opus-5-max,
    gpt-5-6-sol-low, etc.). ``_devin_resolve_model`` maps the user's
    selected base model + reasoning_effort to the concrete uid.
    """
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    resolved = _core._resolve_devin_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-devin-{session_name}-{timestamp}.log"
    model_to_use = _core._devin_resolve_model(
        (model or os.environ.get("CCC_DEVIN_MODEL")),
        reasoning_effort,
    )
    if model_to_use:
        _core._set_session_model(log_filename[:-4], model_to_use, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    cmd = [
        resolved["bin"],
        "--permission-mode", os.environ.get("CCC_DEVIN_PERMISSION_MODE", "dangerous"),
        "--respect-workspace-trust", "false",
    ]
    if model_to_use:
        cmd.extend(["--model", model_to_use])
    cmd.extend(["-p", prompt])
    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=spawn_cwd,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "code": "devin_launch_failed", "via": "devin-spawn"}
    failure = _spawn_early_failure_payload(
        proc, log_path, log_fh, engine="devin", via="devin-spawn",
    )
    if failure:
        return failure
    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "fifo": None,
        "stdin_fd": None,
        "engine": "devin",
        "cwd": spawn_cwd,
        "repo_path": repo_for_logs,
        "model": model_to_use or "",
        "reasoning_effort": reasoning_effort or "",
        "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=None,
        engine="devin",
        repo_path=repo_for_logs,
        model=model_to_use,
        parent_session_id=parent_session_id,
        reasoning_effort=reasoning_effort or "",
    )
    return _finalize_spawn_response(
        {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path)},
        entry,
        ctx,
    )


# A one-shot `devin --resume -p` can fail at startup several seconds AFTER
# spawn — e.g. the CLI's own sessions DB is held by another devin process
# and its internal busy timeout expires ("Error: session/list failed:
# database is locked", observed ~7s in). That is past any synchronous
# early-failure poll, so the send path has already reported success and the
# follow-up would be silently dropped (OPS-807). The watchdog below waits
# out a short startup window; a fast non-zero exit with a leading CLI
# `Error:` line means the turn never started, so the text is requeued for
# the resume-queue drain to retry instead of being lost. A mid-turn crash
# (transcript output before any error) never requeues — the prompt may have
# been consumed and resending it would fork the session.
_DEVIN_RESUME_WATCHDOG_WINDOW_S = 30.0


def _start_devin_resume_watchdog(proc, session_id, text, log_path):
    def _watch():
        try:
            exit_code = proc.wait(timeout=_core._DEVIN_RESUME_WATCHDOG_WINDOW_S)
        except Exception:
            return  # still running past the window: a real turn started
        if not isinstance(exit_code, int) or exit_code == 0:
            return
        try:
            tail = _core._antigravity_read_log_tail(log_path, max_bytes=4000)
        except Exception:
            tail = ""
        first_line = next(
            (ln.strip() for ln in str(tail).splitlines() if ln.strip()), ""
        )
        if not first_line.lower().startswith("error:"):
            return
        print(
            f"[devin-resume] startup failure for {session_id} "
            f"(exit {exit_code}): {first_line[:160]} — requeuing follow-up",
            flush=True,
        )
        with _core._pending_resume_lock:
            _core._pending_resume_queue.setdefault(session_id, []).insert(0, text)
        _core._save_pending_inputs()
        _core._mark_pending_resume_retry(session_id)

    threading.Thread(
        target=_watch,
        daemon=True,
        name=f"devin-resume-watchdog-{str(session_id)[-12:]}",
    ).start()


# Proof-of-delivery window. A `devin --resume -p` must write the prompt into
# its own sessions DB (`prompt_history` table) within this many seconds of
# the dashboard launching it; otherwise the follow-up is requeued for retry.
_DEVIN_DELIVERY_PROOF_WINDOW_S = 30.0
# How long to sleep between DB polls while waiting for proof.
_DEVIN_DELIVERY_PROOF_POLL_INTERVAL_S = 0.5


def _devin_cli_prompt_history_count(raw_id, text, since_ts):
    """Count prompt_history rows for ``raw_id`` with ``content == text`` and
    timestamp >= ``since_ts``.

    Returns 0 when the DB is unreadable or the row is missing. The count is
    used instead of a boolean because tests can inject multiple matching rows.
    """
    con = _core._devin_cli_connect()
    if con is None:
        return 0
    try:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM prompt_history "
            "WHERE session_id = ? AND content = ? AND timestamp >= ?",
            (raw_id, text, int(since_ts)),
        ).fetchone()
        return int((row or {}).get("n", 0)) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def _devin_raw_id(session_id):
    """Strip the devincli- prefix, or return the id unchanged."""
    if session_id and session_id.startswith("devincli-"):
        return session_id[len("devincli-"):]
    return session_id or ""


def _start_devin_delivery_proof_watchdog(proc, session_id, text, started_at):
    """Wait for a new prompt_history row proving Devin accepted the follow-up.

    If the row appears, remove the message from the durable queue. If the
    process exits before the row appears, requeue it for retry with backoff.
    A mid-turn crash after the prompt was written (row present) is treated as
    delivered; resending would fork the session.
    """
    raw_id = _devin_raw_id(session_id)
    if not raw_id:
        return
    # The CLI receives the instruction-stripped text, so prompt_history will
    # contain the stripped form. Match against that, not the raw queued text.
    text = _core._strip_ccc_session_state_instruction(text)

    def _watch():
        deadline = time.time() + _DEVIN_DELIVERY_PROOF_WINDOW_S
        while time.time() < deadline:
            if _core._devin_cli_prompt_history_count(raw_id, text, started_at) > 0:
                removed = False
                with _core._pending_resume_lock:
                    queue = _core._pending_resume_queue.get(session_id) or []
                    if queue and queue[0] == text:
                        queue.pop(0)
                        if not queue:
                            _core._pending_resume_queue.pop(session_id, None)
                        removed = True
                if removed:
                    _core._save_pending_inputs()
                    _core._pending_resume_retry_after.pop(session_id, None)
                return
            try:
                exit_code = proc.poll()
            except Exception:
                exit_code = None
            if exit_code is not None and exit_code != 0:
                break
            time.sleep(_core._DEVIN_DELIVERY_PROOF_POLL_INTERVAL_S)
        # No proof. Requeue at front for retry (unless it was already removed).
        with _core._pending_resume_lock:
            queue = _core._pending_resume_queue.get(session_id) or []
            if queue and queue[0] == text:
                pass  # already at front
            elif text:
                _core._pending_resume_queue.setdefault(session_id, []).insert(0, text)
        _core._save_pending_inputs()
        _core._mark_pending_resume_retry(session_id)
        print(
            f"[devin-proof] no delivery proof for {session_id} — requeuing follow-up",
            flush=True,
        )

    threading.Thread(
        target=_watch,
        daemon=True,
        name=f"devin-proof-watchdog-{str(session_id)[-12:]}",
    ).start()


def resume_session_devin(session_id, text):
    """Resume a Devin CLI session with a one-shot headless prompt.

    Uses `devin --resume <id> -p "text" --permission-mode dangerous`.
    The session_id is expected to be devincli-prefixed; the raw ID is
    extracted for the CLI.
    """
    text = _core._strip_ccc_session_state_instruction(text)
    if not session_id or not text:
        return {"ok": False, "error": "missing session_id or text"}
    resolved = _core._resolve_devin_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    # Strip the devincli- prefix to get the raw Devin CLI session ID.
    raw_id = session_id
    if session_id.startswith("devincli-"):
        raw_id = session_id[len("devincli-"):]
    # Queue if already running.
    for s in _core._spawned_sessions:
        if s.get("engine") == "devin" and s.get("resumed_sid") == session_id:
            try:
                if _core._poll_spawn_entry(s) is None:
                    # A one-shot `devin --resume -p` owns the session lock
                    # until its turn ends (a turn with context compaction
                    # plus tool calls can run for minutes). Say so: the
                    # UI otherwise shows a bare "sending" for that whole time.
                    running_s = None
                    started_epoch = _core._spawn_registry_entry_epoch(s)
                    if started_epoch > 0:
                        running_s = max(0, int(time.time() - started_epoch))
                    reason = (
                        "Devin is still working on the previous turn"
                        + (f" ({running_s}s so far)" if running_s is not None else "")
                        + ". It sends automatically when that turn ends."
                    )
                    with _core._pending_resume_lock:
                        _core._pending_resume_queue.setdefault(session_id, []).append(text)
                    _core._note_pending_queued(session_id, text, reason)
                    _core._save_pending_inputs()
                    return {
                        "ok": True,
                        "queued": True,
                        "pid": s.get("pid"),
                        "via": "devin-resume-queued",
                        "queued_reason": reason,
                        "running_for_s": running_s,
                    }
            except Exception:
                pass
    spawned_ctx = _core._spawn_registry_entry_for_session(session_id, "devin") or {}
    cwd = spawned_ctx.get("cwd") or _devin_cli_session_cwd(raw_id) or str(Path.cwd())
    if not Path(cwd).is_dir():
        cwd = str(Path.cwd())
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-devin-{raw_id[:12]}-{timestamp}.log"
    repo_for_logs = _core._git_toplevel_for_existing_dir(cwd) or cwd
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    cmd = [
        resolved["bin"],
        "--permission-mode", os.environ.get("CCC_DEVIN_PERMISSION_MODE", "dangerous"),
        "--respect-workspace-trust", "false",
        "--resume", raw_id,
    ]
    override = _core._get_session_override(session_id)
    model = (override or {}).get("model") if override else None
    if not model:
        model = os.environ.get("CCC_DEVIN_MODEL")
    if model:
        cmd.extend(["--model", model])
    cmd.extend(["-p", text])
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
        return {"ok": False, "error": str(e), "via": "devin-resume"}
    failure = _spawn_early_failure_payload(
        proc, log_path, log_fh, engine="devin", via="devin-resume",
    )
    if failure:
        return failure
    entry = {
        "pid": proc.pid,
        "name": f"resume-devin-{raw_id[:12]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
        "fifo": None,
        "stdin_fd": None,
        "engine": "devin",
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
        engine="devin",
        session_id=session_id,
        repo_path=repo_for_logs,
        model=model,
    )
    _core._start_devin_resume_watchdog(proc, session_id, text, log_path)
    _core._start_devin_delivery_proof_watchdog(proc, session_id, text, time.time())
    return {"ok": True, "pid": proc.pid, "log": str(log_path), "resumed": True, "via": "devin-resume"}


def _devin_cli_session_cwd(raw_id):
    """Look up the working_directory for a Devin CLI session from the DB."""
    try:
        con = _core._devin_cli_connect()
        if con is None:
            return None
        try:
            row = con.execute(
                "SELECT working_directory FROM sessions WHERE id = ?", (raw_id,)
            ).fetchone()
            if row:
                return str(row["working_directory"] or "").strip() or None
        except sqlite3.Error:
            pass
        finally:
            con.close()
    except Exception:
        pass
    return None


def _devin_cli_claimed_session_ids(entry):
    """Devin CLI session ids already resolved for spawn entries other than
    ``entry`` (in-memory list plus the on-disk registry, so a restarted
    dashboard still honours claims made before it came up).

    A session id handed to one spawn must never be handed to a second one;
    the prompt-match fallback cannot tell two identical spawns apart on its
    own.
    """
    claimed = set()
    entry_pid = entry.get("pid") if isinstance(entry, dict) else None
    sources = list(_core._spawned_sessions)
    try:
        sources.extend(_core._load_spawn_registry())
    except Exception:
        pass
    for s in sources:
        if s is entry or not isinstance(s, dict):
            continue
        if str(s.get("engine") or "").lower() != "devin":
            continue
        if entry_pid is not None and s.get("pid") == entry_pid:
            continue
        for key in ("session_id", "resumed_sid"):
            sid = str(s.get(key) or "").strip()
            if sid:
                claimed.add(sid)
    return claimed


def _devin_cli_session_id_for_spawn_entry(entry):
    """Best-effort Devin CLI session id for a live CCC spawn entry.

    The Devin CLI does not emit its session id on stdout, so CCC resolves it
    from (in order):
      1. The CLI lock file whose recorded pid matches the spawn pid.
      2. The CLI SQLite DB, matching working directory + first prompt +
         start time. First prompt comes from prompt_history, then
         message_nodes (one-shot ``devin -p`` often skips prompt_history).

    The DB match only considers sessions created at or after the spawn
    stamp (a spawn cannot have produced a row that predates it) and takes
    the one created soonest after it. Two spawns with an identical prompt
    in the same cwd used to both resolve to the older session because the
    scan ran newest-first inside a symmetric 900s window. Sessions already
    resolved for another spawn entry are skipped for the same reason.
    """
    if not isinstance(entry, dict):
        return None
    engine = str(entry.get("engine") or "").lower()
    if engine != "devin":
        return None
    pid = entry.get("pid")
    if pid:
        raw_from_lock = _core._devin_cli_raw_id_for_pid(pid)
        if raw_from_lock:
            return _core.DEVIN_CLI_SESSION_PREFIX + raw_from_lock
    cwd = str(entry.get("cwd") or entry.get("repo_path") or "").strip()
    prompt = str(entry.get("command_summary") or entry.get("prompt") or "").strip()
    if not cwd or not prompt:
        return None
    spawned_at = str(entry.get("spawned_at") or entry.get("started") or "").strip()
    spawn_ts = 0.0
    if spawned_at:
        try:
            spawn_ts = datetime.strptime(spawned_at, "%Y%m%dT%H%M%S").timestamp()
        except (ValueError, OSError):
            pass
    try:
        cwd_norm = os.path.realpath(cwd)
    except OSError:
        cwd_norm = os.path.normpath(cwd)
    claimed = _devin_cli_claimed_session_ids(entry)
    con = _core._devin_cli_connect()
    if con is None:
        return None
    try:
        # Filter time in Python via _devin_epoch so millisecond created_at
        # values still match a local spawned_at stamp. SQL comparison against
        # unix-seconds lo/hi misses those rows entirely.
        candidates = list(con.execute(
            "SELECT id, working_directory, created_at FROM sessions"
        ))
        # With a known spawn time, walk oldest-first so the first match is
        # the session created soonest after the spawn. Without one, fall
        # back to newest-first (the only ordering that makes sense blind).
        candidates.sort(
            key=lambda r: _core._devin_epoch(r["created_at"]),
            reverse=not spawn_ts,
        )
        for row in candidates:
            raw_id = str(row["id"] or "").strip()
            if not raw_id:
                continue
            if _core.DEVIN_CLI_SESSION_PREFIX + raw_id in claimed:
                continue
            created = _core._devin_epoch(row["created_at"])
            if spawn_ts:
                # The spawn stamp is floored to the second and taken before
                # Popen, so a genuine child row is never older than it.
                if not created or created < spawn_ts:
                    continue
                if created - spawn_ts > 900:
                    break
            row_cwd = str(row["working_directory"] or "").strip()
            try:
                row_cwd_norm = os.path.realpath(row_cwd) if row_cwd else ""
            except OSError:
                row_cwd_norm = os.path.normpath(row_cwd)
            if row_cwd_norm != cwd_norm and os.path.normpath(row_cwd) != os.path.normpath(cwd):
                continue
            first_prompt = _devin_cli_first_prompt_for_session(con, raw_id)
            if not first_prompt:
                continue
            if not _core._devin_cli_first_prompts_match(prompt, first_prompt):
                continue
            return _core.DEVIN_CLI_SESSION_PREFIX + raw_id
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return None


def _devin_cli_first_prompt_for_session(con, raw_id):
    """First user prompt for a Devin CLI session, history then message_nodes.

    One-shot ``devin -p`` often writes the user turn to message_nodes and
    never inserts prompt_history. Listing already falls back; spawn-id
    matching must too or session_id stays null and the row never opens.
    """
    if not raw_id:
        return ""
    try:
        ph = con.execute(
            "SELECT content FROM prompt_history "
            "WHERE session_id = ? AND is_shell = 0 "
            "ORDER BY timestamp ASC LIMIT 1",
            (raw_id,),
        ).fetchone()
    except sqlite3.Error:
        ph = None
    if ph:
        text = str(ph["content"] or "").strip()
        if text:
            return text
    nodes = _core._devin_cli_first_messages_from_nodes(con, [raw_id])
    return str(nodes.get(raw_id) or "").strip()


def _devin_cli_first_prompts_match(summary, db_prompt):
    """Compare the spawn command summary to the DB's first prompt.

    Either string may be truncated, have trailing whitespace, or differ in
    newlines vs spaces (the spawn registry flattens the prompt). A shared
    leading prefix of collapsed whitespace is enough to confirm the match.
    """
    a = " ".join((summary or "").split())
    b = " ".join((db_prompt or "").split())
    if a == b:
        return True
    n = min(len(a), len(b), 100)
    if n < 10:
        return a == b
    return a[:n] == b[:n]


def resume_session_cursor(session_id, text):
    """Resume a Cursor Agent chat with a one-shot headless prompt."""
    text = _core._strip_ccc_session_state_instruction(text)
    if not session_id or not text:
        return {"ok": False, "error": "missing session_id or text"}
    resolved = _core._resolve_cursor_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    for s in _core._spawned_sessions:
        if s.get("engine") == "cursor" and s.get("resumed_sid") == session_id:
            try:
                if _core._poll_spawn_entry(s) is None:
                    with _core._pending_resume_lock:
                        _core._pending_resume_queue.setdefault(session_id, []).append(text)
                    _core._save_pending_inputs()
                    return {
                        "ok": True,
                        "queued": True,
                        "pid": s.get("pid"),
                        "via": "cursor-resume-queued",
                    }
            except Exception:
                pass
    spawned_ctx = _core._spawn_registry_entry_for_session(session_id, "cursor") or {}
    path = _core._cursor_transcript_path(session_id)
    tail = _core._extract_cursor_tail_meta(path) if path else {}
    cwd = spawned_ctx.get("cwd") or (tail or {}).get("cwd") or _core.find_session_cwd(session_id) or str(Path.cwd())
    if not Path(cwd).is_dir():
        cwd = str(Path.cwd())
    override = _core._get_session_override(session_id)
    override_model = ((override or {}).get("model") if override else None)
    model = (
        override_model
        or os.environ.get("CCC_CURSOR_MODEL")
        or (tail or {}).get("model")
        or _core._spawn_model_for_engine("cursor")
        or spawned_ctx.get("model")
        or _core._spawn_fallback_model_for_engine("cursor")
    )
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-cursor-{session_id[:8]}-{timestamp}.log"
    repo_for_logs = _core._git_toplevel_for_existing_dir(cwd) or cwd
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    cmd = [
        resolved["bin"],
        "--print",
        "--output-format", "stream-json",
        "--stream-partial-output",
        "--resume", session_id,
        "--workspace", cwd,
        "--model", model,
        text,
    ]
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
        return {"ok": False, "error": str(e), "via": "cursor-resume"}
    failure = _cursor_early_failure_payload(
        proc, log_path, log_fh, via="cursor-resume",
    )
    if failure:
        return failure
    entry = {
        "pid": proc.pid,
        "name": f"resume-cursor-{session_id[:8]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
        "fifo": None,
        "stdin_fd": None,
        "engine": "cursor",
        "cwd": cwd,
        "repo_path": repo_for_logs,
        "model": model,
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
        engine="cursor",
        session_id=session_id,
        repo_path=repo_for_logs,
        model=model,
    )
    return {
        "ok": True,
        "pid": proc.pid,
        "log": str(log_path),
        "resumed": True,
        "via": "cursor-resume",
        "model": model,
    }


def spawn_session_cursor(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None):
    """Spawn a headless Cursor Agent run and return tracking info."""
    prompt = _core._strip_ccc_session_state_instruction(prompt or "")
    if not prompt:
        return {"ok": False, "error": "missing prompt"}
    resolved = _core._resolve_cursor_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "cursor"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-cursor-{session_name}-{timestamp}.log"
    model_to_use = _core._spawn_model_for_engine("cursor", model) or _core._spawn_fallback_model_for_engine("cursor")
    if model_to_use:
        _core._set_session_model(log_filename[:-4], model_to_use, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(
                spawn_cwd, session_name,
            )
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}

    cmd = [
        resolved["bin"],
        "--print",
        "--output-format", "stream-json",
        "--stream-partial-output",
        "--force",
        "--trust",
        "--workspace", spawn_cwd,
        "--model", model_to_use,
        prompt,
    ]

    log_fh = open(log_path, "w")
    if worktree_path:
        _core._run_worktree_init_hook(worktree_path, ctx["repo_path"], session_name, log_fh)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=spawn_cwd,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "code": "cursor_launch_failed", "via": "cursor-spawn"}
    failure = _cursor_early_failure_payload(
        proc, log_path, log_fh, via="cursor-spawn",
    )
    if failure is None:
        failure = _spawn_early_failure_payload(
            proc, log_path, log_fh, engine="cursor", via="cursor-spawn",
            delay=0,
        )
    if failure:
        return failure

    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "fifo": None,
        "stdin_fd": None,
        "engine": "cursor",
        "cwd": spawn_cwd,
        "repo_path": repo_for_logs,
        "model": model_to_use,
        "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=None,
        engine="cursor",
        repo_path=repo_for_logs,
        model=model_to_use,
        parent_session_id=parent_session_id,
    )

    resp = {
        "ok": True,
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "via": "cursor-spawn",
        "model": model_to_use,
    }
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    return _finalize_spawn_response(resp, entry, ctx)


def spawn_session_antigravity(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None):
    """Spawn a headless AGY print-mode run and return tracking info.

    If `worktree=True`, create a fresh git worktree off the launch cwd on a
    `feat/<slug>` branch (same shape as the Claude path) and run AGY there.
    """
    prompt = _core._strip_ccc_session_state_instruction(prompt or "")
    if not prompt:
        return {"ok": False, "error": "missing prompt"}
    resolved = _core._resolve_antigravity_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "antigravity"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-antigravity-{session_name}-{timestamp}.log"
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    model_to_use = _core._antigravity_model_settings_label(
        _core._spawn_model_for_engine("antigravity", model) or ""
    )
    if model_to_use:
        settings_result = _core._set_antigravity_cli_model(model_to_use)
        if not settings_result.get("ok"):
            return {
                **settings_result,
                "via": "antigravity-spawn",
            }
        model_to_use = settings_result.get("model") or model_to_use

    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(
                spawn_cwd, session_name,
            )
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}

    cmd = _core._antigravity_command_words(resolved)
    user_args = cmd[1:]
    
    if (
        os.environ.get("CCC_ANTIGRAVITY_SKIP_PERMISSIONS", "1").strip().lower()
        not in ("0", "false", "no", "off")
        and not _core._antigravity_has_arg(user_args, "--dangerously-skip-permissions")
    ):
        cmd.append("--dangerously-skip-permissions")
    add_dirs = []
    if not _core._antigravity_has_arg(user_args, "--add-dir"):
        add_dirs.append(spawn_cwd)
    add_dirs.extend(_core._pasted_image_parent_dirs(prompt))
    _core._antigravity_add_dirs(cmd, user_args, add_dirs)
    cli_log_path = Path(str(log_path) + ".agy.log")
    if not _core._antigravity_has_arg(user_args, "--log-file"):
        cmd.extend(["--log-file", str(cli_log_path)])
    if not _core._antigravity_has_arg(user_args, "--print-timeout"):
        cmd.extend(["--print-timeout", _core._antigravity_print_timeout()])

    cmd.extend(["-p", prompt])

    log_fh = open(log_path, "w")
    if worktree_path:
        _core._run_worktree_init_hook(worktree_path, ctx["repo_path"], session_name, log_fh)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=spawn_cwd,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "via": "antigravity-spawn"}
    time.sleep(0.15)
    early_exit = proc.poll()
    if early_exit is not None and early_exit != 0:
        try:
            log_fh.flush()
        except OSError:
            pass
        log_fh.close()
        detail = _core._antigravity_read_log_tail(log_path, max_bytes=4000).strip()
        first_line = next((line.strip() for line in detail.splitlines() if line.strip()), "")
        return {
            "ok": False,
            "error": first_line or f"AGY exited with code {early_exit}",
            "code": "antigravity_launch_failed",
            "exit_code": early_exit,
            "log": str(log_path),
            "via": "antigravity-spawn",
            "engine": "antigravity",
        }

    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "fifo": None,
        "stdin_fd": None,
        "engine": "antigravity",
        "cwd": spawn_cwd,
        "repo_path": repo_for_logs,
        "model": model_to_use,
        "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=None,
        engine="antigravity",
        repo_path=repo_for_logs,
        model=model_to_use,
        parent_session_id=parent_session_id,
    )
    resp = {
        "ok": True,
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "via": "antigravity-spawn",
        "model": model_to_use,
    }
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    # AGY's --conversation option only resumes an existing conversation.  A
    # generated UUID here is not durable state, so wait briefly for AGY's real
    # session ID instead of returning a fabricated, unresumable one.
    return _finalize_spawn_response(resp, entry, ctx)


def _resume_session_antigravity_app(session_id, text):
    if not _core._antigravity_app_conversation_path(session_id):
        return {
            "ok": False,
            "error": "Antigravity app session is not in the app conversation store.",
            "code": "antigravity_app_conversation_missing",
            "via": "antigravity-app",
        }
    cfg_result = _core._antigravity_latest_user_config(session_id)
    if not cfg_result.get("ok"):
        rpc = cfg_result.get("rpc")
        if rpc and not rpc.get("ok"):
            # Pass through the actual RPC failure (app not running,
            # session not loaded into the app's cascade store, etc.) —
            # the generic "no reusable model" error misled users into
            # thinking the fix was always "pick a model in Antigravity"
            # when the real cause was "Antigravity itself isn't ready".
            rpc.setdefault("via", "antigravity-app")
            return rpc
        return {
            "ok": False,
            "error": "Antigravity app session has no reusable model config. Open the session in Antigravity, select a model, then retry.",
            "code": "antigravity_app_model_config_missing",
            "via": "antigravity-app",
        }
    user_config = cfg_result["config"]
    result = _core._antigravity_app_rpc(
        "SendUserCascadeMessage",
        {
            "cascadeId": session_id,
            "items": [{"text": text}],
            "cascadeConfig": user_config,
        },
        timeout=10,
    )
    if not result.get("ok"):
        result.setdefault("via", "antigravity-app")
        return result
    _core._record_interaction(session_id)
    return {
        "ok": True,
        "resumed": True,
        "via": "antigravity-app",
        "port": result.get("port"),
    }


# A headless Antigravity resume that's still "alive" past this window is treated
# as a lingering/hung process rather than a real in-flight turn, so new input
# resumes fresh instead of queuing behind it forever (CCC-42/43). Must exceed
# the --print-timeout we pass to agy, else a legit long turn gets a second agy
# process racing it on the same conversation.
def _antigravity_resume_stale_seconds():
    return _core._antigravity_print_timeout_seconds() + 60


def _spawn_entry_started_epoch(entry):
    """Epoch seconds for a spawn entry's ``started`` stamp (``%Y%m%dT%H%M%S``,
    local time), or 0 when missing/unparseable."""
    raw = (entry or {}).get("started")
    if not raw:
        return 0
    try:
        return time.mktime(time.strptime(str(raw), "%Y%m%dT%H%M%S"))
    except (ValueError, TypeError, OverflowError):
        return 0


def resume_session_antigravity(session_id, text):
    """Resume an Antigravity conversation through AGY CLI or the running app.

    Routing:
      1. If the AGY CLI has a `.pb` for this sid → CLI print-mode resume
         (the original interactive-CLI session being continued headlessly).
      2. Else if the AGY app has a conversation file for this sid → app
         RPC resume (the user is in Antigravity.app and we inject via
         the language-server RPC).
      3. Else → CLI print-mode anyway with --conversation <sid> -p <text>.
         AGY rehydrates from the brain transcript on disk; previously
         orphaned transcripts (no .pb, no app file) were dead — now they
         get the new turn appended via headless print.
    """
    text = _core._strip_ccc_session_state_instruction(text)
    if not session_id or not text:
        return {"ok": False, "error": "missing session_id or text"}
    cli_conversation = _core._antigravity_cli_conversation_path(session_id)
    has_cli_pb = bool(cli_conversation and cli_conversation.is_file())
    if not has_cli_pb and _core._antigravity_app_conversation_path(session_id):
        return _core._resume_session_antigravity_app(session_id, text)
    resolved = _core._resolve_antigravity_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    for s in _core._spawned_sessions:
        if s.get("engine") == "antigravity" and s.get("resumed_sid") == session_id:
            try:
                if _core._poll_spawn_entry(s) is None:
                    # The prior resume process still LOOKS alive. A headless
                    # Antigravity turn that has overrun a sane window is almost
                    # always a lingering/hung process, not real work — queuing
                    # behind it freezes input forever and paints a permanent
                    # "thinking"/"Queued" (CCC-42/43). Past the threshold, treat
                    # it as done and fall through to a fresh resume instead.
                    started = _spawn_entry_started_epoch(s)
                    if started and (time.time() - started) > _antigravity_resume_stale_seconds():
                        continue
                    with _core._pending_resume_lock:
                        _core._pending_resume_queue.setdefault(session_id, []).append(text)
                    _core._save_pending_inputs()
                    return {

                        "ok": True,
                        "queued": True,
                        "pid": s.get("pid"),
                        "via": "antigravity-resume-queued",
                        "queued_reason": "waiting for the current Antigravity turn to finish (it can't take input mid-turn)",
                    }
            except Exception:
                pass
    spawned_ctx = _core._spawn_registry_entry_for_session(session_id, "antigravity") or {}
    cwd = spawned_ctx.get("cwd") or _core.find_session_cwd(session_id) or str(Path.cwd())
    if not Path(cwd).is_dir():
        cwd = str(Path.cwd())
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-antigravity-{session_id[:8]}-{timestamp}.log"
    repo_for_logs = _core._git_toplevel_for_existing_dir(cwd) or cwd
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    override = _core._get_session_override(session_id)
    model_to_use = _core._antigravity_model_settings_label(
        ((override or {}).get("model") if override else None)
        or os.environ.get("CCC_ANTIGRAVITY_MODEL")
        or ""
    )
    if model_to_use:
        settings_result = _core._set_antigravity_cli_model(model_to_use)
        if not settings_result.get("ok"):
            return {
                **settings_result,
                "via": "antigravity-resume",
            }
        model_to_use = settings_result.get("model") or model_to_use

    cmd = _core._antigravity_command_words(resolved)
    user_args = cmd[1:]
    if (
        os.environ.get("CCC_ANTIGRAVITY_SKIP_PERMISSIONS", "1").strip().lower()
        not in ("0", "false", "no", "off")
        and not _core._antigravity_has_arg(user_args, "--dangerously-skip-permissions")
    ):
        cmd.append("--dangerously-skip-permissions")
    add_dirs = []
    if not _core._antigravity_has_arg(user_args, "--add-dir"):
        add_dirs.append(cwd)
    add_dirs.extend(_core._pasted_image_parent_dirs(text))
    _core._antigravity_add_dirs(cmd, user_args, add_dirs)
    cli_log_path = Path(str(log_path) + ".agy.log")
    if not _core._antigravity_has_arg(user_args, "--log-file"):
        cmd.extend(["--log-file", str(cli_log_path)])
    if not _core._antigravity_has_arg(user_args, "--print-timeout"):
        cmd.extend(["--print-timeout", _core._antigravity_print_timeout()])
    cmd.extend(["--conversation", session_id, "-p", text])

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
        return {"ok": False, "error": str(e), "via": "antigravity-resume"}

    entry = {
        "pid": proc.pid,
        "name": f"resume-antigravity-{session_id[:8]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
        "fifo": None,
        "stdin_fd": None,
        "engine": "antigravity",
        "cwd": cwd,
        "repo_path": repo_for_logs,
        "model": model_to_use,
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
        engine="antigravity",
        session_id=session_id,
        repo_path=repo_for_logs,
        model=model_to_use,
    )
    return {
        "ok": True,
        "pid": proc.pid,
        "log": str(log_path),
        "resumed": True,
        "via": "antigravity-resume",
        "model": model_to_use,
    }


def launch_antigravity_terminal(prompt="", name=None, cwd=None, repo_path=None, terminal_app=None):
    """Launch AGY in a real terminal for manual resume/switch.

    Existing Antigravity session resume is still handled inside the TUI via
    /resume. New-session spawn uses `agy -p` instead.
    """
    prompt = _core._strip_ccc_session_state_instruction(prompt or "")
    resolved = _core._resolve_antigravity_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    if sys.platform != "darwin":
        return {
            "ok": False,
            "error": "Antigravity CLI is an interactive TUI; CCC terminal launch is currently macOS-only.",
            "code": "antigravity_terminal_unavailable",
        }
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    session_name = _core._slugify(name or prompt or "antigravity") or "antigravity"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_dir = _core.repo_log_dir(ctx["repo_path"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"launch-antigravity-{session_name}-{timestamp}.log"
    prompt_path = None
    if prompt:
        prompt_path = log_dir / f"prompt-antigravity-{session_name}-{timestamp}.txt"
        try:
            prompt_path.write_text(prompt + "\n", encoding="utf-8")
        except OSError:
            prompt_path = None

    bin_and_args = _core._antigravity_shell_command(resolved)
    q_cwd = _core._shell_quote(spawn_cwd)
    notes = [
        "echo " + _core._shell_quote("CCC: starting Antigravity CLI in this workspace."),
        "echo " + _core._shell_quote("CCC: use /resume inside AGY to resume or switch sessions."),
    ]
    if prompt_path:
        notes.append("echo " + _core._shell_quote(f"CCC: prompt note saved at {prompt_path}"))
    command = f"cd {q_cwd} && " + " && ".join(notes) + f" && exec {bin_and_args}"

    def as_literal(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    cmd_lit = as_literal(command)
    target = terminal_app or _core._preferred_terminal_app()
    if target == "iTerm2":
        script = f'''
        tell application "iTerm2"
          activate
          set newWin to (create window with default profile)
          tell current session of newWin
            write text "{cmd_lit}"
          end tell
        end tell
        return "ok"
        '''
    else:
        script = f'''
        tell application "Terminal"
          activate
          do script "{cmd_lit}"
        end tell
        return "ok"
        '''

    try:
        lf = open(log_path, "w")
        proc = subprocess.Popen(["osascript", "-e", script], stdout=lf, stderr=lf)
    except (FileNotFoundError, OSError) as e:
        return {"ok": False, "error": str(e), "via": "antigravity-terminal"}

    return {
        "ok": True,
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt_path": str(prompt_path) if prompt_path else None,
        "interactive": True,
        "terminal_app": target,
        "via": "antigravity-terminal",
        "note": "Antigravity CLI opened in Terminal; enter the prompt in the AGY TUI.",
    }


def _write_spawn_error_event(log_fh, code, message):
    try:
        log_fh.write(json.dumps({
            "type": "system",
            "subtype": "spawn_error",
            "code": code,
            "error": message,
        }) + "\n")
        log_fh.flush()
    except Exception:
        pass


def _spawn_early_failure_payload(proc, log_path, log_fh, *, engine, via, delay=0.15):
    """Return an error payload when a just-spawned engine exits non-zero fast.

    A resolver can prove the binary exists, but auth/config/runtime failures
    often only show up after `Popen`. Catching the immediate non-zero case
    keeps `/api/sessions/spawn` from reporting success for a process that
    already died before callers could discover it.
    """
    try:
        time.sleep(delay)
        exit_code = proc.poll()
    except Exception:
        return None
    if exit_code is None:
        return None
    # Bare Mock.poll() returns another Mock; ignore non-real values so tests
    # and unusual Popen stand-ins keep behaving like a live child.
    if not isinstance(exit_code, int):
        return None
    if exit_code == 0:
        return None
    try:
        log_fh.flush()
    except OSError:
        pass
    try:
        log_fh.close()
    except OSError:
        pass
    detail = _core._antigravity_read_log_tail(log_path, max_bytes=4000).strip()
    first_line = next((line.strip() for line in detail.splitlines() if line.strip()), "")
    return {
        "ok": False,
        "error": first_line or f"{engine} exited with code {exit_code}",
        "code": f"{engine}_launch_failed",
        "exit_code": exit_code,
        "log": str(log_path),
        "via": via,
        "engine": engine,
    }


def _cursor_log_failure_message(text):
    """Human-facing Cursor Agent failure from stream-json/plain log output."""
    detail = str(text or "")
    if not detail.strip():
        return ""
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if "usage limit" in lowered or "get cursor pro" in lowered:
            return line.removeprefix("S:").strip()
        if "named models unavailable" in lowered:
            return line.removeprefix("S:").strip()
        if "not authenticated" in lowered or "login" in lowered and "cursor" in lowered:
            return line.removeprefix("S:").strip()
    return ""


def _cursor_early_failure_payload(proc, log_path, log_fh, *, via, delay=0.5):
    """Return a failure when Cursor Agent starts then immediately refuses work."""
    try:
        time.sleep(delay)
        exit_code = proc.poll()
    except Exception:
        return None
    if exit_code is None or not isinstance(exit_code, int):
        return None
    try:
        log_fh.flush()
    except OSError:
        pass
    detail = _core._antigravity_read_log_tail(log_path, max_bytes=4000).strip()
    message = _cursor_log_failure_message(detail)
    if exit_code == 0 and not message:
        return None
    try:
        log_fh.close()
    except OSError:
        pass
    first_line = next((line.strip() for line in detail.splitlines() if line.strip()), "")
    return {
        "ok": False,
        "error": message or first_line or f"Cursor exited with code {exit_code}",
        "code": "cursor_launch_failed",
        "exit_code": exit_code,
        "log": str(log_path),
        "via": via,
        "engine": "cursor",
    }


def _spawn_session_id_from_entry(entry):
    """Best-effort native session id resolver for a CCC-owned spawn entry."""
    if not isinstance(entry, dict):
        return None
    engine = entry.get("engine") or "claude"
    sid = entry.get("session_id") or entry.get("resumed_sid")
    if sid:
        if engine == "claude":
            if not _core._ensure_claude_desktop_session_visible(sid, spawn_entry=entry):
                _core._schedule_claude_desktop_visibility_retry(sid, spawn_entry=entry)
        elif engine == "codex":
            if _core._mark_codex_thread_user_visible(sid, update_rollout=True):
                _core._register_codex_sidebar_project_for_spawn_entry(entry, sid)
            else:
                _core._schedule_codex_visibility_retry(sid, spawn_entry=entry)
        elif engine == "cursor":
            _core._ensure_cursor_session_visible(sid, spawn_entry=entry)
        return sid
    log = entry.get("log")
    if engine == "codex":
        sid = _core._extract_codex_thread_id_from_log(log)
    elif engine == "gemini":
        sid = _core._extract_gemini_session_id_from_log(log)
    elif engine == "cursor":
        sid = _core._extract_cursor_chat_id_from_log(log) or _core._cursor_session_id_for_spawn_entry(entry)
    elif engine == "antigravity":
        meta = {}
        if log:
            meta = _antigravity_cli_log_meta(str(log) + ".agy.log")
            if not meta.get("session_id"):
                meta = _antigravity_cli_log_meta(log)
        sid = meta.get("session_id") or None
    elif engine == "devin":
        sid = _core._devin_cli_session_id_for_spawn_entry(entry)
    else:
        sid = _core.extract_session_id(log)
    if sid:
        entry["session_id"] = sid
        _core._update_spawn_session_id_in_registry(entry.get("pid"), sid)
        if engine == "claude":
            if not _core._ensure_claude_desktop_session_visible(sid, spawn_entry=entry):
                _core._schedule_claude_desktop_visibility_retry(sid, spawn_entry=entry)
        elif engine == "codex":
            if _core._mark_codex_thread_user_visible(sid, update_rollout=True):
                _core._register_codex_sidebar_project_for_spawn_entry(entry, sid)
            else:
                _core._schedule_codex_visibility_retry(sid, spawn_entry=entry)
        elif engine == "cursor":
            _core._ensure_cursor_session_visible(sid, spawn_entry=entry)
    return sid


_SPAWN_SESSION_ID_POLL_S = 0.1


def _wait_for_spawn_session_id(entry, timeout_s=0.75):
    """Poll for the native session id on a fixed ~0.1s wall-clock tick.

    The tick counts the resolver's own cost (for Devin that is a lock-dir
    scan, a ``ps`` fork and a DB scan, ~30ms) so the cadence stays even and
    the last probe lands at the deadline instead of overshooting it. The
    overall ``timeout_s`` budget is unchanged.
    """
    deadline = time.monotonic() + max(0.0, float(timeout_s or 0))
    tick_started = time.monotonic()
    sid = _core._spawn_session_id_from_entry(entry)
    while not sid:
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            break
        pause = _SPAWN_SESSION_ID_POLL_S - (now - tick_started)
        pause = min(max(pause, 0.0), remaining)
        if pause > 0:
            time.sleep(pause)
        tick_started = time.monotonic()
        sid = _core._spawn_session_id_from_entry(entry)
    return sid


def _finalize_spawn_response(resp, entry, ctx, *, wait_for_session_id=True):
    """Attach correlation and placement fields to a successful spawn response."""
    resp = dict(resp or {})
    pid = entry.get("pid") if isinstance(entry, dict) else resp.get("pid")
    spawn_id = entry.get("spawn_id") if isinstance(entry, dict) else resp.get("spawn_id")
    if pid is not None:
        resp.setdefault("pid", pid)
        spawn_id = spawn_id or str(pid)
    if spawn_id:
        resp["spawn_id"] = str(spawn_id)
        resp.setdefault("pid", spawn_id)
    engine = (entry.get("engine") if isinstance(entry, dict) else None) or resp.get("engine") or "claude"
    resp["engine"] = engine
    if isinstance(ctx, dict):
        if ctx.get("repo_path"):
            resp["repo_path"] = ctx["repo_path"]
        if ctx.get("cwd") and not resp.get("cwd"):
            resp["cwd"] = ctx["cwd"]
    if isinstance(entry, dict):
        if entry.get("repo_path"):
            resp["repo_path"] = entry["repo_path"]
        if entry.get("cwd"):
            resp["cwd"] = entry["cwd"]
        if entry.get("parent_session_id"):
            resp["parent_session_id"] = entry["parent_session_id"]
    sid = (
        _core._wait_for_spawn_session_id(entry)
        if wait_for_session_id
        else (
            (entry.get("session_id") or entry.get("resumed_sid"))
            if isinstance(entry, dict)
            else None
        ) or _core._spawn_session_id_from_entry(entry)
    )
    resp["session_id"] = sid
    resp["session_id_pending"] = not bool(sid)
    return resp


def _ux_fixes_scope_project(payload):
    """Resolve a repo-scoping project code from a claim/next payload.

    Returns the normalized project code when the caller supplied `project`
    and/or `repo_path`, else `None` (which keeps the global queue behavior so
    existing un-scoped callers are unaffected).
    """
    if not isinstance(payload, dict):
        return None
    project = str(payload.get("project") or "").strip()
    repo_path = str(payload.get("repo_path") or "").strip()
    if not project and not repo_path:
        return None
    return _core._q._project_for(repo_path=repo_path, project=project)


def _ux_fixes_worker_prompt(project, repo_path, ccc_url, extra_message=""):
    """Build the starting prompt for a repo-scoped UX-fixes worker session.

    The spawned session loops forever via idle self-wakeups (ScheduleWakeup),
    claims only its own project's tickets, applies the fix, then closes and
    advances — never a busy loop.

    ``extra_message`` is optional caller-supplied context appended verbatim as
    its own section (e.g. "dev server runs on :3001", "prioritise mobile
    tickets"). It does not replace the loop instructions.
    """
    extra = ""
    msg = re.sub(r"\s+$", "", str(extra_message or "")).strip()
    if msg:
        extra = (
            "\n## Extra instructions from whoever started you\n"
            "Treat the following as additional context/priorities on top of the "
            "loop above (it does not change how you claim/close tickets):\n\n"
            f"{msg[:4000]}\n"
        )
    return f"""You are the UX-fixes WORKER for project `{project}`, working in the \
repo at `{repo_path}`. Your job: drain this project's UX-fixes queue, one ticket \
at a time, forever — without a human pushing anything.

CCC server: {ccc_url}

## How you run (idle loop, NOT a busy loop)
Never stop waiting for tickets. Poll the queue once, and when it is empty, schedule \
an idle self-wakeup (ScheduleWakeup) a few minutes out and re-check on the next \
wakeup. Do NOT sit in a tight `while`/`sleep` loop and do NOT end the session — \
stay alive via wakeups so new tickets get picked up automatically.

## Network sandbox
Run every curl with the Bash network sandbox DISABLED (these are loopback/IPC calls \
to {ccc_url}; the sandbox blocks loopback and the curl will spuriously fail otherwise).

## Your own session id
You need your full session id for claim/close calls. Use `$CLAUDE_SESSION_ID` if set. \
If it is empty, find the newest `*.jsonl` under the `~/.claude/projects/` directory \
matching this repo's slugified path; its basename (minus `.jsonl`) is your session id.

## The loop
1. Claim a ticket scoped to YOUR project:
   curl -s -X POST {ccc_url}/api/ux-fixes/claim \\
     -H 'Content-Type: application/json' \\
     -d '{{"session_id": "<your-full-session-id>", "project": "{project}"}}'
   The response is `{{"ok": true, "item": <ticket|null>}}`. If `item` is null the \
   queue is drained — schedule a wakeup and stop for now.
   If you attribute claims with a human label instead of your raw UUID, ALSO pass \
   your real session UUID as `session_uuid` so the queue-health watcher can reach \
   you if your project's queue stalls (the label alone is not a reachable id).
2. Apply the fix described by the claimed ticket. Fields you get: `number` (the global \
   ticket id), `title`, `detail`, optional `screenshot_path`, and `repo_path`. Read the \
   screenshot if present. Make the change in this repo.
3. Close the finished ticket AND claim the next one in one call:
   curl -s -X POST {ccc_url}/api/ux-fixes/next \\
     -H 'Content-Type: application/json' \\
     -d '{{"session_id": "<your-full-session-id>", "close_number": <the number you just finished>, "project": "{project}"}}'
   The response is `{{"ok": true, "closed": ..., "next": <ticket|null>}}`. If `next` is \
   non-null, go to step 2 with it. If null, schedule a wakeup and stop for now.

## Git hygiene
Follow THIS repo's own CLAUDE.md git rules. Commit only the paths you changed \
(`git commit --only <paths> -m "..."`), keep commits small, never `git add -A`/`.`, \
never branch, and do NOT push unless explicitly asked.
{extra}"""


# A "return address" is the dispatching session's UUID. Validate loosely —
# Claude/Codex/Gemini/Cursor session ids are UUID-ish (hex + dashes), but stay
# permissive enough for any reasonable engine id. The value lands in prompt
# text and a curl the spawned agent runs, so reject anything that could break
# out of either: shell metachars, quotes, whitespace, control chars.
# Optionally node-qualified: "<node-uuid>:<session-id>" — the federated
# global reference form. A remote child reports to "<parent-node>:<sid>";
# its own CCC parses the prefix and proxies the inject to the owning node.
_RETURN_ADDRESS_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}:)?[A-Za-z0-9][A-Za-z0-9_.-]{7,127}$")
_RETURN_ADDRESS_FOOTER_RE = re.compile(
    r"dispatched by another CCC session \(id `([A-Za-z0-9_.:-]{8,166})`\)"
)
_ANNOUNCED_FROM_RE = re.compile(r"^[A-Za-z0-9/][A-Za-z0-9_. @:+/-]{0,79}$")


def _normalize_return_address(payload):
    """Pull the optional return address from a spawn payload.

    Accepts `report_to` (canonical) and the aliases `return_to` / `reply_to`
    for ergonomics. Returns `(value_or_None, error_or_None)`.
    """
    raw = None
    for key in ("report_to", "return_to", "reply_to"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            raw = v.strip()
            break
    if raw is None:
        return None, None
    if not _RETURN_ADDRESS_RE.match(raw):
        return None, (
            "report_to must be a session id "
            "(8-128 chars, letters/digits/_.- only)"
        )
    return raw, None


def _normalize_spawn_parent_session_id(payload, report_to=None):
    """Resolve optional parent linkage for a spawned session.

    `report_to` already identifies the dispatching session for async callbacks,
    so use it as the default parent when the caller does not pass an explicit
    parent id.
    """
    raw = None
    for key in ("parent_session_id", "parentSessionId", "parent_sid"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            raw = v.strip()
            break
    if raw is None and report_to:
        raw = str(report_to).strip()
    if raw is None:
        return None, None
    if not _RETURN_ADDRESS_RE.match(raw):
        return None, (
            "parent_session_id must be a session id "
            "(8-128 chars, letters/digits/_.- only)"
        )
    return raw, None


def _parent_session_id_from_return_address_text(text):
    """Recover legacy spawn hierarchy from CCC's report-back footer."""
    if not isinstance(text, str) or "Return address" not in text:
        return ""
    m = _RETURN_ADDRESS_FOOTER_RE.search(text)
    if not m:
        return ""
    sid = m.group(1).strip()
    return sid if _RETURN_ADDRESS_RE.match(sid) else ""


_CONTINUATION_ORIGIN_RE = re.compile(
    r"Origin session id: ([A-Za-z0-9][A-Za-z0-9_.-]{7,127})"
)


def _continued_from_session_id_from_text(text):
    """Recover the origin session of an F2/auto-resume continuation.

    Continuation prompts (f2RetrievalPrompt in static/app.js, and the
    server-side _usage_limit_retrieval_prompt port) embed an
    "Origin session id: <sid>" line. A genuinely spawned subagent's prompt
    never does, so unlike the return-address footer this marks continuation
    lineage specifically.
    """
    if not isinstance(text, str) or "Origin session id:" not in text:
        return ""
    m = _CONTINUATION_ORIGIN_RE.search(text)
    if not m:
        return ""
    return m.group(1).strip()


def _parent_session_id_from_transcript_return_address(path):
    """Read a transcript's first user prompt and extract a CCC return address."""
    try:
        with open(path, "r") as fh:
            for i, line in enumerate(fh):
                if i >= 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "user" or ev.get("isMeta"):
                    continue
                parent = _core._parent_session_id_from_return_address_text(
                    _core._extract_user_prompt_text(ev)
                )
                if parent:
                    return parent
                return ""
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def _continued_from_session_id_from_transcript(path):
    """Read a transcript's first user prompt and extract the continuation origin."""
    try:
        with open(path, "r") as fh:
            for i, line in enumerate(fh):
                if i >= 20:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "user" or ev.get("isMeta"):
                    continue
                origin = _continued_from_session_id_from_text(
                    _core._extract_user_prompt_text(ev)
                )
                if origin:
                    return origin
                return ""
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def _normalize_announced_from(payload):
    """Pull the optional injected-message sender label from a payload."""
    raw = None
    for key in ("announced_from", "announce_from"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            raw = v.strip()
            break
    if raw is None:
        return None, None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None, "announced_from must not contain control characters"
    value = re.sub(r"\s+", " ", raw)
    if not _ANNOUNCED_FROM_RE.match(value):
        return None, (
            "announced_from must be 1-80 chars "
            "(letters/digits/spaces and _. @:+/- only)"
        )
    return value, None


def _wrap_injected_text_with_announced_from(text, announced_from):
    """Prefix injected follow-up text with explicit sender attribution.

    Slash commands are returned unwrapped (CCC-1000 Phase 4). A prefix pushes
    the leading slash off the start of the string, and a slash command only
    executes when it is the first thing on the line -- so wrapping one turns it
    into inert prose on every transport, and it also blinds the UDS slash guard
    in _try_uds_peer_delivery, which is exactly the case that guard exists for.
    Attribution is meaningless for a command anyway: it has no sender semantics,
    it either runs or it does not.
    """
    label = (announced_from or "").strip()
    if not label:
        return text
    if _core._SLASH_COMMAND_TRIGGER_RE.match(str(text or "")):
        return text
    return f"Announced from: {label}\n\n{text}"


def _wrap_prompt_with_return_address(prompt, report_to, port=None, engine="claude"):
    """Append a 'report back when done' footer addressed to `report_to`.

    Engine-agnostic by default: plain prompt text instructing the spawned
    agent to POST one structured completion report to /api/inject-input,
    targeting the dispatching session. Works identically for claude / codex /
    cursor / antigravity since none of them need a special channel — they all
    can run the curl. No-op when `report_to` is falsy.

    Claude children report back over native peer messaging (SendMessage to
    CCC's own peer identity) instead, but ONLY when the uds gate is on AND
    CCC's own peer socket is actually listening (checked via
    _CCC_PEER_STATE, not just the env flag) -- a footer telling a child to
    SendMessage a receiver that doesn't exist would silently black-hole the
    report. Every other case (gate off, non-Claude engine, or the gate on
    but CCC's own listener didn't start) keeps the curl footer byte-for-byte,
    so this is a strict opt-in with automatic fallback, not a behavior
    change for anyone not on the flag.
    """
    rid = (report_to or "").strip()
    if not rid:
        return prompt
    envelope = json.dumps({
        "session_id": rid, "mode": "steer",
        "announced_from": "<your session name or id>", "text": "<your report>",
    })
    use_sendmessage = (
        engine == "claude"
        and _core._uds_messaging_enabled()
        and bool(_core._CCC_PEER_STATE.get("socket_path"))
    )
    if use_sendmessage:
        return prompt + (
            "\n\n---\n"
            "## Return address — report back when done\n"
            f"You were dispatched by another CCC session (id `{rid}`). When this "
            "task is fully complete — whether it SUCCEEDED or FAILED — send exactly "
            "ONE completion report back via Claude Code's native peer messaging. "
            "Call SendMessage with agent=\"ccc\" and a message whose ENTIRE content "
            "is exactly this JSON (fill in your own values, keep it valid JSON, "
            "escape quotes/newlines in \"text\"):\n\n"
            f"```\n{envelope}\n```\n\n"
            "The report text must contain, in this order:\n"
            "- STATUS: SUCCEEDED or FAILED\n"
            "- SUMMARY: 1-3 sentence summary of what you did\n"
            "- FILES: relevant file paths touched/created (or \"none\")\n"
            "- REASON: if FAILED, the reason and what blocked you (omit if succeeded)\n\n"
            "Send the report once, at the very end — not progress updates mid-task.\n"
        )
    p = port or _core.PORT
    footer = (
        "\n\n---\n"
        "## Return address — report back when done\n"
        f"You were dispatched by another CCC session (id `{rid}`). When this "
        "task is fully complete — whether it SUCCEEDED or FAILED — send exactly "
        "ONE completion report back to that session via CCC's inject-input API. "
        "Run the curl with the network sandbox disabled (localhost IPC):\n\n"
        "```bash\n"
        f"curl -s --max-time 30 -X POST \"http://127.0.0.1:{p}/api/inject-input\" \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        f"  -d '{{\"session_id\": \"{rid}\", \"mode\": \"steer\", \"announced_from\": \"<your session name or id>\", \"text\": \"<your report>\"}}'\n"
        "```\n\n"
        "Use `\"mode\": \"steer\"` exactly as shown — without it the report "
        "queues behind whatever the dispatching session is doing and can sit "
        "unseen for a long time; steer delivers it right away.\n\n"
        "The report text must contain, in this order:\n"
        "- STATUS: SUCCEEDED or FAILED\n"
        "- SUMMARY: 1-3 sentence summary of what you did\n"
        "- FILES: relevant file paths touched/created (or \"none\")\n"
        "- REASON: if FAILED, the reason and what blocked you (omit if succeeded)\n\n"
        "Send the report once, at the very end — not progress updates mid-task. "
        "Remember to JSON-escape the text (quotes, newlines) so the payload is valid.\n"
    )
    return prompt + footer


def _claude_spawn_command(claude_bin, model, session_name, session_id, capabilities, effort=""):
    cmd = [
        claude_bin, "-p", "--verbose",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--model", model,
        "--dangerously-skip-permissions",
        "--name", session_name,
    ]
    # `--effort` is a top-level session flag, not a resume-only one, so a cold
    # spawn can honour the picker instead of falling back to the CLI default.
    # An unknown level only warns, but validate anyway: the spawn is headless
    # and nobody reads that warning.
    effort = str(effort or "").strip().lower()
    if effort and effort in _core.CLAUDE_REASONING_EFFORTS:
        cmd.extend(["--effort", effort])
    if session_id:
        cmd.extend(["--session-id", session_id])
    if capabilities.get("partial_messages"):
        cmd.append("--include-partial-messages")
    cmd.extend(_core._claude_session_state_args())
    cmd.extend(_core._claude_peer_inbound_args())
    return cmd


def _discard_claude_prewarm(entry):
    """Retire one unclaimed reservation and remove only its private artifacts."""
    if not isinstance(entry, dict):
        return
    _core._log_activity(
        "prewarm", "PREWARM_EXPIRED",
        f"pid={entry.get('pid')} prewarm_id={str(entry.get('prewarm_id') or '')[:8]} "
        f"name={entry.get('name') or '-'}",
    )
    _core._record_prewarm_event("prewarm_expired", str(entry.get("prewarm_id") or ""), entry)
    _core._retire_unresponsive_spawn_entry(entry, terminate=True, reason="prewarm_expired")
    _core._unlink_quiet(entry.get("log"))
    sid = str(entry.get("session_id") or "").strip()
    cwd = entry.get("cwd")
    if sid and cwd:
        try:
            _core._canonical_conversation_path(cwd, sid).unlink(missing_ok=True)
        except OSError:
            pass


def _prune_claude_prewarms(now=None):
    now = float(now if now is not None else time.time())
    expired = []
    with _core._CLAUDE_PREWARM_LOCK:
        for prewarm_id, entry in list(_core._CLAUDE_PREWARMS.items()):
            proc = entry.get("proc") if isinstance(entry, dict) else None
            try:
                alive = proc is not None and proc.poll() is None
            except Exception:
                alive = False
            age = now - float(entry.get("created_at_epoch") or 0)
            if not alive or age > _core._CLAUDE_PREWARM_TTL_S:
                expired.append(_core._CLAUDE_PREWARMS.pop(prewarm_id))
    for entry in expired:
        _core._discard_claude_prewarm(entry)


def _store_claude_prewarm(entry):
    """Install one bounded, tab-owned reservation and retire superseded ones."""
    evicted = []
    client_id = str(entry.get("client_id") or "").strip()
    with _core._CLAUDE_PREWARM_LOCK:
        if client_id:
            for existing_id, existing in list(_core._CLAUDE_PREWARMS.items()):
                if str(existing.get("client_id") or "").strip() == client_id:
                    evicted.append(_core._CLAUDE_PREWARMS.pop(existing_id))
        while len(_core._CLAUDE_PREWARMS) >= _core._CLAUDE_PREWARM_MAX:
            oldest_id = min(
                _core._CLAUDE_PREWARMS,
                key=lambda key: float(
                    (_core._CLAUDE_PREWARMS.get(key) or {}).get("created_at_epoch") or 0
                ),
            )
            evicted.append(_core._CLAUDE_PREWARMS.pop(oldest_id))
        _core._CLAUDE_PREWARMS[entry["prewarm_id"]] = entry
    for old_entry in evicted:
        _core._discard_claude_prewarm(old_entry)


def _watch_prewarm_readiness(entry, log_path):
    """Watch a prewarm's stdout log for the system/init event.

    Claude emits {"type":"system","subtype":"init",...} once it has finished
    SessionStart hooks and MCP setup and is ready to process input. We tail
    the log file (Claude's stdout is redirected there) and set entry["ready"]
    + signal the ready_event when we see it.

    Exits when: init event seen, process dies, or 90s timeout (the prewarm
    TTL is 120s, so this is well within the expiry window).
    """
    deadline = time.time() + 90
    path = str(log_path)
    try:
        # Wait for the log file to have content
        while time.time() < deadline:
            try:
                if Path(path).stat().st_size > 0:
                    break
            except OSError:
                pass
            # Check if the process died
            proc = entry.get("proc")
            if proc and proc.poll() is not None:
                return
            time.sleep(0.1)
        # Tail the log file looking for the init event
        with open(path, "r") as f:
            while time.time() < deadline:
                line = f.readline()
                if not line:
                    # No new data — check if process is still alive
                    proc = entry.get("proc")
                    if proc and proc.poll() is not None:
                        return
                    time.sleep(0.05)
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if (
                    isinstance(ev, dict)
                    and ev.get("type") == "system"
                    and ev.get("subtype") == "init"
                ):
                    with entry.get("ready_lock", threading.Lock()):
                        entry["ready"] = True
                    ev_obj = entry.get("ready_event")
                    if ev_obj:
                        ev_obj.set()
                    _core._log_activity(
                        "prewarm", "PREWARM_READY",
                        f"pid={entry.get('pid')} prewarm_id={str(entry.get('prewarm_id') or '')[:8]}",
                    )
                    return
    except Exception:
        pass


def _start_claude_prewarm(
    cwd=None, repo_path=None, model=None, name=None, client_id=None,
    reasoning_effort="", auto_compact_k=None,
):
    """Start a prompt-less Claude stream process for the new-session composer.

    Claude performs its SessionStart hooks and MCP setup before the first
    stream-json user message arrives. Reserving that exact process while the
    user types moves the expensive initialization off the submit-to-text path.

    The reservation bakes in `--effort`, so the effort is part of its identity:
    a spawn asking for a different level must not adopt this process.
    """
    reasoning_effort = _core._validate_reasoning_effort(reasoning_effort, "claude")
    auto_compact_k = _core._validate_auto_compact_k(auto_compact_k)
    routed, _dropped = _route_claude_call_with_kwarg_fallback(
        "prewarm", {
            "cwd": cwd,
            "repo_path": repo_path,
            "model": model,
            "name": name,
            "client_id": client_id,
            "reasoning_effort": reasoning_effort,
            "auto_compact_k": auto_compact_k,
        },
    )
    if routed is not None:
        # The worker ran the prewarm — record the event in the dashboard's
        # ring buffer so the UI can surface it. The worker has its own
        # _PREWARM_EVENTS but the frontend polls the dashboard, not the worker.
        if isinstance(routed, dict) and routed.get("ok"):
            _core._log_activity(
                "prewarm", "PREWARM",
                f"pid={routed.get('pid')} prewarm_id={str(routed.get('prewarm_id') or '')[:8]} "
                f"name={name or '-'} cwd={cwd or '-'} (via worker)",
            )
            _core._record_prewarm_event("prewarm_spawned", str(routed.get("prewarm_id") or ""), {
                "pid": routed.get("pid"),
                "name": name,
                "cwd": cwd,
            })
        return routed
    _core._prune_claude_prewarms()
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    model_to_use = _core._cli_model_flag(_core._spawn_model_for_engine("claude", model) or "opus")
    session_name = _core._slugify(name or "")
    if not session_name:
        return {"ok": False, "available": False, "code": "prewarm_name_required"}
    claude_bin = _core._resolve_claude_bin()
    if not claude_bin.get("available"):
        return {
            "ok": False,
            "error": claude_bin.get("reason") or "Claude Code CLI not found",
            "code": claude_bin.get("code", "claude_unavailable"),
        }
    capabilities = _core._claude_spawn_capabilities(claude_bin["bin"])
    if not capabilities.get("ready"):
        capabilities = _core._probe_claude_spawn_capabilities(claude_bin["bin"])
    if not capabilities.get("session_id"):
        return {"ok": False, "available": False, "code": "prewarm_unsupported"}

    prewarm_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    log_dir = _core.repo_log_dir(ctx["repo_path"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f".ccc-prewarm-{prewarm_id}.log"
    log_fh = open(log_path, "w")
    fifo_path, child_stdin_fd = _core._make_stdin_fifo(log_path)
    cmd = _core._claude_spawn_command(
        claude_bin["bin"], model_to_use, session_name, session_id, capabilities,
        effort=reasoning_effort,
    )
    popen_kwargs = dict(
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=spawn_cwd,
        start_new_session=True,
        env=_core._spawn_env(auto_compact_k=auto_compact_k),
    )
    popen_kwargs["stdin"] = child_stdin_fd if child_stdin_fd is not None else subprocess.PIPE
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except (FileNotFoundError, OSError) as exc:
        log_fh.close()
        if child_stdin_fd is not None:
            _core._close_fd_quiet(child_stdin_fd)
        _core._unlink_quiet(fifo_path)
        _core._unlink_quiet(log_path)
        return {"ok": False, "error": str(exc), "code": "claude_unavailable"}
    stdin_fd = _core._open_fifo_writer(fifo_path) if fifo_path else None
    if child_stdin_fd is not None:
        _core._close_fd_quiet(child_stdin_fd)
    entry = {
        "prewarm_id": prewarm_id,
        "created_at_epoch": time.time(),
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": "",
        "started": time.strftime("%Y%m%dT%H%M%S"),
        "proc": proc,
        "log_fh": log_fh,
        "fifo": fifo_path,
        "stdin_fd": stdin_fd,
        "engine": "claude",
        "cwd": spawn_cwd,
        "repo_path": ctx["repo_path"],
        "model": model_to_use,
        "parent_session_id": "",
        "session_id": session_id,
        "partial_messages": bool(capabilities.get("partial_messages")),
        "command": list(cmd),
        "prewarmed": True,
        "client_id": str(client_id or "").strip(),
        "reasoning_effort": reasoning_effort,
        "auto_compact_k": auto_compact_k,
        "ready": False,
        "ready_lock": threading.Lock(),
        "ready_event": threading.Event(),
    }
    _core._store_claude_prewarm(entry)
    # Start a background thread that watches the prewarm's stdout log for the
    # system/init event, which signals Claude has finished booting (SessionStart
    # hooks, MCP setup) and is ready to process input. The claim path waits for
    # this before writing the prompt, so the user doesn't pay the boot latency
    # after claim.
    threading.Thread(
        target=_watch_prewarm_readiness,
        args=(entry, log_path),
        daemon=True,
        name=f"ccc-prewarm-watch-{prewarm_id[:8]}",
    ).start()
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=entry["started"],
        command_summary="",
        fifo=fifo_path,
        engine="claude",
        session_id=session_id,
        repo_path=ctx["repo_path"],
        model=model_to_use,
        prewarm=True,
        prewarm_id=prewarm_id,
        client_id=entry["client_id"],
        reasoning_effort=reasoning_effort,
        auto_compact_k=auto_compact_k,
        created_at_epoch=entry["created_at_epoch"],
    )
    expiry_timer = threading.Timer(
        _core._CLAUDE_PREWARM_TTL_S + 1, _core._prune_claude_prewarms,
    )
    expiry_timer.daemon = True
    expiry_timer.start()
    _core._log_activity(
        "prewarm", "PREWARM",
        f"pid={proc.pid} prewarm_id={prewarm_id[:8]} name={session_name} "
        f"cwd={spawn_cwd or '-'} model={model_to_use or '-'} "
        f"effort={reasoning_effort or '-'} ttl={_core._CLAUDE_PREWARM_TTL_S}s",
    )
    _core._record_prewarm_event("prewarm_spawned", prewarm_id, entry)
    return {
        "ok": True,
        "prewarm_id": prewarm_id,
        "session_id": session_id,
        "pid": proc.pid,
        "cwd": spawn_cwd,
        "repo_path": ctx["repo_path"],
        "model": model_to_use,
        "reasoning_effort": reasoning_effort,
        "auto_compact_k": auto_compact_k,
    }


def _take_claude_prewarm(prewarm_id, cwd, model, name=None, effort="", auto_compact_k=None):
    if not prewarm_id:
        return None
    _core._prune_claude_prewarms()
    with _core._CLAUDE_PREWARM_LOCK:
        entry = _core._CLAUDE_PREWARMS.get(str(prewarm_id))
        if not entry:
            return None
        # Name is NOT checked: the prewarm uses a stable placeholder name
        # ("prewarm-<cwd-basename>"), not the user's text. The real session
        # name is set after claim. Matching on cwd+model+effort is sufficient
        # — those are baked into the reserved argv and can't be changed.
        if (
            entry.get("cwd") != cwd
            or entry.get("model") != model
            or str(entry.get("reasoning_effort") or "") != str(effort or "")
            or str(entry.get("auto_compact_k") or "") != str(auto_compact_k or "")
        ):
            return None
        entry = _core._CLAUDE_PREWARMS.pop(str(prewarm_id))
    try:
        if entry["proc"].poll() is not None:
            _core._discard_claude_prewarm(entry)
            return None
    except Exception:
        _core._discard_claude_prewarm(entry)
        return None
    _core._log_activity(
        "prewarm", "PREWARM_CLAIMED",
        f"pid={entry.get('pid')} prewarm_id={str(prewarm_id)[:8]} "
        f"name={entry.get('name') or '-'}",
    )
    _core._record_prewarm_event("prewarm_claimed", str(prewarm_id), entry)
    return entry


def _take_claude_prewarm_for_request(
    prewarm_id, cwd=None, repo_path=None, model=None, name=None, effort="",
    auto_compact_k=None,
):
    """Claim a reservation using its already-validated launch context.

    The prewarm endpoint resolved and allowed both paths before creating the
    opaque id. Re-validating the caller's concrete paths against that stored
    context is sufficient and avoids repeating the full repo registry lookup
    on the click-to-ack path. A mismatch leaves the reservation untouched and
    the caller falls back to the normal cold validation path.
    """
    if not prewarm_id or not (cwd or repo_path):
        return None
    _core._prune_claude_prewarms()

    def same_path(requested, expected):
        if not requested:
            return True
        try:
            candidate = Path(str(requested)).expanduser().resolve()
            return candidate.is_dir() and candidate == Path(str(expected)).expanduser().resolve()
        except (OSError, ValueError, RuntimeError):
            return False

    with _core._CLAUDE_PREWARM_LOCK:
        entry = _core._CLAUDE_PREWARMS.get(str(prewarm_id))
        if not entry:
            return None
        if entry.get("model") != model:
            return None
        # Name is NOT checked: the prewarm uses a stable placeholder name,
        # not the user's text. See _take_claude_prewarm for rationale.
        # The reserved argv already carries --effort, so a different level is a
        # miss: adopting it would silently launch at the wrong effort.
        if str(entry.get("reasoning_effort") or "") != str(effort or ""):
            return None
        if str(entry.get("auto_compact_k") or "") != str(auto_compact_k or ""):
            return None
        if not same_path(cwd, entry.get("cwd")):
            return None
        if not same_path(repo_path, entry.get("repo_path")):
            return None
        entry = _core._CLAUDE_PREWARMS.pop(str(prewarm_id))
    try:
        if entry["proc"].poll() is not None:
            _core._discard_claude_prewarm(entry)
            return None
    except Exception:
        _core._discard_claude_prewarm(entry)
        return None
    _core._log_activity(
        "prewarm", "PREWARM_CLAIMED",
        f"pid={entry.get('pid')} prewarm_id={str(prewarm_id)[:8]} "
        f"name={entry.get('name') or '-'}",
    )
    _core._record_prewarm_event("prewarm_claimed", str(prewarm_id), entry)
    return entry


def _schedule_session_model_update(session_id, model, context_1m, reasoning_effort=None):
    """Persist non-critical model UI metadata after the first-response window."""
    timer = threading.Timer(
        12, _core._set_session_model, args=(session_id, model, context_1m, reasoning_effort),
    )
    timer.daemon = True
    timer.start()


_UNEXPECTED_KWARG_RE = re.compile(r"unexpected keyword argument '([^']+)'")


def _unexpected_keyword_argument(result):
    """The routed argument an older worker rejected, or "" if that isn't why.

    worker_engines splats these args straight into the legacy function, so any
    optional field this server has learned and the running worker has not comes
    back as a TypeError raised BEFORE the work happened. Naming the key lets the
    caller retry without it instead of failing the call on a version skew.
    """
    if not isinstance(result, dict) or result.get("ok"):
        return ""
    match = _UNEXPECTED_KWARG_RE.search(str(result.get("error") or ""))
    return match.group(1) if match else ""


def _route_claude_call_with_kwarg_fallback(operation, route_args, idempotency_key=None):
    """Route one Claude engine call, shedding args an older worker rejects.

    Returns (result, dropped_keys). Each retry gets a fresh action id so the
    previous worker's durable failed work item is not replayed.
    """
    result = _core._control_plane_engine_call(
        "claude", operation, dict(route_args), idempotency_key=idempotency_key,
    )
    dropped = []
    if result is None:
        return None, dropped
    retry_args = dict(route_args)
    while True:
        stale_key = _unexpected_keyword_argument(result)
        if not stale_key or stale_key not in retry_args:
            return result, dropped
        retry_args.pop(stale_key)
        dropped.append(stale_key)
        retried = _core._control_plane_engine_call(
            "claude", operation, retry_args, idempotency_key=str(uuid.uuid4()),
        )
        if not isinstance(retried, dict):
            return result, dropped
        result = retried


def spawn_session(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None,
                  parent_session_id=None, timeline_t0_epoch_ms=None, prewarm_id=None,
                  auto_compact_k=None, reasoning_effort=""):
    """Spawn a headless Claude Code session and return tracking info.

    The spawned subprocess requires an explicit cwd or repo_path.

    `reasoning_effort` is Claude's `--effort` level for the whole session. It
    is fixed at launch: headless Claude has no live effort switch, so the
    picker's later changes take effect on the next resume.

    If `worktree=True`, create a fresh git worktree off the launch cwd on a
    `feat/<slug>` branch and run the spawned session there. The worktree path
    + branch are returned in the response under
      `worktree_path` / `worktree_branch` so the UI can show them.
    """
    reasoning_effort = _core._validate_reasoning_effort(reasoning_effort, "claude")
    route_args = {
        "prompt": prompt,
        "name": name,
        "cwd": cwd,
        "repo_path": repo_path,
        "worktree": bool(worktree),
        "model": model,
        "parent_session_id": parent_session_id,
        "timeline_t0_epoch_ms": timeline_t0_epoch_ms,
        "prewarm_id": prewarm_id,
        "auto_compact_k": auto_compact_k,
        "reasoning_effort": reasoning_effort,
    }
    routed, dropped = _route_claude_call_with_kwarg_fallback(
        "spawn", route_args, idempotency_key=_core._take_control_plane_action_id(),
    )
    if routed is not None:
        if isinstance(routed, dict) and routed.get("ok") and "prewarm_id" in dropped:
            # The retry that succeeded ran cold, so the UI can explain why this
            # spawn was slower than the reserved one it was promised.
            routed["prewarm_fallback"] = True
        return routed
    if os.environ.get("CCC_SSH_HOST"):
        try:
            import ssh_multiplexer
            if ssh_multiplexer.get_global_multiplexer():
                return spawn_session_remote(
                    prompt, name=name, cwd=cwd, repo_path=repo_path, worktree=worktree,
                    model=model, parent_session_id=parent_session_id,
                    auto_compact_k=auto_compact_k, engine="claude"
                )
        except Exception:
            pass
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    model_to_use = _core._cli_model_flag(_core._spawn_model_for_engine("claude", model) or "opus")
    # The reservation launches with this exact native Claude name. Claiming is
    # therefore name-sensitive as well as cwd/model-sensitive: a stale process
    # must never surface under the wrong title in Claude's own resume picker or
    # SessionStart hook context.
    session_name = _core._slugify(name or prompt)
    if not session_name:
        session_name = "unnamed"
    entry = None if worktree else _core._take_claude_prewarm_for_request(
        prewarm_id,
        cwd=cwd,
        repo_path=repo_path,
        model=model_to_use,
        name=session_name,
        effort=reasoning_effort,
        auto_compact_k=auto_compact_k,
    )
    if entry is not None:
        ctx = {"cwd": entry["cwd"], "repo_path": entry["repo_path"]}
    else:
        ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-{session_name}-{timestamp}.log"
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    if entry is not None:
        capabilities = {"partial_messages": bool(entry.get("partial_messages"))}
        session_id = entry.get("session_id")
        cmd = list(entry.get("command") or [])
    else:
        claude_bin = _core._resolve_claude_bin()
        if not claude_bin.get("available"):
            return {
                "ok": False,
                "error": claude_bin.get("reason") or "Claude Code CLI not found",
                "code": claude_bin.get("code", "claude_unavailable"),
            }
        capabilities = _core._claude_spawn_capabilities(claude_bin["bin"])
        session_id = str(uuid.uuid4()) if capabilities.get("session_id") else None
        cmd = _core._claude_spawn_command(
            claude_bin["bin"], model_to_use, session_name, session_id, capabilities,
            effort=reasoning_effort,
        )

    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(
                spawn_cwd, session_name,
            )
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}
    if entry is not None:
        old_log_path = entry.get("log")
        try:
            os.replace(old_log_path, log_path)
        except OSError:
            _core._discard_claude_prewarm(entry)
            entry = None
            # Adoption failed before the reserved process became user-visible.
            # Rebuild every identity-bearing cold-spawn value; reusing the
            # reservation's command here would silently launch another hidden
            # `ccc-prewarm` session with the already-consumed UUID.
            claude_bin = _core._resolve_claude_bin()
            if not claude_bin.get("available"):
                return {
                    "ok": False,
                    "error": claude_bin.get("reason") or "Claude Code CLI not found",
                    "code": claude_bin.get("code", "claude_unavailable"),
                }
            capabilities = _core._claude_spawn_capabilities(claude_bin["bin"])
            session_id = str(uuid.uuid4()) if capabilities.get("session_id") else None
            cmd = _core._claude_spawn_command(
                claude_bin["bin"], model_to_use, session_name, session_id, capabilities,
                effort=reasoning_effort,
            )
        else:
            session_id = entry.get("session_id")
            proc = entry["proc"]
            log_fh = entry["log_fh"]
            fifo_path = entry.get("fifo")
            stdin_fd = entry.get("stdin_fd")
            cmd = list(entry.get("command") or cmd)
            prewarm_age_ms = round(
                max(0.0, time.time() - float(entry.get("created_at_epoch") or time.time())) * 1000,
                1,
            )
            entry.update({
                "name": session_name,
                "log": str(log_path),
                "prompt": prompt[:200],
                "started": timestamp,
                "parent_session_id": parent_session_id or "",
                "prewarm_age_ms": prewarm_age_ms,
            })
            _core._spawn_timeline_start(
                session_id,
                t0_epoch_ms=timeline_t0_epoch_ms,
                engine="claude",
                model=model_to_use,
                cwd=spawn_cwd,
                prewarmed=True,
                prewarm_age_ms=prewarm_age_ms,
            )
            _core._spawn_timeline_mark(session_id, "process_started", 0)
            _core._spawn_timeline_mark(session_id, "prewarm_claimed")

    if entry is None:
        log_fh = open(log_path, "w")
        # Run a per-repo env-setup hook in fresh worktrees, before the
        # Claude process starts. No-op when not a worktree spawn. See #47.
        if worktree_path:
            _core._run_worktree_init_hook(worktree_path, ctx["repo_path"], session_name, log_fh)
        fifo_path, child_stdin_fd = _core._make_stdin_fifo(log_path)
        popen_kwargs = dict(
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=spawn_cwd,
            start_new_session=True,
            env=_core._spawn_env(auto_compact_k=auto_compact_k),
        )
        popen_kwargs["stdin"] = child_stdin_fd if child_stdin_fd is not None else subprocess.PIPE
        if session_id:
            _core._spawn_timeline_start(
                session_id,
                t0_epoch_ms=timeline_t0_epoch_ms,
                engine="claude",
                model=model_to_use,
                cwd=spawn_cwd,
                prewarmed=False,
            )
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except (FileNotFoundError, OSError) as e:
            log_fh.close()
            if child_stdin_fd is not None:
                _core._close_fd_quiet(child_stdin_fd)
            if fifo_path:
                _core._unlink_quiet(fifo_path)
            return {
                "ok": False,
                "error": f"Claude Code CLI failed to start: {e}",
                "code": "claude_unavailable",
            }
        if session_id:
            _core._spawn_timeline_mark(session_id, "process_started")
        # Keep a parent-owned writer open before dropping the local RDWR fd.
        # If this open fails, the prompt write below fails closed instead of
        # reporting a live session that never received its initial task.
        stdin_fd = _core._open_fifo_writer(fifo_path) if fifo_path else None
        if child_stdin_fd is not None:
            _core._close_fd_quiet(child_stdin_fd)

        entry = {
            "pid": proc.pid,
            "name": session_name,
            "log": str(log_path),
            "prompt": prompt[:200],
            "started": timestamp,
            "proc": proc,
            "log_fh": log_fh,
            "fifo": fifo_path,
            "stdin_fd": stdin_fd,
            "engine": "claude",
            "cwd": spawn_cwd,
            "repo_path": ctx["repo_path"],
            "model": model_to_use,
            "parent_session_id": parent_session_id or "",
            "session_id": session_id,
            "partial_messages": bool(capabilities.get("partial_messages")),
            "command": list(cmd),
            "prewarmed": False,
            "reasoning_effort": reasoning_effort,
            "auto_compact_k": auto_compact_k,
        }
    # Write the initial prompt as the first stream-json user message.
    # Note: headless `claude -p` doesn't support TUI slash commands like /rename
    # or /color — they're treated as unknown skills. Tab naming/coloring only
    # happens when the user "jumps" into the TUI (see launch_terminal_for_session).
    # Do NOT wait for the prewarm's system/init event before writing here.
    # Measured against Claude Code 2.1.228: system/init is only emitted once
    # Claude starts processing the *first* input message, not once boot/MCP
    # setup finishes. So waiting for it before writing that first message is a
    # deadlock-by-construction — it can never resolve early and previously
    # burned the full 30s timeout on effectively every prewarmed spawn. The
    # FIFO write end is already held open (_open_fifo_writer), so writing
    # immediately is safe: the bytes sit buffered until Claude's own stdin
    # read loop is ready, whether that's before or after SessionStart hooks.
    if entry and entry.get("prewarmed"):
        _core._spawn_timeline_mark(session_id, "prewarm_ready")
    prompt_written = _core._write_stream_json_user_message(entry, prompt, timeout=30)
    if not prompt_written:
        message = "Claude Code started, but CCC could not write the initial prompt to stdin."
        _write_spawn_error_event(log_fh, "spawn_stdin_unavailable", message)
        _core._retire_unresponsive_spawn_entry(entry, terminate=True, reason="write_failed")
        return {
            "ok": False,
            "error": message,
            "code": "spawn_stdin_unavailable",
            "pid": proc.pid,
            "name": session_name,
            "log": str(log_path),
            "engine": "claude",
        }
    if session_id:
        _core._spawn_timeline_mark(session_id, "initial_prompt_written")
    # The override sidecar is what the composer pill and the next resume read,
    # so record the effort this process actually launched with. Blank stays the
    # "preserve whatever is there" sentinel rather than clearing a picked value.
    if model or reasoning_effort:
        model_session_id = session_id or log_filename[:-4]
        effort_arg = reasoning_effort or None
        if entry.get("prewarmed"):
            _core._schedule_session_model_update(
                model_session_id, model or model_to_use, False, effort_arg,
            )
        else:
            _core._set_session_model(model_session_id, model or model_to_use, False, effort_arg)

    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=fifo_path,
        engine="claude",
        session_id=session_id,
        repo_path=ctx["repo_path"],
        model=model_to_use,
        parent_session_id=parent_session_id,
        reasoning_effort=reasoning_effort,
        auto_compact_k=auto_compact_k,
        input_result_target=entry.get("input_result_target"),
        input_accepted_at=entry.get("input_accepted_at"),
        input_command_uuids=entry.get("input_command_uuids"),
    )
    # Cwd determines the ~/.claude/projects/ bucket the new session
    # logs to, which is how the kanban groups it by repo. Print it so
    # mis-routed sessions are debuggable from the server log.
    print(f"  [spawn] PID {proc.pid} ({session_name}) in cwd {spawn_cwd}")

    if session_id:
        _core._schedule_claude_desktop_visibility_retry(session_id, spawn_entry=entry)
        if entry.get("prewarmed") and session_name:
            _core._save_session_name_override(session_id, session_name)

    resp = {
        "ok": True,
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "engine": "claude",
        "initial_prompt_written": True,
        "prewarmed": bool(entry.get("prewarmed")),
    }
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    if session_id:
        _core._spawn_timeline_mark(session_id, "spawn_response_sent")
        _core._spawn_timeline_save()
    return _finalize_spawn_response(
        resp,
        entry,
        ctx,
        wait_for_session_id=not bool(session_id),
    )


def spawn_session_codex(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, reasoning_effort="", parent_session_id=None):
    """Spawn a headless Codex run and return tracking info.

    Prefer the Codex app-server for fresh durable threads when available. If
    that is disabled or unavailable before a thread is created, fall back to the
    legacy Codex CLI `exec` path. `codex exec` is one-shot: the prompt comes
    from argv and the process exits when the model is done, so it uses
    `subprocess.DEVNULL` for stdin (no FIFO, no mid-run inject support).

    Tested against codex-cli 0.125.0-alpha.3.

    The spawned subprocess requires an explicit cwd or repo_path. Codex `--cd`
    is set so the agent's workspace root matches that concrete directory.

    If `worktree=True`, create a fresh git worktree off the launch cwd on a
    `feat/<slug>` branch (same shape as the Claude path) and run codex there.

    Returns the same shape as spawn_session:
      {ok: True,  pid, name, log}                       — success
      {ok: False, error}                                — resolver failed
    """
    routed = _core._control_plane_engine_call(
        "codex", "spawn", {
            "prompt": prompt,
            "name": name,
            "cwd": cwd,
            "repo_path": repo_path,
            "worktree": bool(worktree),
            "model": model,
            "reasoning_effort": reasoning_effort,
            "parent_session_id": parent_session_id,
        },
        idempotency_key=_core._take_control_plane_action_id(),
    )
    if routed is not None:
        return routed
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    image_paths = _core._extract_pasted_image_paths(prompt)
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]

    session_name = _core._slugify(name or prompt)
    if not session_name:
        session_name = "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-codex-{session_name}-{timestamp}.log"
    model_to_use = _core._spawn_model_for_engine("codex", model) or _core._spawn_fallback_model_for_engine("codex")
    if model_to_use:
        _core._set_session_model(log_filename[:-4], model_to_use, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(
                spawn_cwd, session_name,
            )
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}

    app_server_spawn = _core._codex_spawn_via_app_server(
        prompt,
        session_name=session_name,
        spawn_cwd=spawn_cwd,
        repo_for_logs=repo_for_logs,
        model_to_use=model_to_use,
        reasoning_effort=reasoning_effort,
        image_paths=image_paths,
        parent_session_id=parent_session_id,
        timestamp=timestamp,
        worktree_path=worktree_path,
        worktree_branch=worktree_branch,
        parent_repo=ctx["repo_path"],
    )
    if app_server_spawn is not None:
        return app_server_spawn

    exec_total_start = time.monotonic()
    resolved = _core._resolve_codex_bin()
    if not resolved["available"]:
        _core._codex_telemetry_append(
            "codex_spawn",
            ok=False,
            via="codex-spawn",
            stage="resolve",
            error=resolved["reason"],
            code=resolved.get("code"),
            total_ms=_core._codex_elapsed_ms(exec_total_start),
            cwd=spawn_cwd,
            model=model_to_use,
        )
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    bin_path = resolved["bin"]

    cmd = [
        bin_path, *_core._codex_context_window_args(), "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model", model_to_use,
        "--cd", spawn_cwd,
    ]
    if reasoning_effort:
        cmd.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])
    for image_path in image_paths:
        cmd.extend(["--image", image_path])
    cmd.extend(["--", prompt])

    log_fh = open(log_path, "w")
    if worktree_path:
        _core._run_worktree_init_hook(worktree_path, ctx["repo_path"], session_name, log_fh)
    try:
        launch_start = time.monotonic()
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=spawn_cwd,
            start_new_session=True,
        )
        launch_ms = _core._codex_elapsed_ms(launch_start)
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        _core._codex_telemetry_append(
            "codex_spawn",
            ok=False,
            via="codex-spawn",
            stage="popen",
            error=str(e),
            code="codex_launch_failed",
            total_ms=_core._codex_elapsed_ms(exec_total_start),
            cwd=spawn_cwd,
            model=model_to_use,
        )
        return {"ok": False, "error": str(e), "code": "codex_launch_failed", "via": "codex-spawn"}
    failure = _spawn_early_failure_payload(
        proc, log_path, log_fh, engine="codex", via="codex-spawn",
    )
    if failure:
        _core._codex_telemetry_append(
            "codex_spawn",
            ok=False,
            via="codex-spawn",
            stage="early-failure",
            error=failure.get("error"),
            code=failure.get("code"),
            launch_ms=launch_ms,
            total_ms=_core._codex_elapsed_ms(exec_total_start),
            pid=proc.pid,
            cwd=spawn_cwd,
            model=model_to_use,
        )
        return failure

    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "fifo": None,         # Codex exec is one-shot; no inject FIFO.
        "stdin_fd": None,
        "engine": "codex",
        "cwd": spawn_cwd,
        "repo_path": repo_for_logs,
        "model": model_to_use or "",
        "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=None,
        engine="codex",
        repo_path=repo_for_logs,
        model=model_to_use,
        parent_session_id=parent_session_id,
        reasoning_effort=reasoning_effort,
    )

    resp = {
        "ok": True,
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "via": "codex-spawn",
    }
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    _core._codex_telemetry_append(
        "codex_spawn",
        ok=True,
        via="codex-spawn",
        launch_ms=launch_ms,
        total_ms=_core._codex_elapsed_ms(exec_total_start),
        pid=proc.pid,
        cwd=spawn_cwd,
        model=model_to_use,
    )
    return _finalize_spawn_response(resp, entry, ctx)


def spawn_session_kilo(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None):
    """Spawn a headless Kilo Code CLI run and return tracking info."""
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    resolved = _core._resolve_kilo_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-kilo-{session_name}-{timestamp}.log"
    model_to_use = _core._spawn_model_for_engine("kilo", model) or os.environ.get("CCC_KILO_MODEL", "kilo/stepfun/step-3.7-flash:free")
    if model_to_use:
        _core._set_session_model(log_filename[:-4], model_to_use, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(spawn_cwd, session_name)
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}
    cmd = [resolved["bin"], "run", "--auto"]
    if model_to_use:
        cmd.extend(["--model", model_to_use])
    cmd.extend([prompt])
    log_fh = open(log_path, "w")
    if worktree_path:
        _core._run_worktree_init_hook(worktree_path, ctx["repo_path"], session_name, log_fh)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=spawn_cwd, start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "code": "kilo_launch_failed", "via": "kilo-spawn"}
    failure = _spawn_early_failure_payload(proc, log_path, log_fh, engine="kilo", via="kilo-spawn")
    if failure:
        return failure
    entry = {
        "pid": proc.pid, "name": session_name, "log": str(log_path),
        "prompt": prompt[:200], "started": timestamp, "proc": proc,
        "log_fh": log_fh, "fifo": None, "stdin_fd": None,
        "engine": "kilo", "cwd": spawn_cwd, "repo_path": repo_for_logs,
        "model": model_to_use or "", "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid, name=session_name, log_path=log_path, cwd=spawn_cwd,
        spawned_at=timestamp, command_summary=prompt[:200],
        fifo=None, engine="kilo", repo_path=repo_for_logs, model=model_to_use,
        parent_session_id=parent_session_id,
    )
    resp = {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path), "via": "kilo-spawn"}
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    return _finalize_spawn_response(resp, entry, ctx)


def spawn_session_opencode(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None):
    """Spawn a headless OpenCode CLI run and return tracking info."""
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    resolved = _core._resolve_opencode_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-opencode-{session_name}-{timestamp}.log"
    model_to_use = _core._spawn_model_for_engine("opencode", model) or os.environ.get("CCC_OPENCODE_MODEL", "openrouter/anthropic/claude-sonnet-4.5")
    if model_to_use:
        _core._set_session_model(log_filename[:-4], model_to_use, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(spawn_cwd, session_name)
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}
    cmd = [resolved["bin"], "run", "--auto"]
    if model_to_use:
        cmd.extend(["--model", model_to_use])
    cmd.extend([prompt])
    log_fh = open(log_path, "w")
    if worktree_path:
        _core._run_worktree_init_hook(worktree_path, ctx["repo_path"], session_name, log_fh)
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=spawn_cwd, start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "code": "opencode_launch_failed", "via": "opencode-spawn"}
    failure = _spawn_early_failure_payload(proc, log_path, log_fh, engine="opencode", via="opencode-spawn")
    if failure:
        return failure
    entry = {
        "pid": proc.pid, "name": session_name, "log": str(log_path),
        "prompt": prompt[:200], "started": timestamp, "proc": proc,
        "log_fh": log_fh, "fifo": None, "stdin_fd": None,
        "engine": "opencode", "cwd": spawn_cwd, "repo_path": repo_for_logs,
        "model": model_to_use or "", "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid, name=session_name, log_path=log_path, cwd=spawn_cwd,
        spawned_at=timestamp, command_summary=prompt[:200],
        fifo=None, engine="opencode", repo_path=repo_for_logs, model=model_to_use,
        parent_session_id=parent_session_id,
    )
    resp = {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path), "via": "opencode-spawn"}
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    return _finalize_spawn_response(resp, entry, ctx)


def spawn_session_kimi(
    prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None,
    parent_session_id=None, effort=None,
):
    """Spawn a Kimi session via the ACP harness (session/new + first prompt).

    Unlike the CLI-spawning engines there is no per-session process or log
    file: the session lives inside CCC's shared ``kimi acp`` subprocess and
    streams over ACP notifications. The registry entry carries the ACP
    sessionId up front; pid stays None (nothing to reattach after a CCC
    restart — the session is rediscovered from the ACP state file instead).
    """
    routed = _core._control_plane_engine_call(
        "kimi", "spawn", {
            "prompt": prompt,
            "name": name,
            "cwd": cwd,
            "repo_path": repo_path,
            "worktree": bool(worktree),
            "model": model,
            "parent_session_id": parent_session_id,
            "effort": effort,
        },
        idempotency_key=_core._take_control_plane_action_id(),
    )
    if routed is not None:
        return routed
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    resolved = _core._resolve_kimi_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(spawn_cwd, session_name)
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}
    model_to_use = _core._spawn_model_for_engine("kimi", model)
    # CCC-spawned sessions run unattended like every other engine's headless
    # spawn — "default" (manual approvals) would park the session on the first
    # tool call. "auto" auto-approves safe operations; CCC_KIMI_SPAWN_MODE=yolo
    # for full bypass, "default" to opt back into manual approvals.
    spawn_mode = (os.environ.get("CCC_KIMI_SPAWN_MODE") or "auto").strip() or "auto"
    result = _core._acp_session_new("kimi", spawn_cwd, prompt=prompt or None, model=model_to_use or None, mode=spawn_mode, effort=effort or None)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "kimi ACP session/new failed",
            "code": result.get("code") or "kimi_spawn_failed",
            "via": "kimi-acp",
        }
    sid = result["session_id"]
    entry = {
        "pid": None, "name": session_name, "log": None,
        "prompt": prompt[:200], "started": timestamp, "proc": None,
        "log_fh": None, "fifo": None, "stdin_fd": None,
        "engine": "kimi", "session_id": sid, "cwd": spawn_cwd,
        "repo_path": repo_for_logs, "model": model_to_use or "",
        "reasoning_effort": effort or "",
        "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=None, name=session_name, log_path=_core._acp_transcript_path("kimi", sid),
        cwd=spawn_cwd, spawned_at=timestamp, command_summary=prompt[:200],
        fifo=None, engine="kimi", session_id=sid, model=model_to_use,
        repo_path=repo_for_logs, parent_session_id=parent_session_id,
        reasoning_effort=effort or "",
    )
    resp = {
        "ok": True, "pid": None, "session_id": sid, "name": session_name,
        "spawn_id": sid, "via": "kimi-acp",
    }
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    return resp


def spawn_session_grok(
    prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None,
    parent_session_id=None, effort=None,
):
    """Spawn a Grok session via the ACP harness (session/new + first prompt).

    Same shape as spawn_session_kimi: no per-session process. The session
    lives in CCC's shared ``grok agent stdio`` subprocess (worker-owned).
    """
    routed = _core._control_plane_engine_call(
        "grok", "spawn", {
            "prompt": prompt,
            "name": name,
            "cwd": cwd,
            "repo_path": repo_path,
            "worktree": bool(worktree),
            "model": model,
            "parent_session_id": parent_session_id,
            "effort": effort,
        },
        idempotency_key=_core._take_control_plane_action_id(),
    )
    if routed is not None:
        return routed
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    resolved = _core._resolve_grok_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    worktree_path = None
    worktree_branch = None
    if worktree:
        try:
            worktree_path, worktree_branch = _core._create_worktree_for_spawn(spawn_cwd, session_name)
            spawn_cwd = worktree_path
        except RuntimeError as e:
            return {"ok": False, "error": f"worktree creation failed: {e}"}
    model_to_use = _core._spawn_model_for_engine("grok", model)
    spawn_mode = (os.environ.get("CCC_GROK_SPAWN_MODE") or "yolo").strip() or "yolo"
    result = _core._acp_session_new(
        "grok", spawn_cwd, prompt=prompt or None,
        model=model_to_use or None, mode=spawn_mode, effort=effort or None,
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "grok ACP session/new failed",
            "code": result.get("code") or "grok_spawn_failed",
            "via": "grok-acp",
        }
    sid = result["session_id"]
    entry = {
        "pid": None, "name": session_name, "log": None,
        "prompt": prompt[:200], "started": timestamp, "proc": None,
        "log_fh": None, "fifo": None, "stdin_fd": None,
        "engine": "grok", "session_id": sid, "cwd": spawn_cwd,
        "repo_path": repo_for_logs, "model": model_to_use or "",
        "reasoning_effort": effort or "",
        "parent_session_id": parent_session_id or "",
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=None, name=session_name, log_path=_core._acp_transcript_path("grok", sid),
        cwd=spawn_cwd, spawned_at=timestamp, command_summary=prompt[:200],
        fifo=None, engine="grok", session_id=sid, model=model_to_use,
        repo_path=repo_for_logs, parent_session_id=parent_session_id,
        reasoning_effort=effort or "",
    )
    resp = {
        "ok": True, "pid": None, "session_id": sid, "name": session_name,
        "spawn_id": sid, "via": "grok-acp",
    }
    if worktree_path:
        resp["worktree_path"] = worktree_path
        resp["worktree_branch"] = worktree_branch
    return resp


def spawn_session_hermes(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None):
    """Spawn a headless Hermes CLI run and return tracking info."""
    if os.environ.get("CCC_SSH_HOST"):
        try:
            import ssh_multiplexer
            if ssh_multiplexer.get_global_multiplexer():
                return spawn_session_remote(
                    prompt, name=name, cwd=cwd, repo_path=repo_path, worktree=worktree,
                    model=model, parent_session_id=parent_session_id, engine="hermes"
                )
        except Exception:
            pass
    prompt = _core._strip_ccc_session_state_instruction(prompt)
    resolved = _core._resolve_hermes_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt) or "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-hermes-{session_name}-{timestamp}.log"
    model_to_use = model or _core._spawn_model_for_engine("hermes") or "auto"
    if model_to_use:
        _core._set_session_model(log_filename[:-4], model_to_use, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    # Hermes session UUID
    session_id = str(uuid.uuid4())

    cmd = [
        resolved["bin"],
        "chat",
        "--query", prompt,
        "--quiet",
    ]
    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=spawn_cwd, start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        return {"ok": False, "error": str(e), "code": "hermes_launch_failed", "via": "hermes-spawn"}
    failure = _spawn_early_failure_payload(proc, log_path, log_fh, engine="hermes", via="hermes-spawn")
    if failure:
        return failure
    entry = {
        "pid": proc.pid, "name": session_name, "log": str(log_path),
        "prompt": prompt[:200], "started": timestamp, "proc": proc,
        "log_fh": log_fh, "fifo": None, "stdin_fd": None,
        "engine": "hermes", "cwd": spawn_cwd, "repo_path": repo_for_logs,
        "model": model_to_use or "", "parent_session_id": parent_session_id or "",
        "session_id": session_id,
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid, name=session_name, log_path=log_path, cwd=spawn_cwd,
        spawned_at=timestamp, command_summary=prompt[:200],
        fifo=None, engine="hermes", repo_path=repo_for_logs, model=model_to_use,
        parent_session_id=parent_session_id, session_id=session_id,
    )
    resp = {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path), "via": "hermes-spawn", "session_id": session_id}
    return _finalize_spawn_response(resp, entry, ctx)


def _find_remote_sessions(repo_path=None, progress=None, limit=None):
    try:
        import ssh_multiplexer
        mux = ssh_multiplexer.get_global_multiplexer()
        if not mux:
            return []
        if repo_path:
            repo_path = _core.resolve_repo_path(repo_path)
        return ssh_multiplexer.find_remote_sessions(mux, repo_path=repo_path, limit=limit)
    except Exception as exc:
        if progress:
            progress("remote", state="error", detail=f"Remote session scan failed: {exc}")
        return []


def spawn_session_remote(prompt, name=None, cwd=None, repo_path=None, worktree=False, model=None, parent_session_id=None, auto_compact_k=None, engine="claude"):
    """Spawn a remote CLI session over SSH using OpenSSH ControlMaster multiplexing."""
    try:
        import ssh_multiplexer
    except ImportError:
        return {"ok": False, "error": "ssh_multiplexer module not found", "code": "ssh_unavailable"}
    mux = ssh_multiplexer.get_global_multiplexer()
    if not mux:
        return {"ok": False, "error": "CCC_SSH_HOST is not configured", "code": "ssh_unconfigured"}

    prompt = _core._strip_ccc_session_state_instruction(prompt)
    ctx = _core._spawn_repo_context(cwd=cwd, repo_path=repo_path)
    spawn_cwd = ctx["cwd"]
    repo_for_logs = ctx["repo_path"]
    session_name = _core._slugify(name or prompt)
    if not session_name:
        session_name = "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-remote-{session_name}-{timestamp}.log"
    if model:
        _core._set_session_model(log_filename[:-4], model, False)
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    session_id = str(uuid.uuid4())
    model_to_use = _core._cli_model_flag(_core._spawn_model_for_engine(engine or "claude", model) or "opus")

    if engine == "hermes":
        remote_cmd = f"cd {shlex.quote(spawn_cwd)} && exec hermes chat --query {shlex.quote(prompt)} --quiet"
    else:
        args = [
            "claude", "-p", "--verbose",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--model", model_to_use,
            "--dangerously-skip-permissions",
            "--name", session_name,
        ]
        args.extend(_core._claude_session_state_args())
        remote_cmd = f"cd {shlex.quote(spawn_cwd)} && exec " + " ".join(shlex.quote(a) for a in args)

    log_fh = open(log_path, "w")
    fifo_path, child_stdin_fd = _core._make_stdin_fifo(log_path)
    popen_kwargs = dict(
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=_core._spawn_env(auto_compact_k=auto_compact_k),
    )
    popen_kwargs["stdin"] = child_stdin_fd if child_stdin_fd is not None else subprocess.PIPE
    try:
        proc = mux.popen(remote_cmd, **popen_kwargs)
    except Exception as e:
        log_fh.close()
        if child_stdin_fd is not None:
            _core._close_fd_quiet(child_stdin_fd)
        if fifo_path:
            _core._unlink_quiet(fifo_path)
        return {"ok": False, "error": f"Remote SSH launch failed: {e}", "code": "ssh_launch_failed"}

    stdin_fd = _core._open_fifo_writer(fifo_path) if fifo_path else None
    if child_stdin_fd is not None:
        _core._close_fd_quiet(child_stdin_fd)

    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "fifo": fifo_path,
        "stdin_fd": stdin_fd,
        "engine": f"remote-{engine}",
        "cwd": spawn_cwd,
        "repo_path": ctx["repo_path"],
        "model": model_to_use,
        "parent_session_id": parent_session_id or "",
        "session_id": session_id,
        "is_remote": True,
    }
    if engine != "hermes":
        prompt_written = _core._write_stream_json_user_message(entry, prompt, timeout=30)
        if not prompt_written:
            message = "Remote Claude Code started over SSH, but CCC could not write initial prompt."
            _write_spawn_error_event(log_fh, "spawn_stdin_unavailable", message)
            _core._retire_unresponsive_spawn_entry(entry, terminate=True, reason="write_failed")
            return {
                "ok": False,
                "error": message,
                "code": "spawn_stdin_unavailable",
                "pid": proc.pid,
                "name": session_name,
                "log": str(log_path),
                "engine": f"remote-{engine}",
            }

    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=fifo_path,
        engine=f"remote-{engine}",
        repo_path=ctx["repo_path"],
        model=model_to_use,
        session_id=session_id,
        parent_session_id=parent_session_id,
        input_result_target=entry.get("input_result_target"),
        input_accepted_at=entry.get("input_accepted_at"),
        input_command_uuids=entry.get("input_command_uuids"),
    )
    resp = {
        "ok": True,
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "via": f"remote-{engine}-spawn",
        "session_id": session_id,
        "engine": f"remote-{engine}",
        "is_remote": True,
    }
    return _finalize_spawn_response(resp, entry, ctx, wait_for_session_id=False)


_COLOR_PALETTE = [
    "red", "orange", "yellow", "green", "cyan", "blue", "purple", "magenta", "pink",
]


def _pick_color_for_session(name):
    """Deterministic color from a session name so the same session always gets the same color."""
    if not name:
        return "blue"
    h = 0
    for ch in name:
        h = (h * 131 + ord(ch)) & 0xFFFF
    return _COLOR_PALETTE[h % len(_COLOR_PALETTE)]


def _make_stdin_fifo(log_path):
    """Create a named pipe for the spawn log and open it RDWR.

    The RDWR open is the trick that makes headless agents survive a
    CCC restart: when we pass this fd to the child as its stdin (Popen
    dup2's fd → fd 0), the kernel sees the child as a *writer* of its
    own stdin too (the dup'd fd inherits RDWR mode). So even when every
    external writer closes — e.g. CCC dies — the kernel's FIFO writer
    count stays ≥ 1 as long as the child is alive, which means no EOF,
    which means no premature exit.

    The FIFO lives under COMMAND_CENTER_STATE_DIR, not next to the log
    file in the target repo's .claude/logs/. A FIFO inside a project
    tree hangs any tool that walks the tree and opens every file it
    finds (e.g. `next build --turbopack`, `rg` without excludes) —
    open() on a FIFO blocks until a peer opens the other end, which
    never happens (OPS-726). The log file itself stays in the repo
    since callers/users tail it there; only the transient pipe moves.

    Returns (fifo_path, rdwr_fd), or (None, None) on failure (e.g. a
    filesystem that doesn't support FIFOs). Callers should fall back
    to subprocess.PIPE in that case — same behavior as before this
    feature shipped.
    """
    try:
        log_path = Path(log_path)
        fifo_dir = _core.COMMAND_CENTER_STATE_DIR / "fifos"
        fifo_dir.mkdir(parents=True, exist_ok=True)
        fifo_path = fifo_dir / (log_path.name + ".stdin")
        # mkfifo refuses if the path already exists; clear any stale
        # leftover from a previous spawn that didn't get cleaned up.
        if fifo_path.exists():
            try:
                fifo_path.unlink()
            except OSError:
                pass
        os.mkfifo(str(fifo_path), 0o600)
        # O_RDWR works for FIFOs on both Linux and macOS and never blocks.
        # O_RDONLY/O_WRONLY would wait for the other side to appear, which
        # would deadlock the spawn flow.
        fd = os.open(str(fifo_path), os.O_RDWR | os.O_CLOEXEC)
        return str(fifo_path), fd
    except OSError as e:
        print(f"  [spawn-fifo] mkfifo failed for {log_path} ({e}); falling back to PIPE")
        return None, None


def _open_fifo_writer(fifo_path):
    """Open a FIFO write-only without blocking. Returns fd, or None."""
    if not fifo_path:
        return None
    try:
        return os.open(fifo_path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None


def _close_fd_quiet(fd):
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _unlink_quiet(path):
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _cleanup_finished_entry(entry):
    """Close the FIFO writer fd and unlink the FIFO when a session ends.

    Idempotent: zeroes out the fd/path keys so a second call is a no-op.
    The on-disk log itself is preserved for forensics — only the
    transient FIFO node goes away.
    """
    fd = entry.get("stdin_fd")
    if fd is not None:
        _core._close_fd_quiet(fd)
        entry["stdin_fd"] = None
    fifo = entry.get("fifo")
    if fifo:
        _core._unlink_quiet(fifo)
        entry["fifo"] = None
    log_fh = entry.get("log_fh")
    if log_fh is not None:
        try:
            log_fh.close()
        except OSError:
            pass
        entry["log_fh"] = None


def _retire_unresponsive_spawn_entry(entry, *, terminate=False, reason=None, caller=None):
    """Stop tracking a CCC-owned spawn whose stdin can no longer accept input."""
    if not isinstance(entry, dict):
        return
    pid = entry.get("pid")
    # Diagnostic: record whether the child was still alive at retire time so we
    # can distinguish "we killed a live process" from "cleaned up a dead one".
    alive = None
    _proc = entry.get("proc")
    try:
        if _proc is not None:
            alive = _proc.poll() is None
    except Exception:
        alive = None
    # Compute spawn age at kill time for diagnostics.
    started_epoch = _spawn_entry_started_epoch(entry)
    age_s = round(time.time() - started_epoch, 1) if started_epoch else None
    # If we're killing a live process mid-turn, Claude will write
    # "[Request interrupted by user]" to the transcript. Flag it so the
    # activity log can distinguish "CCC killed a working session" from
    # "cleaned up an idle/dead one".
    mid_turn = alive and bool(_core._spawn_entry_active_tool_child(entry))
    _core._resume_ledger_append(
        "retire", sid=entry.get("resumed_sid"), pid=pid,
        terminate=bool(terminate), reason=reason or "unspecified", alive=alive,
        caller=caller or "", age_s=age_s, mid_turn=mid_turn,
    )
    _core._log_activity(
        "retire", "RETIRE",
        f"pid={pid} sid={str(entry.get('resumed_sid') or '')[:8]} "
        f"reason={reason or 'unspecified'} caller={caller or '-'} "
        f"alive={alive} age={age_s}s mid_turn={mid_turn}",
    )
    # Surface the kill to the UI via the ring buffer (polled by live-activity).
    _core._record_kill_event(
        entry, reason=reason or "unspecified", caller=caller or "",
        alive=alive, age_s=age_s,
    )
    if terminate and pid is not None:
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except (PermissionError, OSError, ValueError):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError, ValueError):
                pass
    _cleanup_finished_entry(entry)
    if pid is not None:
        _core._remove_spawn_from_registry(pid)
    try:
        _core._spawned_sessions.remove(entry)
    except ValueError:
        pass


def _poll_spawn_entry(entry):
    """Poll a tracked spawned child and clean transient handles once it exits."""
    if isinstance(entry, dict) and entry.get("app_server_spawn"):
        state = _core._codex_app_server_thread_state(entry.get("session_id"))
        turn_id = entry.get("turn_id")
        completed_turn = state.get("last_completed_turn_id")
        if turn_id and completed_turn == turn_id:
            poll = 0
        elif completed_turn:
            poll = 0
        elif state.get("active_turn_id") or str(state.get("status") or "").lower() == "active":
            poll = None
        else:
            poll = None
    else:
        proc = entry.get("proc") if isinstance(entry, dict) else None
        try:
            poll = proc.poll() if proc is not None else -1
        except Exception:
            poll = -1
    # Devin's CLI wrapper can exit while the real agent child (recorded in
    # session_locks/<id>.lock) is still running. Treat that as live so the
    # spawn placeholder can swap onto the durable row.
    if (
        poll is not None
        and isinstance(entry, dict)
        and str(entry.get("engine") or "").lower() == "devin"
    ):
        sid = entry.get("session_id") or entry.get("resumed_sid")
        if not sid:
            try:
                sid = _core._devin_cli_session_id_for_spawn_entry(entry)
            except Exception:
                sid = None
            if sid:
                entry["session_id"] = sid
                try:
                    _core._update_spawn_session_id_in_registry(entry.get("pid"), sid)
                except Exception:
                    pass
        if sid and _core._devin_cli_session_live(_core._devin_cli_raw_id(sid)):
            return None
    if poll is not None and isinstance(entry, dict) and not entry.get("_cleanup_done"):
        # Diagnostic: a tracked child just EXITED. Log lifetime + cache/cost
        # from the resume log's final result event before we drop its handles.
        # Guarded by `_cleanup_done` so this fires exactly once per child.
        try:
            started_epoch = _core._resume_entry_started_epoch(entry)
            lifetime = round(time.time() - started_epoch, 1) if started_epoch else None
            usage = _core._resume_parse_result_usage(entry.get("log")) if entry.get("log") else {}
            _core._resume_ledger_append(
                "exit", sid=entry.get("resumed_sid"), pid=entry.get("pid"),
                exit_code=poll, lifetime_s=lifetime, **usage,
            )
        except Exception:
            pass
        _cleanup_finished_entry(entry)
        pid = entry.get("pid")
        if pid is not None:
            _core._remove_spawn_from_registry(pid)
        entry["_cleanup_done"] = True
    return poll


def _set_fd_nonblocking(fd):
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if not (flags & os.O_NONBLOCK):
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        return True
    except (OSError, ValueError):
        return False


def _write_fd_nonblocking(fd, line_bytes, timeout=0.25):
    """Write a complete stream-json line without hanging the request thread."""
    if fd is None or not _set_fd_nonblocking(fd):
        return False
    total = 0
    deadline = time.monotonic() + timeout
    while total < len(line_bytes):
        try:
            written = os.write(fd, line_bytes[total:])
        except BlockingIOError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                _r, writable, _x = select.select([], [fd], [], min(0.05, remaining))
            except (OSError, ValueError):
                return False
            if not writable:
                continue
            continue
        except InterruptedError:
            continue
        except (BrokenPipeError, OSError, ValueError):
            return False
        if written <= 0:
            return False
        total += written
    return True


def _write_via_pipe(proc, line_bytes):
    if proc is None or getattr(proc, "stdin", None) is None:
        return False
    try:
        fd = proc.stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return False
    return _write_fd_nonblocking(fd, line_bytes)


def _write_via_spawn_fd(target, line, timeout=0.25):
    fd = target.get("stdin_fd")
    if fd is not None:
        if _write_fd_nonblocking(fd, line, timeout=timeout):
            return True
        # Cached fd is either broken or not accepting input. Drop it so
        # the next attempt reopens the FIFO instead of wedging on the old fd.
        _core._close_fd_quiet(fd)
        target["stdin_fd"] = None

    fifo = target.get("fifo")
    if fifo:
        new_fd = _core._open_fifo_writer(fifo)
        if new_fd is not None:
            if _write_fd_nonblocking(new_fd, line, timeout=timeout):
                target["stdin_fd"] = new_fd
                return True
            _core._close_fd_quiet(new_fd)
    return False


def _write_stream_json_user_message(target, text, timeout=0.25):
    """Emit a stream-json user message to a running headless claude.

    `target` can be:
      - A dict (spawn entry) — preferred. We write to the FIFO writer
        fd cached on the entry, reopening from `entry["fifo"]` if it
        was lost (e.g. across a CCC restart). This path is the whole
        reason FIFOs exist: it survives the orchestrator dying.
      - A subprocess.Popen — legacy fallback for spawns that didn't
        get a FIFO (mkfifo failure → subprocess.PIPE).
    """
    text = _core._strip_lone_surrogates(str(text or ""))
    if not text:
        return False
    command_uuid = str(uuid.uuid4())
    msg = {
        "type": "user",
        # Claude echoes this as command_lifecycle.command_uuid and as the
        # result's user_message_uuid. That gives us a causal acknowledgement
        # for this exact input; result counts sampled around a FIFO write do
        # not, because the preceding turn can finish during the write.
        "uuid": command_uuid,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }
    line = (json.dumps(msg) + "\n").encode("utf-8")

    if isinstance(target, dict):
        # Serialize physical delivery and boundary accounting so simultaneous
        # composer submits cannot race the same spawn state. Multiple messages
        # accepted during one active response intentionally share a boundary.
        # The lock is process-local and omitted from the JSON spawn registry.
        write_lock = target.setdefault("_input_write_lock", threading.Lock())
        with write_lock:
            pending_commands = _pending_input_command_uuids(target)
            delivered = _core._write_via_spawn_fd(target, line, timeout=timeout)
            if not delivered:
                delivered = _core._write_via_pipe(target.get("proc"), line)
            if not delivered:
                return False

            accepted_at = time.time()
            target.pop("input_result_target", None)
            target["input_command_uuids"] = pending_commands + [command_uuid]
            target["input_accepted_at"] = accepted_at
            _core._update_spawn_input_state_in_registry(
                target.get("pid"), None, accepted_at,
                target["input_command_uuids"],
            )
            return True

    return _core._write_via_pipe(target, line)


def _write_fifo_line_once(fifo_path, text, timeout=0.25):
    """One-shot stream-json user-line write to a FIFO CCC does not own
    long-term (a WatchTower worker's FIFO). Always closes the fd it opens.

    Deliberately does NOT go through _write_via_spawn_fd: that helper caches
    the opened fd on its `target` dict for reuse across calls, which is
    correct for a CCC-owned spawn entry that lives as long as the process, but
    would leak one descriptor per call against a throwaway dict here -- there
    is no long-lived entry to cache it on."""
    text = _core._strip_lone_surrogates(str(text or ""))
    if not fifo_path or not text:
        return False
    line = (json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }) + "\n").encode("utf-8")
    fd = _core._open_fifo_writer(fifo_path)
    if fd is None:
        return False
    try:
        return _write_fd_nonblocking(fd, line, timeout=timeout)
    finally:
        _core._close_fd_quiet(fd)


def _write_stream_json_interrupt(target, timeout=0.25):
    """Emit a stream-json `interrupt` control request to a running headless.

    Claude's stream-json *input* accepts control requests alongside user
    messages; `interrupt` aborts the in-flight tool and ends the turn
    (verified against claude 2.1.216: the running Bash comes back as
    "The user doesn't want to proceed with this tool use" and the turn
    closes with `terminal_reason: aborted_tools`). That abort is the whole
    point — a wedged turn never reaches a boundary on its own, so queued
    input can never land. Interrupting manufactures the boundary.

    Same FIFO and same cached writer fd as `_write_stream_json_user_message`,
    so this works across a CCC restart for any spawn that still has its FIFO.
    """
    msg = {
        "type": "control_request",
        "request_id": str(uuid.uuid4()),
        "request": {"subtype": "interrupt"},
        "uuid": str(uuid.uuid4()),
    }
    line = (json.dumps(msg) + "\n").encode("utf-8")

    if isinstance(target, dict):
        if _core._write_via_spawn_fd(target, line, timeout=timeout):
            return True
        return _core._write_via_pipe(target.get("proc"), line)

    return _core._write_via_pipe(target, line)


def inject_into_spawned(pid, text):
    """Send a follow-up user message to a previously spawned session."""
    text = _core._strip_ccc_session_state_instruction(text)
    if not text:
        return {"ok": False, "error": "missing text"}
    for s in _core._spawned_sessions:
        if s["pid"] == pid:
            if _core._poll_spawn_entry(s) is not None:
                return {"ok": False, "error": "process exited"}
            ok = _core._write_stream_json_user_message(s, text)
            return {"ok": ok, "pid": pid}
    return {"ok": False, "error": "unknown pid (not spawned by this server)"}


def _find_live_spawn_entry_for_session(session_id):
    """Return a live `_spawned_sessions` entry whose log mentions `session_id`,
    or None. Matches both fresh spawns (where the spawn's own session_id is
    in the log header) and resume subprocesses (where the resumed sid plus
    the resume's new sid both appear).
    """
    if not session_id:
        return None
    for s in _core._spawned_sessions:
        try:
            if _core._poll_spawn_entry(s) is not None:
                continue
        except Exception:
            continue
        if s.get("resumed_sid") == session_id:
            return s
        log = s.get("log")
        if s.get("engine") == "codex" and _core._extract_codex_thread_id_from_log(log) == session_id:
            return s
        if s.get("engine") == "gemini" and _core._extract_gemini_session_id_from_log(log) == session_id:
            return s
        if log and session_id in _core._log_session_ids(log):
            return s
    return None


# ── Headless staleness detection (GH #71) ─────────────────────────────────────
#
# A live `claude` process runs off in-memory state loaded at resume and NEVER
# re-reads the transcript from disk. So a CCC-spawned persistent headless goes
# stale the moment a *different* writer (a human terminal, or any other
# `claude --resume`) appends a turn to the shared append-only `.jsonl`. If CCC
# then keeps writing to that stale headless, it answers from frozen memory —
# an effective rollback (GH #71 / scratch C3).
#
# Staleness signal — why (size_bytes, last_event_uuid) of the transcript tail,
# attributed via the headless's own stdout result-count:
# I verified empirically (2026-06-07, claude 2.1.168) that the on-disk events
# carry NO field that identifies the writing process. A separate
# `claude --resume <sid> -p` writes the SAME `sessionId` and the SAME
# `entrypoint` ("sdk-cli") as our own headless — identical on disk. The
# headless's own stdout-log event uuids also do NOT match the transcript uuids,
# so cross-referencing the log uuids is unreliable too. The robust signal is:
#   1. transcript tail = (byte size, last real event uuid), and
#   2. the headless's OWN stdout log emits one `result` event per turn it
#      completes — a reliable, monotone count of turns THIS headless produced.
# We watermark the transcript tail together with the headless's result-count.
# At the next inject we recompute both: if the headless's result-count grew,
# the tail moved because of the headless's OWN response → refresh the watermark
# (NOT stale). If after accounting for the headless's own turns the tail STILL
# differs from the watermark → a *different* writer (terminal / other resume)
# appended → STALE. This attribution is what keeps the no-concurrency path from
# false-positiving: a lone headless's own delayed response is recognised as its
# own work, not mistaken for an external writer.
#
# Correctness over precision: a fresh `claude --resume` always reads current
# disk, so retire+respawn is ALWAYS correct. When uncertain we prefer to
# retire — the only cost is an unnecessary respawn, never a rollback.


def _headless_log_result_count(entry):
    """Count `result` events in a Claude headless's own stdout log.

    Each completed turn emits exactly one stream-json {"type":"result"} line,
    so this is a monotone count of turns THIS headless produced — the basis
    for attributing transcript growth to the headless itself vs an external
    writer. Returns 0 if the log can't be read.
    """
    if not isinstance(entry, dict):
        return 0
    log = entry.get("log")
    if not log:
        return 0
    count = 0
    try:
        with open(log, "rb") as f:
            for raw in f:
                # Cheap pre-filter before json.loads on every line.
                if b'"type":"result"' not in raw and b'"type": "result"' not in raw:
                    continue
                try:
                    ev = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if ev.get("type") == "result":
                    count += 1
    except OSError:
        return 0
    return count


def _valid_input_result_target(entry):
    """Return a spawn's valid owned-input result target, else ``None``."""
    value = entry.get("input_result_target") if isinstance(entry, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _valid_input_accepted_at(entry):
    """Return a finite accepted-input timestamp, else ``None``."""
    value = entry.get("input_accepted_at") if isinstance(entry, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _valid_input_command_uuids(entry):
    """Return persisted CCC-owned Claude command UUIDs, else ``None``.

    An empty list is meaningful: it says the entry uses lifecycle accounting
    and currently owes no command acknowledgement. Missing/malformed state is
    legacy and falls back to result-target/log-tail accounting.
    """
    if not isinstance(entry, dict) or "input_command_uuids" not in entry:
        return None
    values = entry.get("input_command_uuids")
    if not isinstance(values, list):
        return None
    cleaned = []
    for value in values:
        value = str(value or "").strip()
        try:
            parsed = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            return None
        canonical = str(parsed)
        if canonical not in cleaned:
            cleaned.append(canonical)
    return cleaned


def _pending_input_command_uuids(entry):
    """Return owned command UUIDs lacking Claude's completed lifecycle event."""
    command_uuids = _valid_input_command_uuids(entry)
    if command_uuids is None:
        return []
    pending = set(command_uuids)
    log = entry.get("log") if isinstance(entry, dict) else None
    if not log or not pending:
        return command_uuids
    try:
        with open(log, "rb") as fh:
            for raw in fh:
                if b'"command_lifecycle"' not in raw or b'"completed"' not in raw:
                    continue
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if (
                    event.get("type") == "command_lifecycle"
                    and event.get("state") == "completed"
                ):
                    pending.discard(str(event.get("command_uuid") or ""))
    except OSError:
        return command_uuids
    return [value for value in command_uuids if value in pending]


def _headless_turn_in_progress(entry):
    """True while a CCC-owned Claude input still owes completion.

    Command UUID lifecycle acknowledgements are authoritative for new writes.
    Legacy result targets and stdout-tail state remain readable across an
    upgrade until the next successful CCC write establishes UUID tracking.
    """
    command_uuids = _valid_input_command_uuids(entry)
    if command_uuids is not None:
        if _pending_input_command_uuids(entry):
            return True
        return _headless_log_turn_open(entry)
    target = _valid_input_result_target(entry)
    if target is not None:
        if _headless_log_result_count(entry) < target:
            return True
        # A mid-turn input may be handled as a separate response after the
        # result that closed the preceding response. Meaningful stdout after
        # the tracked boundary proves that next response has begun. Benign
        # post-result trailers are filtered by `_headless_log_turn_open`.
        return _headless_log_turn_open(entry)
    return _headless_log_turn_open(entry)


def _headless_log_turn_open(entry):
    """True if a Claude headless's own stdout log ends mid-turn.

    Each completed turn ends with exactly one `{"type":"result"}` line (see
    `_headless_log_result_count`). A later assistant/user/stream event, or an
    active system status, opens the next turn until its result. Benign events
    that can trail a completed result (`command_lifecycle`, rate-limit and
    background-task notifications) do not reopen it; treating every trailing
    event as busy previously stranded queued input indefinitely.

    This is the only signal that covers a separately queued next response
    streaming text without a tool child. Returns False (not busy) if the log
    is missing, empty, or unreadable — the same fail-open default as the rest
    of the busy-detection chain.

    KEEP IN SYNC with `worker_turn_open()` in watchtower/workers.py, which is
    a character-identical copy apart from an engine=="claude" guard. The two
    are maintained in parallel with no shared import; if you change the
    turn-open predicate here, change it there too. Same rule as
    ccc_peer_uds.py / watchtower's peer_uds.py.
    """
    if not isinstance(entry, dict):
        return False
    log = entry.get("log")
    if not log:
        return False
    turn_open = False
    active_system_subtypes = {"init", "status", "thinking_tokens"}
    try:
        with open(log, "rb") as f:
            try:
                size = os.fstat(f.fileno()).st_size
            except OSError:
                size = 0
            if size > 65536:
                f.seek(size - 65536)
                f.readline()  # discard partial first line
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(ev, dict):
                    continue
                event_type = ev.get("type")
                if event_type == "result":
                    turn_open = False
                elif event_type in ("assistant", "user", "stream_event"):
                    turn_open = True
                elif (
                    event_type == "system"
                    and ev.get("subtype") in active_system_subtypes
                ):
                    turn_open = True
    except OSError:
        return False
    return turn_open


def _transcript_tail_signature(session_id):
    """Return (size_bytes, last_event_uuid) for a session's transcript.

    last_event_uuid is the `uuid` of the last JSONL event that has one
    (some trailer events like last-prompt/mode have uuid=None — we skip
    those so the signature tracks real conversation events). Returns
    (None, None) if the transcript can't be read.
    """
    path = _core._find_session_jsonl(session_id)
    if path is None:
        return (None, None)
    try:
        size = path.stat().st_size
    except OSError:
        return (None, None)
    last_uuid = None
    try:
        # Read only the tail; transcripts can be large. 64 KiB comfortably
        # covers several events even with big tool payloads.
        with open(path, "rb") as f:
            if size > 65536:
                f.seek(size - 65536)
                f.readline()  # discard partial first line
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                u = ev.get("uuid")
                if u:
                    last_uuid = u
    except OSError:
        return (size, None)
    return (size, last_uuid)


def _spawn_entry_session_id(entry):
    """Best-effort sessionId a Claude spawn entry is driving.

    For a `claude --resume` spawn that's `resumed_sid`; for a fresh spawn
    it's the sid minted in the log header. Returns None if unknown.
    """
    if not isinstance(entry, dict):
        return None
    sid = entry.get("resumed_sid")
    if sid:
        return sid
    log = entry.get("log")
    if log:
        ids = _core._log_session_ids(log)
        if len(ids) == 1:
            return next(iter(ids))
    return None


def _update_spawn_transcript_watermark(entry, session_id=None):
    """Snapshot the transcript tail + the headless's own result-count.

    Call when CCC drives the headless. The pair stored on the entry under
    `_transcript_watermark` = (size, last_uuid, result_count) lets the next
    inject distinguish growth produced by THIS headless (result_count rose)
    from growth produced by an external writer.
    """
    if not isinstance(entry, dict):
        return
    sid = session_id or _spawn_entry_session_id(entry)
    if not sid:
        return
    size, last_uuid = _transcript_tail_signature(sid)
    entry["_transcript_watermark"] = (size, last_uuid, _headless_log_result_count(entry))


def _headless_spawn_is_stale(entry, session_id=None):
    """True if a writer OTHER than this headless advanced the transcript.

    Compares the current transcript tail against the watermark recorded when
    CCC last drove this headless. Growth attributable to the headless's own
    turns (its stdout result-count rose since the watermark) is NOT stale —
    we refresh the watermark in place and return False. Any *remaining*
    advance (tail moved with no new headless result) means a different writer
    appended → stale.

    Returns False (not stale) when there's no watermark yet (first use), the
    transcript can't be read, or the tail matches. Claude-only by
    construction (callers gate on engine == "claude").
    """
    if not isinstance(entry, dict):
        return False
    watermark = entry.get("_transcript_watermark")
    if not watermark:
        return False
    sid = session_id or _spawn_entry_session_id(entry)
    if not sid:
        return False
    cur_size, cur_uuid = _transcript_tail_signature(sid)
    if cur_size is None:
        return False
    prev_size, prev_uuid, prev_results = watermark
    if prev_size is None:
        return False
    # Real conversation events (user/assistant/result) all carry a `uuid`;
    # only benign trailer writes (last-prompt / mode / custom-title /
    # queue-operation / agent-name / pr-link) are uuid-less. Base "the tail
    # moved" on the last uuid'd event — NOT raw byte size. Counting size growth
    # here treated those metadata writes as an external writer and retired warm
    # headless processes (false-positive staleness → cold resume every send;
    # one real transcript was 28% uuid-less events). A genuine external writer
    # still lands a uuid'd turn, so this stays correct for the GH #71 guard.
    tail_moved = (cur_uuid != prev_uuid)
    if not tail_moved:
        return False
    # Tail moved. Did THIS headless produce a new turn since the watermark?
    cur_results = _headless_log_result_count(entry)
    if cur_results > prev_results:
        # Growth is (at least partly) the headless's own response landing on
        # disk after the watermark was taken. Re-baseline to the current tail
        # and treat as current — a lone, busy-then-idle headless is the
        # common no-concurrency case and must not be retired here.
        entry["_transcript_watermark"] = (cur_size, cur_uuid, cur_results)
        return False
    # Tail advanced with no new headless result → an external writer is ahead
    # of the headless's in-memory state.
    # DIAGNOSTIC (premature-death hunt): stash WHY we judged stale so the
    # retire call site can log the discriminator. uuid_changed=False with a
    # positive size_delta means only a uuid-less trailer event (last-prompt /
    # mode / title) grew the file — a benign write that would be a FALSE
    # staleness positive, needlessly killing a warm process.
    try:
        entry["_last_stale_diag"] = {
            "uuid_changed": cur_uuid != prev_uuid,
            "size_delta": (cur_size - prev_size) if (cur_size is not None and prev_size is not None) else None,
            "cur_results": cur_results,
            "prev_results": prev_results,
        }
    except Exception:
        pass
    return True


def _retire_idle_headless_for_session(session_id, *, reason="", defer_if_busy=False, require_approval=False):
    """Retire a CCC-spawned IDLE Claude headless for `session_id` (GH #71).

    Used when a terminal takes over a session (mechanism 2 on launch, and
    mechanism 5 in the background watcher). Safety: only Claude engine, and
    NEVER a busy headless — an active tool child means it's mid-turn, so we
    leave it alone (killing real work is worse than a transient stale read,
    which the use-time check (4) will catch on the next inject anyway).

    CCC-53: when the user explicitly launches a terminal and the headless is
    busy, `defer_if_busy` records intent to retire it as soon as it goes idle
    (honored by the staleness watcher) — so "spawn a terminal" reliably kills
    the headless once its current turn finishes, instead of leaving it running.

    `require_approval` is for callers where retiring the session isn't the
    human directly acting on THIS session (e.g. a model/effort override that
    only needs to apply on the process's NEXT natural resume) — even once
    every busy/pending-prompt/startup-grace guard above says it's safe to
    kill, CCC still shouldn't unilaterally interrupt a live process the human
    didn't ask to interrupt right now. Files an interrupt-ask instead of
    killing; the ask executes the same SIGTERM on approval.

    Returns a dict {retired: bool, pid?, reason?, deferred?}.
    """
    if not session_id:
        return {"retired": False}
    if _core._detect_session_engine(session_id) != "claude":
        return {"retired": False}
    spawn = _core._find_live_spawn_entry_for_session(session_id)
    if spawn is None or spawn.get("engine") != "claude":
        return {"retired": False}
    # Never retire a busy headless. The owned-input boundary covers thinking,
    # pure-text streaming, and between-tool gaps that have no child process;
    # the tool-child probe remains defense in depth for legacy spawn entries.
    if _headless_turn_in_progress(spawn) or _core._spawn_entry_active_tool_child(spawn):
        if defer_if_busy:
            spawn["retire_when_idle"] = True
            spawn["retire_requires_approval"] = require_approval
            spawn["retire_reason"] = reason
            _core._ensure_headless_staleness_watcher_started()
            return {"retired": False, "reason": "busy", "deferred": True}
        return {"retired": False, "reason": "busy"}
    # Also check if the spawn has a pending prompt that hasn't reached a turn
    # boundary yet. `_spawn_entry_active_tool_child` only sees TOOL calls —
    # once the prompt is written, Claude can be mid-response (streaming plain
    # text, no tool child) for a while before its first `result` event, and
    # killing it in that window produces "[Request interrupted by user]" and
    # loses the in-flight response just as much as killing a tool call would.
    # `_headless_log_result_count` covers the whole window (no output yet AND
    # output streaming but not yet complete) with one check.
    if spawn.get("prompt") and _headless_log_result_count(spawn) == 0:
        if defer_if_busy:
            spawn["retire_when_idle"] = True
            spawn["retire_requires_approval"] = require_approval
            spawn["retire_reason"] = reason
            _core._ensure_headless_staleness_watcher_started()
            return {"retired": False, "reason": "pending_prompt", "deferred": True}
        return {"retired": False, "reason": "pending_prompt"}
    # Startup grace period: a freshly spawned headless runs SessionStart hooks
    # (Total Recall, Token Optimizer, Superpowers, etc.) before it produces any
    # stream output.  Hook processes are NOT tool children (they're skipped by
    # _spawn_entry_active_tool_child), so during this window the spawn looks
    # "idle" even though it's actively initializing.  Without this guard the
    # staleness watcher or a status poll can SIGTERM a spawn mid-hook, killing
    # it before it ever processes its first prompt (the spawn-stream then hangs
    # until the browser gives up — "session doesn't load, times out").
    _STARTUP_GRACE_S = 60
    started_epoch = _spawn_entry_started_epoch(spawn)
    if started_epoch and (time.time() - started_epoch) < _STARTUP_GRACE_S:
        sid_for_timeline = _spawn_entry_session_id(spawn)
        timeline = _core._spawn_timeline_get(sid_for_timeline) if sid_for_timeline else None
        marks = (timeline or {}).get("marks") or {}
        if "claude_system_init" not in marks and "claude_first_stream_event" not in marks:
            return {"retired": False, "reason": "startup_grace"}
    pid = spawn.get("pid")
    if require_approval:
        ask = _core._file_interrupt_ask(
            session_id, reason or "retire-idle-headless",
            f"CCC wants to retire this session's headless process "
            f"(reason: {reason or 'unspecified'}) to apply a change on its "
            "next resume. Approve to SIGTERM it now, dismiss to let it keep "
            "running on the current process until it exits naturally.",
            {"kind": "sigterm", "pid": pid},
        )
        return {"retired": False, "reason": "pending_approval",
                "ask_id": (ask or {}).get("id"), "pid": pid}
    spawn.pop("retire_when_idle", None)
    _core._retire_unresponsive_spawn_entry(spawn, terminate=True, reason=reason or "terminal-takeover", caller=reason or "terminal-takeover")
    return {"retired": True, "pid": pid, "reason": reason or "terminal-takeover"}


def _session_has_live_terminal(session_id, exclude_pid=None):
    """True if a live interactive `claude` (a real TTY) exists for `session_id`,
    other than `exclude_pid` (the headless's own pid).

    Reads Claude's per-process registry files directly (rather than
    `_load_session_registry`, which collapses a C3 concurrent pair to a single
    pid) so we can see a terminal that coexists with our headless. A registry
    entry counts as a terminal only if its pid is a live `claude` with a TTY.
    """
    if not session_id:
        return False
    terminal_pids_by_sid = _live_claude_terminal_pids_by_session()
    try:
        exclude_pid = int(exclude_pid) if exclude_pid is not None else None
    except (TypeError, ValueError):
        exclude_pid = None
    return any(
        pid != exclude_pid
        for pid in terminal_pids_by_sid.get(session_id, set())
    )


@_core._ttl_memo(5.0)
def _live_claude_terminal_pids_by_session():
    """Map Claude session ids to live TTY pids with one batched ps probe."""
    out = {}
    if not _core.SESSIONS_REGISTRY.is_dir():
        return out
    try:
        session_files = list(_core.SESSIONS_REGISTRY.iterdir())
    except OSError:
        return out
    pid_to_sids = {}
    for f in session_files:
        if not f.name.endswith(".json") or not f.is_file():
            continue
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        sid = str(data.get("sessionId") or "").strip()
        if not sid:
            continue
        try:
            pid = int(data.get("pid"))
        except (TypeError, ValueError):
            continue
        pid_to_sids.setdefault(pid, set()).add(sid)
    if not pid_to_sids:
        return out
    try:
        ps_out = subprocess.run(
            ["ps", "-o", "pid=,tty=,comm=,args=", "-p", ",".join(str(pid) for pid in sorted(pid_to_sids))],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return out
    if ps_out.returncode != 0:
        return out
    for line in ps_out.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3:
            continue
        pid_s, tty, comm = parts[:3]
        command = parts[3] if len(parts) > 3 else comm
        if not _core._is_real_tty(tty):
            continue
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        cmd_first = (command.split(None, 1)[0] if command else "")
        if not (_core._process_comm_is_claude(comm) or _core._process_comm_is_claude(cmd_first)):
            continue
        for sid in pid_to_sids.get(pid, set()):
            out.setdefault(sid, set()).add(pid)
    return out


def _bg_pty_entry_for_session(session_id):
    """Registry entry for a live Claude bg-pty-daemon process on `session_id`.

    Claude Code's background pty host (registry `kind: "bg"`) runs the REPL
    with no controlling tty, but the session IS attached to an open terminal
    pane. Returns {pid, kind, status, ...} or None. Distinct from both a CCC
    headless spawn and a tty terminal — callers must never SIGTERM these (it
    would close the user's open window).
    """
    if not session_id or not _core.SESSIONS_REGISTRY.is_dir():
        return None
    try:
        session_files = list(_core.SESSIONS_REGISTRY.iterdir())
    except OSError:
        return None
    for f in session_files:
        if not f.name.endswith(".json") or not f.is_file():
            continue
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("sessionId") != session_id or data.get("kind") != "bg":
            continue
        try:
            pid = int(data.get("pid"))
        except (TypeError, ValueError):
            continue
        if _core._pid_is_engine_process(pid, "claude"):
            return data
    return None


_HEADLESS_STALENESS_WATCHER_LOCK = threading.Lock()
_HEADLESS_STALENESS_WATCHER_STARTED = False


def _ensure_headless_staleness_watcher_started() -> None:
    """Idempotently start _start_headless_staleness_watcher in THIS process.

    The dashboard process starts it unconditionally at boot (see main()).
    But `retire_when_idle` can also get set on a spawn entry that lives in
    the WORKER process (e.g. a model-switch deferred while mid-turn, routed
    through the control plane) -- a spawn the dashboard's own watcher can
    never see, since _spawned_sessions is per-process. Callers that set the
    flag call this too, so whichever process actually holds the deferred
    spawn also has a watcher running to honor it, without hardcoding a
    worker-startup hook that's easy to forget when a new deferred-retire
    caller is added.
    """
    global _HEADLESS_STALENESS_WATCHER_STARTED
    with _HEADLESS_STALENESS_WATCHER_LOCK:
        if _HEADLESS_STALENESS_WATCHER_STARTED:
            return
        _HEADLESS_STALENESS_WATCHER_STARTED = True
    _start_headless_staleness_watcher()


def _start_headless_staleness_watcher() -> None:
    """Honor deferred retire requests from terminal launches.

    When the user clicks "Launch Terminal" and the headless is mid-turn,
    _retire_idle_headless_for_session(defer_if_busy=True) sets
    retire_when_idle=True on the spawn entry. This watcher is the only
    thing that honors that flag — it polls every ~8s and retires the
    headless the moment it goes idle.

    The background terminal-scan kill (the original mechanism 5) was
    removed: it killed spawns during SessionStart hooks before they
    could produce output (75 kills in 7 days, 59% under 10s old).
    The launch-time kill (mechanism 2) is the only place that should
    initiate a retire — it fires on an explicit user action.
    """
    # Generation token: after an importlib.reload (test suites pop/reimport
    # server), a watcher thread from the OLD module instance must exit
    # instead of retiring spawns owned by the CURRENT instance via _core.
    gen = _MODULE_GEN

    def _watcher():
        while True:
            time.sleep(8)
            if gen is not _MODULE_GEN:
                return
            try:
                entries = [
                    s for s in list(_core._spawned_sessions)
                    if isinstance(s, dict) and s.get("engine") == "claude"
                ]
            except Exception:
                continue
            for entry in entries:
                try:
                    if _core._poll_spawn_entry(entry) is not None:
                        continue  # already exited
                    if not entry.get("retire_when_idle"):
                        continue
                    if _core._spawn_entry_active_tool_child(entry):
                        continue  # still mid-turn; wait for it to finish
                    sid = _spawn_entry_session_id(entry)
                    if not sid:
                        continue
                    _retire_idle_headless_for_session(
                        sid,
                        reason=entry.get("retire_reason") or "deferred-terminal-takeover",
                        require_approval=bool(entry.get("retire_requires_approval")),
                    )
                except Exception:
                    continue
    threading.Thread(
        target=_watcher, daemon=True, name="headless-staleness-watcher"
    ).start()


def resume_session_headless(session_id, text, cwd=None, idempotency_key=None):
    """Resume a dormant session headlessly (`claude --resume`) and send text.

    If we already resumed this session and the process is still alive, reuse it.
    Optional `cwd` parameter allows bypassing session lookup (useful in remote envs).
    """
    # Claude Task-tool children have their own JSONL transcripts but cannot be
    # resumed independently. Some automatic callers invoke this lower-level
    # helper directly, bypassing _inject_text_into_session's normalization.
    # Always resume the owning parent session instead of sending forbidden
    # direct app-server input to the child (the multi-agent v2 -32600 path).
    # Resolved before control-plane routing so a remote node receives the
    # parent id too.
    session_id = _core._claude_subagent_parent_session_id(session_id) or session_id
    routed = _core._control_plane_engine_call(
        "claude", "resume", {
            "session_id": session_id,
            "text": text,
            "cwd": cwd,
        },
        idempotency_key=idempotency_key,
    )
    if routed is not None:
        return routed
    text = _core._strip_ccc_session_state_instruction(text)
    if not text:
        return {"ok": False, "error": "missing text"}
    # Reuse existing resumed process
    for s in list(_core._spawned_sessions):
        if (
            (s.get("resumed_sid") == session_id or s.get("session_id") == session_id)
            and _core._poll_spawn_entry(s) is None
        ):
            ok = _core._write_stream_json_user_message(s, text)
            if ok:
                _se = _core._resume_entry_started_epoch(s)
                _core._resume_ledger_append(
                    "reuse_hit", sid=session_id, pid=s.get("pid"),
                    lifetime_s=(round(time.time() - _se, 1) if _se else None),
                )
                return {"ok": True, "pid": s["pid"], "resumed": True, "reused": True}
            if _headless_turn_in_progress(s) or _core._spawn_entry_active_tool_child(s):
                return {
                    "ok": False,
                    "pid": s["pid"],
                    "resumed": True,
                    "reused": True,
                    "error": "session input pipe is busy",
                }
            _core._retire_unresponsive_spawn_entry(s, terminate=True, reason="write_failed")
            break

    if cwd:
        try:
            ctx = _core._resolve_cwd_context(cwd)
        except _core.RepoContextError as e:
            return e.as_payload()
    else:
        try:
            ctx = _core.repo_from_session(session_id)
        except _core.RepoContextError as e:
            return e.as_payload()
    cwd = ctx["cwd"]
    rebucket = _core._ensure_session_jsonl_for_cwd(session_id, cwd)
    if not rebucket.get("ok"):
        return {
            "ok": False,
            "error": rebucket.get("message") or rebucket.get("error") or "session jsonl unavailable",
            "code": rebucket.get("error") or "session_jsonl_unavailable",
        }
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-{session_id[:8]}-{timestamp}.log"
    log_dir = _core.repo_log_dir(ctx["repo_path"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename

    claude_bin = _core._resolve_claude_bin()
    if not claude_bin.get("available"):
        return {
            "ok": False,
            "error": claude_bin.get("reason") or "Claude Code CLI not found",
            "code": claude_bin.get("code", "claude_unavailable"),
        }

    cmd = [
        claude_bin["bin"], "-p", "--verbose",
        "--resume", session_id,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
    ]
    cmd.extend(_core._claude_session_state_args())
    cmd.extend(_core._claude_peer_inbound_args())
    # Per-session override (set via the click-to-switch picker). Resume
    # would otherwise inherit the previously-recorded model.
    #
    # CCC-55: the `[1m]` suffix is a TUI-only affordance for the interactive
    # `/model` slash command — it is NOT a valid `--model` value. Spawning
    # `claude -p --model opus-4-8[1m]` is rejected with "There's an issue with
    # the selected model (opus-4-8[1m]). It may not exist." In headless mode
    # the 1M context window is enabled via the `context-1m-2025-08-07` beta
    # header (`--betas`), not a model-id suffix. Use _cli_model_flag() which
    # also expands versioned short aliases (e.g. sonnet-4-6 → claude-sonnet-4-6)
    # since the --model flag does not accept bare versioned aliases for 4.x models.
    override = _core._get_session_override(session_id)
    if override and override.get("model"):
        alias = _core._cli_model_flag(override["model"])  # strips [1m], normalizes to full ID
        if alias:
            cmd.extend(["--model", alias])
        if override.get("context_1m"):
            cmd.extend(["--betas", "context-1m-2025-08-07"])
        effort = str(override.get("reasoning_effort") or "").strip().lower()
        if effort in _core.CLAUDE_REASONING_EFFORTS and effort:
            cmd.extend(["--effort", effort])

    # Diagnostic: we reached the fresh-spawn path, so no live warm process was
    # reused for this sid. Log the miss and stamp the spawn epoch for lifetime.
    _core._resume_ledger_append("reuse_miss", sid=session_id, reason="no_live_entry")
    _spawn_started_epoch = time.time()

    log_fh = open(log_path, "w")
    fifo_path, child_stdin_fd = _core._make_stdin_fifo(log_path)
    popen_kwargs = dict(
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        start_new_session=True,
        env=_core._question_relay_env(),
    )
    popen_kwargs["stdin"] = child_stdin_fd if child_stdin_fd is not None else subprocess.PIPE
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        if child_stdin_fd is not None:
            _core._close_fd_quiet(child_stdin_fd)
        if fifo_path:
            _core._unlink_quiet(fifo_path)
        return {
            "ok": False,
            "error": f"Claude Code CLI failed to start: {e}",
            "code": "claude_unavailable",
        }
    # Keep a parent-owned writer open before dropping the local RDWR fd.
    stdin_fd = _core._open_fifo_writer(fifo_path) if fifo_path else None
    if child_stdin_fd is not None:
        _core._close_fd_quiet(child_stdin_fd)

    entry = {
        "pid": proc.pid,
        "name": f"resume-{session_id[:8]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "started_epoch": _spawn_started_epoch,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
        "fifo": fifo_path,
        "stdin_fd": stdin_fd,
        "engine": "claude",
        "cwd": cwd,
        "repo_path": ctx["repo_path"],
    }
    ok = _core._write_stream_json_user_message(entry, text, timeout=30)
    if not ok:
        message = "Claude Code started, but CCC could not write the resume prompt to stdin."
        _write_spawn_error_event(log_fh, "spawn_stdin_unavailable", message)
        _core._retire_unresponsive_spawn_entry(entry, terminate=True, reason="write_failed")
        return {
            "ok": False,
            "error": message,
            "code": "spawn_stdin_unavailable",
            "pid": proc.pid,
            "log": str(log_path),
            "resumed": True,
        }
    _core._spawned_sessions.append(entry)
    # GH #71 — baseline the staleness watermark to the transcript as it stands
    # at fresh-resume time. This resume just read current disk, so the tail is
    # current; any later advance with no new headless turn means an external
    # writer took over. result_count starts at 0 (the initial prompt's response
    # hasn't been written yet — the next inject's check will attribute it).
    _core._update_spawn_transcript_watermark(entry, session_id)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=entry["name"],
        log_path=log_path,
        cwd=cwd,
        spawned_at=timestamp,
        command_summary=text[:200],
        fifo=fifo_path,
        engine="claude",
        session_id=session_id,
        repo_path=ctx["repo_path"],
        input_result_target=entry.get("input_result_target"),
        input_accepted_at=entry.get("input_accepted_at"),
        input_command_uuids=entry.get("input_command_uuids"),
    )
    # Diagnostic: a fresh warm process is now live. Record the gap since this
    # sid's last exit/cold_resume — a short gap right after `server_start`
    # implicates a restart-EOF killing the previous warm process.
    _prev_epoch = _core._RESUME_LAST_EPOCH.get(session_id)
    _core._resume_ledger_append(
        "cold_resume", sid=session_id, pid=proc.pid,
        repo=str(ctx["repo_path"]), log_path=str(log_path),
        gap_since_last_exit_s=(round(time.time() - _prev_epoch, 1) if _prev_epoch else None),
    )
    return {
        "ok": True,
        "pid": proc.pid,
        "log": str(log_path),
        "resumed": True,
        "initial_prompt_written": True,
    }


