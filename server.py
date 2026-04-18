#!/usr/bin/env python3
"""
Claude Log Viewer — Web UI

Browse and view claude-issue-watcher stream-json logs in the browser.

Usage:
    ./scripts/claude-log-viewer-web.py             # starts on port 8090
    PORT=9000 ./scripts/claude-log-viewer-web.py   # custom port
"""

import ast
import http.server
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

# The repository the command center is watching. Override with CCC_WATCH_REPO;
# defaults to the current working directory when the server is launched.
_env_watch = os.environ.get("CCC_WATCH_REPO")
REPO_ROOT = Path(_env_watch).resolve() if _env_watch else Path.cwd().resolve()
LOG_DIR = REPO_ROOT / ".claude" / "logs"
FALLBACK_DIR = Path("/tmp")
WATCHER_SCRIPT = REPO_ROOT / "scripts" / "claude-issue-watcher.sh"
# Claude Code encodes project path by replacing "/" with "-" under ~/.claude/projects/
_cc_project_slug = "-" + str(REPO_ROOT).lstrip("/").replace("/", "-")
CONVERSATIONS_DIR = Path.home() / ".claude" / "projects" / _cc_project_slug
# Tool's own assets live next to this file.
CCC_ROOT = Path(__file__).resolve().parent
STATIC_DIR = CCC_ROOT / "static"
MORNING_STATIC_DIR = STATIC_DIR / "morning"
AFFILIATES_STATIC_DIR = STATIC_DIR / "affiliates"

import morning  # morning.py — goals/tasks/inbox API (Phase 1 sample data)
import affiliates_store  # JSON-backed store for the Affiliates section
PORT = int(os.environ.get("PORT", 8090))
# Optional title-prefix noise stripper (e.g. "[BYM Problem]"). Comma-separated prefixes.
# Empty by default; set `CCC_TITLE_STRIP=BYM,FOO` to strip `[BYM ...]` and `[FOO ...]` from titles.
TITLE_STRIP_PREFIXES = [p for p in os.environ.get("CCC_TITLE_STRIP", "").split(",") if p]

# Optional org-tagger for multi-tenant apps. Set CCC_ORG_PATTERNS as
# `Label1:pat1a|pat1b;Label2:pat2`. The server scans each GitHub issue body
# for the patterns and tags the card with `org: "Label1"`, letting the UI
# group backlog by org. Leave unset and every issue is tagged `org: null`.
_org_spec = os.environ.get("CCC_ORG_PATTERNS", "")
ORG_PATTERNS = []
for chunk in _org_spec.split(";"):
    if ":" not in chunk:
        continue
    label, pats = chunk.split(":", 1)
    label = label.strip()
    alts = [p.strip() for p in pats.split("|") if p.strip()]
    if label and alts:
        try:
            ORG_PATTERNS.append((label, re.compile("|".join(alts), re.IGNORECASE)))
        except re.error:
            pass


def _detect_issue_org(body):
    """Return the first matching org label for an issue body, or None."""
    if not body or not ORG_PATTERNS:
        return None
    for label, rx in ORG_PATTERNS:
        if rx.search(body):
            return label
    return None
_TITLE_STRIP_RE = re.compile(
    r"^\s*\[(?:" + "|".join(re.escape(p) for p in TITLE_STRIP_PREFIXES) + r")[^\]]*\]\s*"
) if TITLE_STRIP_PREFIXES else None


def _strip_title_prefix(title):
    if not title or not _TITLE_STRIP_RE:
        return title
    return _TITLE_STRIP_RE.sub("", title)

# Sidecar state (written by hooks)
SIDECAR_STATE_DIR = Path.home() / ".claude" / "log-viewer" / "live-state"
HOOK_SCRIPTS_DIR = Path.home() / ".claude" / "log-viewer" / "hooks"
HOOK_MARKER = "log-viewer/hooks/"

# Global watcher process handle
_watcher_proc = None
_watcher_output_lines = []

# Spawned headless Claude sessions
_spawned_sessions = []  # [{pid, name, log, proc}]


# ---------------------------------------------------------------------------
# Log parsing (mirrors the bash viewer filter logic)
# ---------------------------------------------------------------------------

def extract_session_id(path):
    """Scan the first ~60 lines of a stream-json log file for a session_id UUID."""
    try:
        with open(path, "r") as f:
            for i, line in enumerate(f):
                if i >= 60:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = ev.get("session_id") or ev.get("sessionId")
                if sid and len(sid) >= 32:
                    return sid
    except (OSError, UnicodeDecodeError):
        pass
    return None


# Cache of session_id -> cwd so we don't rescan ~/.claude/projects on every request
_session_cwd_cache = {}
_session_cwd_cache_mtime = 0

PROJECTS_ROOT = Path.home() / ".claude" / "projects"
SESSIONS_REGISTRY = Path.home() / ".claude" / "sessions"  # per-pid {sessionId, cwd, ...}
LOG_VIEWER_STATE_DIR = Path.home() / ".claude" / "log-viewer"
SESSION_NAMES_FILE = LOG_VIEWER_STATE_DIR / "session-names.json"  # side-car overrides
CONVERSATION_ORDER_FILE = LOG_VIEWER_STATE_DIR / "conversation-order.json"  # [session_id,...]
ARCHIVED_CONVERSATIONS_FILE = LOG_VIEWER_STATE_DIR / "archived-conversations.json"  # [session_id,...]
VERIFIED_CONVERSATIONS_FILE = LOG_VIEWER_STATE_DIR / "verified-conversations.json"  # [session_id,...]
SESSION_ISSUES_FILE = LOG_VIEWER_STATE_DIR / "session-issues.json"  # {session_id: issue_number}
FIX_DEPLOY_SPAWNED_FILE = LOG_VIEWER_STATE_DIR / "fix-deploy-spawned.json"  # {commit_sha: {pid, spawned_at, name}}

# {path: {mtime, custom_title, last_prompt, agent_name}}
_conv_meta_cache = {}


_META_MARKERS = (
    '"type":"custom-title"',
    '"type":"agent-name"',
    '"type":"last-prompt"',
)

# Markers for session signals — only lines with these need full JSON parse
_SIGNAL_MARKERS = (
    '"tool_use"',     # Edit/Write/Bash tool calls
    '"type":"result"',  # turn completion
)


def _extract_tail_meta(path):
    """Extract metadata + session signals from a jsonl in a single pass.

    Metadata: custom-title, agent-name, last-prompt (from /rename etc.)
    Signals:  stage (planning→coding→committed→pushed), last event type,
              activity status (working/waiting/idle).

    Uses string pre-filters to skip the vast majority of lines without
    JSON-parsing them. Cached by mtime.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _conv_meta_cache.get(str(path))
    if cached and cached.get("mtime") == mtime:
        return cached
    meta = {
        "mtime": mtime,
        # last_meaningful_ts: timestamp of the most recent user/assistant/result
        # event. Administrative writes (custom-title, agent-name, etc.) don't
        # bump this, so renames don't artificially push cards to "just now".
        "last_meaningful_ts": 0,
        "custom_title": None,
        "agent_name": None,
        "last_prompt": None,
        # Session signals — positions track ordering so stage can regress
        "has_edit": False,
        "has_commit": False,
        "has_push": False,
        "last_edit_pos": 0,
        "last_commit_pos": 0,
        "last_push_pos": 0,
        "last_event_type": None,  # "assistant", "result", "user", etc.
        "pending_tool": None,     # tool awaiting approval (last assistant had tool_use, no result yet)
        "pending_file": None,     # file path from pending tool
        "last_assistant_text": None,  # last text block from an assistant message (the "outcome")
        # Issue number detected from Bash/commit content — covers sessions where the
        # issue wasn't in the spawn prompt (e.g. Claude ran `gh issue create` mid-session).
        "tail_issue_number": None,
    }
    # Regexes compiled once per call; order matters — earlier = higher confidence.
    _gh_issue_cmd_re = re.compile(r'gh\s+issue\s+(?:view|edit|close|comment|reopen|create)\s+(?:.*?)(?<!\d)(\d{1,6})(?!\d)')
    _closes_re = re.compile(r'(?i)\bClos(?:es|e|ed|ing)\s+#(\d{1,6})\b')
    _gh_url_re = re.compile(r'github\.com/[^/\s]+/[^/\s]+/issues/(\d{1,6})')
    _pos = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                _pos += 1
                is_meta = any(m in line for m in _META_MARKERS)
                is_signal = not is_meta and any(m in line for m in _SIGNAL_MARKERS)
                # User/assistant events may not start with "type" (parentUuid first).
                # Check for a timestamp + user/assistant marker to catch them.
                is_typed = not is_meta and not is_signal and (
                    line.startswith('{"type":')
                    or '"type":"user"' in line
                    or '"type":"assistant"' in line
                    or '"type":"result"' in line
                )
                if not (is_meta or is_signal or is_typed):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get("type", "")
                # Track last event type for activity detection
                if t in ("assistant", "result", "user"):
                    meta["last_event_type"] = t
                    # Clear pending tool when a result or user msg arrives
                    if t in ("result", "user"):
                        meta["pending_tool"] = None
                        meta["pending_file"] = None
                    # Record meaningful-activity timestamp (ISO 8601 → epoch)
                    ts = ev.get("timestamp", "")
                    if ts:
                        try:
                            from datetime import datetime as _dt
                            # Format like "2026-04-12T20:42:58.123Z" (UTC)
                            dt = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                            meta["last_meaningful_ts"] = dt.timestamp()
                        except (ValueError, ImportError):
                            pass
                # Metadata
                if t == "custom-title":
                    meta["custom_title"] = ev.get("customTitle") or meta["custom_title"]
                elif t == "agent-name":
                    meta["agent_name"] = ev.get("agentName") or meta["agent_name"]
                elif t == "last-prompt":
                    meta["last_prompt"] = ev.get("lastPrompt") or meta["last_prompt"]
                # Session signals from tool calls
                elif t == "assistant":
                    last_tool_name = None
                    last_tool_file = None
                    # Capture last text block from this assistant turn as the "outcome"
                    for block in ev.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            txt = (block.get("text") or "").strip()
                            if txt:
                                meta["last_assistant_text"] = txt
                    for block in ev.get("message", {}).get("content", []):
                        if block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        inp = block.get("input", {})
                        last_tool_name = name
                        last_tool_file = inp.get("file_path") or inp.get("command", "")[:60] or None
                        if name in ("Edit", "Write", "NotebookEdit"):
                            meta["has_edit"] = True
                            meta["last_edit_pos"] = _pos
                        elif name == "Bash":
                            cmd = inp.get("command", "")
                            if "git commit" in cmd:
                                meta["has_commit"] = True
                                meta["last_commit_pos"] = _pos
                            if "git push" in cmd:
                                meta["has_push"] = True
                                meta["last_push_pos"] = _pos
                            # Detect issue number from high-confidence signals
                            mi = (_gh_issue_cmd_re.search(cmd)
                                  or _closes_re.search(cmd)
                                  or _gh_url_re.search(cmd))
                            if mi:
                                meta["tail_issue_number"] = mi.group(1)
                    # The last assistant message's tool_use is "pending" until
                    # a tool_result or user message clears it
                    if last_tool_name:
                        meta["pending_tool"] = last_tool_name
                        meta["pending_file"] = last_tool_file
    except OSError:
        pass
    _conv_meta_cache[str(path)] = meta
    return meta


def _load_session_name_overrides():
    """Load user-set names from the side-car file. Returns {session_id: name}."""
    try:
        return json.loads(SESSION_NAMES_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _load_conversation_order():
    """Load user-set conversation order. Returns list of session_ids (or []) ."""
    try:
        data = json.loads(CONVERSATION_ORDER_FILE.read_text())
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_conversation_order(order):
    """Persist custom conversation order (list of session_ids)."""
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(order, list):
        order = []
    CONVERSATION_ORDER_FILE.write_text(json.dumps(order, indent=2))
    return order


def _load_archived_conversations():
    """Load list of archived session_ids from the side-car file."""
    try:
        data = json.loads(ARCHIVED_CONVERSATIONS_FILE.read_text())
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_archived_conversations(archived):
    """Persist list of archived session_ids."""
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(archived, list):
        archived = []
    ARCHIVED_CONVERSATIONS_FILE.write_text(json.dumps(archived, indent=2))
    return archived


def _load_verified_conversations():
    """Load list of verified session_ids."""
    try:
        data = json.loads(VERIFIED_CONVERSATIONS_FILE.read_text())
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_verified_conversations(verified):
    """Persist list of verified session_ids."""
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not isinstance(verified, list):
        verified = []
    VERIFIED_CONVERSATIONS_FILE.write_text(json.dumps(verified, indent=2))
    return verified


def _load_session_issues():
    """Load {session_id: issue_number} map of sessions linked to GitHub issues."""
    try:
        data = json.loads(SESSION_ISSUES_FILE.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_session_issue(session_id, issue_number):
    """Record that a session is linked to a GitHub issue. Pass None to unlink."""
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    current = _load_session_issues()
    if issue_number:
        current[session_id] = str(issue_number)
    else:
        current.pop(session_id, None)
    SESSION_ISSUES_FILE.write_text(json.dumps(current, indent=2))
    global _SESSION_ISSUES_CACHE
    _SESSION_ISSUES_CACHE = current
    return current


_SESSION_ISSUES_CACHE = None

_SESSION_STATE_RE = re.compile(
    r"<session-state>\s*(.*?)\s*</session-state>",
    re.IGNORECASE | re.DOTALL,
)
_SESSION_STATE_FIELD_RE = re.compile(
    r"^(DID|INSIGHT|NEXT_STEP_USER)\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_session_state(text):
    """Extract the structured `<session-state>` block sessions emit on final
    reply. Returns {did, insight, next_step_user} or None.
    """
    if not text:
        return None
    m = _SESSION_STATE_RE.search(text)
    if not m:
        return None
    body = m.group(1)
    out = {"did": None, "insight": None, "next_step_user": None}
    for fm in _SESSION_STATE_FIELD_RE.finditer(body):
        key = fm.group(1).upper()
        val = fm.group(2).strip()
        if key == "DID":
            out["did"] = val
        elif key == "INSIGHT":
            out["insight"] = val
        elif key == "NEXT_STEP_USER":
            out["next_step_user"] = val
    if not any(out.values()):
        return None
    return out


def _detect_issue_number_for_session(conv):
    """Try to extract a GitHub issue number this session references.

    Explicit side-car mapping is authoritative. For heuristic detection,
    require strong markers to avoid false positives like "Image #1".
    """
    global _SESSION_ISSUES_CACHE
    if _SESSION_ISSUES_CACHE is None:
        _SESSION_ISSUES_CACHE = _load_session_issues()
    sid = conv.get("session_id", "")
    # Explicit mapping wins
    explicit = _SESSION_ISSUES_CACHE.get(sid)
    if explicit:
        return str(explicit)
    # Strong patterns only (avoid "Image #1" false positives):
    #   - branch like "issue-91", "fix/91", "fix-91"
    #   - display_name or first_message containing "issue 91", "issue-91",
    #     "issue/91", "fix-91", or "GitHub issue #91" / "GitHub issue 91"
    strong = re.compile(
        r"(?:github\s+)?(?:issue|fix)[\s/-]+#?(\d+)",
        re.IGNORECASE,
    )
    branch = conv.get("branch", "") or ""
    m = strong.search(branch)
    if m:
        return m.group(1)
    dname = conv.get("display_name", "") or ""
    m = strong.search(dname)
    if m:
        return m.group(1)
    # display_name that starts with "#NN: " or "#NN " is a prefix style
    m = re.match(r"^#(\d+)[:\s]", dname)
    if m:
        return m.group(1)
    # Fall back to first_message — spawn prompts include "Fix GitHub issue #N: ..."
    fm = conv.get("first_message", "") or ""
    m = strong.search(fm[:200])  # only head; avoids body noise
    if m:
        return m.group(1)
    # Last resort: high-confidence signals mined from the jsonl tail (gh issue cmds,
    # "Closes #N" in commits, github.com/.../issues/N URLs). Covers sessions where
    # Claude created or touched an issue mid-conversation.
    tail_num = conv.get("tail_issue_number")
    if tail_num:
        return str(tail_num)
    return None


def _latest_commit_sha(cwd=None):
    """Return the latest commit SHA (short) from the given cwd or REPO_ROOT."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(cwd) if cwd else str(REPO_ROOT),
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def create_github_issue_for_session(conv):
    """Create a new GitHub issue populated from the session's data.

    Returns {ok, issue_number, issue_url} or {ok: False, error}.
    """
    sid = conv.get("session_id")
    title = conv.get("display_name") or conv.get("first_message", "")[:80] or "Untitled session"
    # Clean the title: strip dashes, truncate
    display_title = title.replace("-", " ").strip()[:120]
    body_parts = []
    fm = conv.get("first_message", "")
    if fm:
        body_parts.append("**Original prompt:**\n\n" + fm)
    last = conv.get("last_prompt", "")
    if last and last != fm:
        body_parts.append("\n**Most recent prompt:**\n\n" + last)
    branch = conv.get("branch", "")
    if branch:
        body_parts.append(f"\n**Branch:** `{branch}`")
    if sid:
        body_parts.append(f"\n_Created from session viewer. Session ID: `{sid}`_")
    body = "\n".join(body_parts) or "Created from session viewer."
    try:
        out = subprocess.run(
            ["gh", "issue", "create", "--title", display_title, "--body", body],
            capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
        )
        if out.returncode != 0:
            return {"ok": False, "error": (out.stderr or "gh issue create failed").strip()}
        url = out.stdout.strip()
        # URL is like https://github.com/user/repo/issues/123
        m = re.search(r"/issues/(\d+)", url)
        issue_num = m.group(1) if m else ""
        if issue_num and sid:
            _save_session_issue(sid, issue_num)
        # Invalidate backlog cache so this issue doesn't show as backlog
        global _backlog_issues_cache_ts
        _backlog_issues_cache_ts = 0
        return {"ok": True, "issue_number": issue_num, "issue_url": url}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"ok": False, "error": str(e)}


def close_github_issue_with_commit(issue_number, conv):
    """Close a GitHub issue and add a comment referencing the latest commit."""
    cwd = conv.get("session_cwd") or str(REPO_ROOT)
    sha = _latest_commit_sha(cwd)
    name = conv.get("display_name") or conv.get("session_id", "")
    comment = f"Verified via session viewer ({name})"
    if sha:
        comment += f". Latest commit: {sha}"
    try:
        subprocess.run(
            ["gh", "issue", "comment", str(issue_number), "--body", comment],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    try:
        out = subprocess.run(
            ["gh", "issue", "close", str(issue_number)],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        ok = out.returncode == 0
        if ok:
            # We need the global declared in mark_issue_in_progress; use the helper.
            # remove_in_progress_label is defined later in this module.
            try:
                _globals = globals()
                fn = _globals.get("remove_in_progress_label")
                if fn:
                    fn(issue_number)
            except Exception:
                pass
        return ok
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _save_session_name_override(session_id, name):
    """Write a user-set name to the side-car file."""
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    current = _load_session_name_overrides()
    if name:
        current[session_id] = name
    else:
        current.pop(session_id, None)
    SESSION_NAMES_FILE.write_text(json.dumps(current, indent=2))
    return current


def _find_session_jsonl(session_id):
    """Scan ~/.claude/projects/*/ for <session_id>.jsonl. Returns Path or None."""
    if not PROJECTS_ROOT.is_dir():
        return None
    target = session_id + ".jsonl"
    for project_dir in PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / target
        if candidate.is_file():
            return candidate
    return None


def _append_custom_title(path, session_id, name):
    """Append a custom-title event to a session's .jsonl file.

    Uses the exact shape Claude writes when you run /rename, so `claude --resume`
    will pick up the new name next time it reads the file.
    """
    event = {"type": "custom-title", "customTitle": name, "sessionId": session_id}
    # Ensure file ends with a newline before appending (defensive — append
    # mode writes at EOF, and a missing trailing newline would glue lines)
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)  # end
            size = f.tell()
            if size > 0:
                f.seek(size - 1)
                tail = f.read(1)
            else:
                tail = b"\n"
    except OSError:
        tail = b"\n"
    with open(path, "a", encoding="utf-8") as f:
        if tail != b"\n":
            f.write("\n")
        f.write(json.dumps(event) + "\n")
    # Invalidate our meta cache so next listing picks up the change
    _conv_meta_cache.pop(str(path), None)


def rename_session(session_id, name):
    """Rename a session, writing through to the .jsonl when safe.

    Strategy:
      1. If session is dormant AND .jsonl exists AND name is non-empty:
         append a custom-title event to the .jsonl (visible to claude --resume).
         Clear any stale side-car entry.
      2. Otherwise: write to the side-car file only. Used for live sessions
         (to avoid racing claude's writes), missing jsonls, and name clears.

    Returns {ok, method, live, error?}.
    """
    result = {"ok": False, "method": None, "live": False}
    if not session_id:
        result["error"] = "missing session_id"
        return result

    cwd = find_session_cwd(session_id)
    status = session_live_status(session_id, cwd)
    is_live = bool(status.get("live"))
    result["live"] = is_live

    path = _find_session_jsonl(session_id)
    # Extra safety: even if the session isn't in the registry, refuse to
    # write-through if the .jsonl was touched very recently — some entrypoints
    # (SDK, background tasks) don't write ~/.claude/sessions/<pid>.json and
    # would race with our append.
    recently_touched = False
    if path is not None:
        try:
            recently_touched = (time.time() - path.stat().st_mtime) < 30
        except OSError:
            pass
    can_writethrough = (not is_live) and (not recently_touched) and (path is not None) and bool(name)

    if can_writethrough:
        try:
            _append_custom_title(path, session_id, name)
        except OSError as e:
            # Fall back to side-car on write failure
            try:
                _save_session_name_override(session_id, name or None)
                result["ok"] = True
                result["method"] = "sidecar"
                result["error"] = f"jsonl append failed, used side-car: {e}"
                return result
            except OSError as e2:
                result["error"] = f"both paths failed: {e2}"
                return result
        # Also record in side-car as a "user set this from the viewer" marker.
        # Display priority still comes from the jsonl (authoritative), but the
        # side-car's presence is used to render the teal "I renamed this" color.
        try:
            _save_session_name_override(session_id, name)
        except OSError:
            pass  # non-fatal
        result["ok"] = True
        result["method"] = "jsonl"
        return result

    # Side-car path: live session, missing jsonl, or clearing a name
    try:
        _save_session_name_override(session_id, name or None)
    except OSError as e:
        result["error"] = f"side-car write failed: {e}"
        return result
    result["ok"] = True
    result["method"] = "sidecar"
    return result


def _extract_first_message(session_id):
    """Read a session's opening user prompt from its .jsonl."""
    path = _find_session_jsonl(session_id)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "user":
                    continue
                content = ev.get("message", {}).get("content", "")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    text = "\n".join(parts)
                else:
                    text = ""
                text = text.strip()
                if text and not text.startswith("<system-reminder>") and not text.startswith("<command-") and not text.startswith("<local-command"):
                    return text[:1500]
    except OSError:
        pass
    return ""


def summarize_session_title(session_id):
    """Use `claude -p` to produce a concise title for a session's opening prompt."""
    result = {"ok": False}
    first_msg = _extract_first_message(session_id)
    if not first_msg:
        result["error"] = "no opening prompt found"
        return result

    instruction = (
        "Produce a concise 4-8 word title summarizing what the user is trying to do "
        "below. No quotes, no trailing punctuation, just the title itself on a single "
        "line. Skip image references and boilerplate.\n\n"
        "Opening prompt:\n"
        + first_msg
        + "\n\nTitle:"
    )

    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", "claude-haiku-4-5-20251001", instruction],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except FileNotFoundError:
        result["error"] = "claude CLI not in PATH"
        return result
    except subprocess.TimeoutExpired:
        result["error"] = "claude -p timed out"
        return result

    if proc.returncode != 0:
        result["error"] = (proc.stderr or "").strip()[:300] or f"claude exited {proc.returncode}"
        return result

    raw = (proc.stdout or "").strip().splitlines()
    title = ""
    for line in reversed(raw):
        s = line.strip().strip('"').strip("'").rstrip(".")
        if s:
            title = s
            break
    if not title:
        result["error"] = "empty response"
        return result

    # Cap length defensively
    title = title[:120]
    rename_result = rename_session(session_id, title)
    result["ok"] = bool(rename_result.get("ok"))
    result["title"] = title
    result["rename_method"] = rename_result.get("method")
    if not result["ok"]:
        result["error"] = rename_result.get("error") or "rename failed"
    return result


# Terminal apps we know how to focus via AppleScript. Matched case-insensitively
# against the comm of an ancestor process of the running claude.
_TERMINAL_APPS = {
    "terminal": "Terminal",
    "iterm": "iTerm2",
    "iterm2": "iTerm2",
    "ghostty": "Ghostty",
    "wezterm": "WezTerm",
    "wezterm-gui": "WezTerm",
    "alacritty": "Alacritty",
    "kitty": "kitty",
    "warp": "Warp",
    "warp-preview": "Warp",
    "hyper": "Hyper",
    "tabby": "Tabby",
}


def _proc_ancestor_terminal(pid):
    """Walk a PID's parent chain and return (term_app_friendly_name, term_pid) or (None, None).

    Uses `ps -o ppid,comm -p <pid>` to avoid parsing platform-specific /proc.
    Stops at init (ppid==1) or when a known terminal app is found.
    """
    current = pid
    for _ in range(20):  # hard cap to avoid runaway loops
        try:
            out = subprocess.run(
                ["ps", "-o", "pid,ppid,comm", "-p", str(current)],
                capture_output=True, text=True, timeout=1,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None, None
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        if len(lines) < 2:
            return None, None
        parts = lines[1].split(None, 2)
        if len(parts) < 3:
            return None, None
        _pid, ppid, comm = parts
        comm_base = comm.rsplit("/", 1)[-1].lower()
        # Strip .app/Contents/MacOS/... suffix by taking only basename
        comm_base = comm_base.replace(".app", "")
        for key, friendly in _TERMINAL_APPS.items():
            if comm_base == key or comm_base.startswith(key):
                return friendly, int(_pid)
        if ppid == "1" or ppid == "0":
            return None, None
        current = int(ppid)
    return None, None


def _proc_cwd(pid):
    """Return a process's cwd via lsof, or None."""
    try:
        out = subprocess.run(
            ["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
            capture_output=True, text=True, timeout=1,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    for line in out.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def find_live_claude_processes():
    """Return list of dicts for every running `claude` CLI process:

    [{pid, tty, cwd, terminal_app}, ...]

    Uses `ps -A -o pid,comm` + manual filter. We avoid `pgrep -x claude`
    because on macOS it can silently miss some processes (observed: one
    out of six live claudes was absent from pgrep output while ps -A
    listed it correctly).
    """
    procs = []
    try:
        ps_out = subprocess.run(
            ["ps", "-A", "-o", "pid=,comm="],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return procs
    pids = []
    for line in ps_out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid, comm = parts
        # comm is the basename of the executable; match exactly "claude"
        if comm.rsplit("/", 1)[-1] == "claude":
            pids.append(pid)
    if not pids:
        return procs
    # Get tty for each pid in one call
    try:
        ps_out = subprocess.run(
            ["ps", "-o", "pid,tty", "-p", ",".join(pids)],
            capture_output=True, text=True, timeout=1,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return procs
    tty_by_pid = {}
    for line in ps_out.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            tty_by_pid[parts[0]] = parts[1]
    for pid in pids:
        cwd = _proc_cwd(pid)
        if not cwd:
            continue
        term_app, _term_pid = _proc_ancestor_terminal(pid)
        procs.append({
            "pid": int(pid),
            "tty": tty_by_pid.get(pid),
            "cwd": cwd,
            "terminal_app": term_app,
        })
    return procs


def _load_session_registry():
    """Read ~/.claude/sessions/*.json and return {session_id: {pid, cwd, ...}}.

    Claude Code writes one JSON file per running process with its current
    sessionId, giving us an authoritative pid↔session mapping.

    Staleness filter: we verify the pid still belongs to a `claude` process
    (not just that the pid exists — OSes recycle pids, so a dead claude's
    pid might be reused by something unrelated, which would silently point
    our Jump button at the wrong terminal).
    """
    registry = {}
    if not SESSIONS_REGISTRY.is_dir():
        return registry
    # Build a set of currently-live claude pids in one ps call
    live_claude_pids = set()
    try:
        ps_out = subprocess.run(
            ["ps", "-A", "-o", "pid=,comm="],
            capture_output=True, text=True, timeout=2,
        )
        for line in ps_out.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[1].rsplit("/", 1)[-1] == "claude":
                try:
                    live_claude_pids.add(int(parts[0]))
                except ValueError:
                    pass
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    for f in SESSIONS_REGISTRY.iterdir():
        if not f.name.endswith(".json") or not f.is_file():
            continue
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("sessionId")
        try:
            pid = int(data.get("pid"))
        except (TypeError, ValueError):
            continue
        if not sid:
            continue
        if pid not in live_claude_pids:
            continue  # stale: pid dead or reassigned to a non-claude
        registry[sid] = data
    return registry


def session_live_status(session_id, session_cwd):
    """Look up a session's running process via ~/.claude/sessions/<pid>.json.

    Returns dict {live, pid, tty, cwd, terminal_app, recently_written}.
    The registry gives us an authoritative pid↔session mapping written by
    Claude Code itself — no more cwd-based heuristics.
    """
    result = {
        "session_id": session_id,
        "live": False,
        "pid": None,
        "tty": None,
        "terminal_app": None,
        "recently_written": False,
        "ambiguous": False,
        "match_count": 0,
    }
    if not session_id:
        return result

    # Recency check on the .jsonl file (for the "is actively being used" signal)
    jsonl_name = session_id + ".jsonl"
    recent = False
    if PROJECTS_ROOT.is_dir():
        now = time.time()
        for project_dir in PROJECTS_ROOT.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / jsonl_name
            if candidate.is_file():
                try:
                    if now - candidate.stat().st_mtime < 300:  # 5 min
                        recent = True
                except OSError:
                    pass
                break
    result["recently_written"] = recent

    # Primary lookup: session registry (authoritative)
    registry = _load_session_registry()
    entry = registry.get(session_id)
    if entry:
        pid = int(entry["pid"])
        result["pid"] = pid
        result["match_count"] = 1
        # Hydrate tty + terminal_app from the live pid
        try:
            ps_out = subprocess.run(
                ["ps", "-o", "tty=", "-p", str(pid)],
                capture_output=True, text=True, timeout=1,
            )
            tty = (ps_out.stdout or "").strip()
            if tty and tty != "??":
                result["tty"] = tty
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        term_app, _ = _proc_ancestor_terminal(pid)
        result["terminal_app"] = term_app
        result["live"] = True
        return result

    # Fallback: cwd-based matching (for older claude versions or missing registry)
    if not session_cwd:
        return result
    procs = find_live_claude_processes()
    matches = [p for p in procs if p["cwd"] == session_cwd]
    result["match_count"] = len(matches)
    if not matches:
        return result
    if len(matches) > 1:
        result["ambiguous"] = True
        return result
    match = matches[0]
    result["pid"] = match["pid"]
    result["tty"] = match["tty"]
    result["terminal_app"] = match["terminal_app"]
    if recent:
        result["live"] = True
    return result


def _preferred_terminal_app():
    """Pick a terminal to launch new sessions in.

    Prefers the terminal app that's hosting the newest running claude process,
    falling back to Terminal.app (which is always available on macOS).
    """
    procs = find_live_claude_processes()
    # Prefer known terminals
    for p in procs:
        if p.get("terminal_app") in _TERMINAL_APPS.values() or p.get("terminal_app") in ("Terminal", "iTerm2"):
            return p["terminal_app"]
    return "Terminal"


def _shell_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def _build_resume_command(session_id, cwd, cwd_exists):
    """Same logic as the frontend buildResumeCommand — keep them in sync."""
    if not cwd:
        return f"claude --resume {session_id}"
    q_cwd = _shell_quote(cwd)
    if cwd_exists:
        return f"cd {q_cwd} && claude --resume {session_id}"
    # Worktree recreation fallback
    m = re.search(r"/\.claude/worktrees/(.+)$", cwd)
    if m:
        branch = m.group(1)
        repo_root = cwd.split("/.claude/worktrees/")[0]
        q_repo = _shell_quote(repo_root)
        q_branch = _shell_quote(branch)
        return (
            f"(cd {q_repo} && git worktree add {q_cwd} {q_branch} 2>/dev/null "
            f"|| git worktree add {q_cwd} -b {q_branch} origin/main) "
            f"&& cd {q_cwd} && claude --resume {session_id}"
        )
    return f"cd {q_cwd} && claude --resume {session_id}"


def launch_terminal_for_session(session_id, cwd=None, terminal_app=None):
    """Open a new terminal window and run the resume command for this session.

    Returns {ok, terminal_app, command, error?}.
    """
    if not session_id:
        return {"ok": False, "error": "missing session_id"}
    if cwd is None:
        cwd = find_session_cwd(session_id)
    cwd_exists = bool(cwd and Path(cwd).is_dir())
    command = _build_resume_command(session_id, cwd, cwd_exists)
    target = terminal_app or _preferred_terminal_app()

    # AppleScript string needs the command embedded; escape backslashes and
    # double quotes for the AppleScript literal.
    def as_literal(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')
    cmd_lit = as_literal(command)

    # Use a human-readable name for the terminal tab.
    # Look up display_name from conversations, fall back to session name or ID prefix.
    rename_target = None
    try:
        convs = find_all_sessions() or []
        for c in convs:
            if c.get("session_id") == session_id:
                rename_target = c.get("display_name") or c.get("name")
                break
    except Exception:
        pass
    if not rename_target:
        rename_target = (session_id or "")[:12]
    # Sanitize for AppleScript (no quotes/backslashes)
    rename_target = rename_target.replace('"', '').replace('\\', '').replace("'", "")[:60]
    color = _pick_color_for_session(rename_target)
    if target == "iTerm2":
        script = f'''
        tell application "iTerm2"
          activate
          set newWin to (create window with default profile)
          tell current session of newWin
            write text "{cmd_lit}"
          end tell
        end tell
        delay 2.0
        tell application "iTerm2" to activate
        delay 0.3
        tell application "System Events" to keystroke "/rename {rename_target}"
        delay 0.25
        tell application "System Events" to key code 36
        delay 0.7
        tell application "iTerm2" to activate
        delay 0.2
        tell application "System Events" to keystroke "/color {color}"
        delay 0.25
        tell application "System Events" to key code 36
        return "ok"
        '''
    else:
        # Terminal.app: explicitly create a new window, hold onto it, and keep
        # it frontmost across the keystrokes. `do script` returns a tab whose
        # window we can reference.
        script = f'''
        set winId to 0
        tell application "Terminal"
          activate
          set newTab to do script "{cmd_lit}"
          set winId to id of window 1
        end tell
        delay 2.0
        tell application "Terminal"
          activate
          set frontmost of (first window whose id is winId) to true
        end tell
        delay 0.3
        tell application "System Events" to keystroke "/rename {rename_target}"
        delay 0.25
        tell application "System Events" to key code 36
        delay 0.7
        tell application "Terminal"
          activate
          set frontmost of (first window whose id is winId) to true
        end tell
        delay 0.2
        tell application "System Events" to keystroke "/color {color}"
        delay 0.25
        tell application "System Events" to key code 36
        return "ok"
        '''

    # Run the osascript in the background (captures stderr to a log for debugging).
    try:
        log_path = LOG_DIR / f"jump-{(session_id or 'x')[:8]}.log"
        lf = open(log_path, "w")
        subprocess.Popen(["osascript", "-e", script], stdout=lf, stderr=lf)
    except (FileNotFoundError, OSError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "terminal_app": target, "command": command}


def inject_input_via_keystroke(tty, terminal_app, text):
    """Focus the terminal tab for `tty`, then type `text` + Enter via System Events.

    This goes through the same event pipeline as real keyboard input, so
    Claude Code's TUI properly receives and processes the text (unlike raw
    TTY writes which bypass the input handler).
    """
    tty_short = tty.replace("/dev/", "")
    tty_full = "/dev/" + tty_short

    # Escape text for AppleScript string literal
    def as_lit(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')
    text_lit = as_lit(text)

    if terminal_app == "iTerm2":
        # iTerm2: find the session by tty, select it, then keystroke
        script = f'''
        tell application "iTerm2"
          set found to false
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              repeat with j from 1 to (count of tabs of w)
                try
                  set t to tab j of w
                  repeat with s in sessions of t
                    try
                      if tty of s is "{tty_full}" then
                        select w
                        tell w to select t
                        select s
                        set found to true
                        exit repeat
                      end if
                    end try
                  end repeat
                  if found then exit repeat
                end try
              end repeat
              if found then exit repeat
            end try
          end repeat
          if not found then return "notfound"
          activate
        end tell
        delay 0.15
        tell application "System Events"
          keystroke "{text_lit}"
          keystroke return
        end tell
        return "ok"
        '''
    else:
        # Terminal.app: find the tab by tty, focus it, then keystroke
        script = f'''
        tell application "Terminal"
          set foundWin to missing value
          set foundTab to missing value
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              repeat with j from 1 to (count of tabs of w)
                try
                  set t to tab j of w
                  if tty of t is "{tty_full}" then
                    set foundWin to w
                    set foundTab to t
                    exit repeat
                  end if
                end try
              end repeat
              if foundTab is not missing value then exit repeat
            end try
          end repeat
          if foundTab is missing value then return "notfound"
          set selected of foundTab to true
          try
            set index of foundWin to 1
          end try
          activate
        end tell
        delay 0.15
        tell application "System Events"
          keystroke "{text_lit}"
          keystroke return
        end tell
        return "ok"
        '''

    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"ok": False, "error": str(e)}
    result_str = (out.stdout or "").strip()
    if out.returncode != 0:
        return {"ok": False, "error": (out.stderr or "").strip() or "AppleScript failed"}
    if result_str == "notfound":
        return {"ok": False, "error": f"No {terminal_app} tab found for {tty_short}"}
    return {"ok": True, "tty": tty}


def focus_terminal_by_tty(tty, terminal_app):
    """Bring the terminal window/tab backing `tty` to the front.

    `tty` is like "ttys008". `terminal_app` is the friendly name from
    _TERMINAL_APPS. Returns {ok, error}.
    """
    if not tty or tty == "??":
        return {"ok": False, "error": "No tty available"}
    if not terminal_app:
        return {"ok": False, "error": "Unknown terminal app"}

    tty_short = tty.replace("/dev/", "")
    tty_full = "/dev/" + tty_short

    if terminal_app == "iTerm2":
        # Defensive iteration: phantom/minimized windows can throw errors and
        # abort the whole loop. Use index-based iteration with try/on-error.
        script = f'''
        tell application "iTerm2"
          set found to false
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              set tabCount to count of tabs of w
              repeat with j from 1 to tabCount
                try
                  set t to tab j of w
                  set sessList to sessions of t
                  repeat with s in sessList
                    try
                      if tty of s is "{tty_full}" then
                        select w
                        tell w to select t
                        select s
                        set found to true
                        exit repeat
                      end if
                    end try
                  end repeat
                  if found then exit repeat
                end try
              end repeat
              if found then exit repeat
            end try
          end repeat
          if found then
            activate
            return "ok"
          else
            return "notfound"
          end if
        end tell
        '''
    elif terminal_app == "Terminal":
        # Defensive iteration: Terminal.app can have phantom windows whose
        # `tabs` accessor throws, which would abort a naive `repeat with w in windows`.
        # We use index-based loops with try/on-error to skip them.
        script = f'''
        tell application "Terminal"
          set foundWin to missing value
          set foundTab to missing value
          set winCount to count of windows
          repeat with i from 1 to winCount
            try
              set w to window i
              set tabCount to count of tabs of w
              repeat with j from 1 to tabCount
                try
                  set t to tab j of w
                  if tty of t is "{tty_full}" then
                    set foundWin to w
                    set foundTab to t
                    exit repeat
                  end if
                end try
              end repeat
              if foundTab is not missing value then exit repeat
            end try
          end repeat
          if foundTab is not missing value then
            set selected of foundTab to true
            try
              set index of foundWin to 1
            end try
            activate
            return "ok"
          else
            return "notfound"
          end if
        end tell
        '''
    elif terminal_app == "Ghostty":
        # Ghostty doesn't expose tab-level AppleScript; best we can do is activate it
        script = 'tell application "Ghostty" to activate\nreturn "ok"'
    else:
        # Generic fallback: just activate the app
        script = f'tell application "{terminal_app}" to activate\nreturn "ok"'

    try:
        out = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"ok": False, "error": str(e)}
    result = (out.stdout or "").strip()
    if out.returncode != 0:
        return {"ok": False, "error": (out.stderr or "").strip() or "AppleScript failed"}
    if result == "notfound":
        return {"ok": False, "error": f"No {terminal_app} tab found for {tty_short}"}
    return {"ok": True, "terminal_app": terminal_app}


def find_session_cwd(session_id):
    """Locate the .jsonl for a session_id across ~/.claude/projects/*/ and return its cwd.

    Sessions may have been run in a worktree or other directory; `claude --resume`
    only finds them when run from the original cwd, so we need to `cd` there first.
    """
    if not session_id:
        return None
    if session_id in _session_cwd_cache:
        return _session_cwd_cache[session_id]
    if not PROJECTS_ROOT.is_dir():
        return None

    jsonl_name = session_id + ".jsonl"
    for project_dir in PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / jsonl_name
        if not candidate.is_file():
            continue
        # Read until we find the first event with a `cwd` field
        try:
            with open(candidate, "r") as f:
                for i, line in enumerate(f):
                    if i >= 40:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = ev.get("cwd")
                    if cwd:
                        _session_cwd_cache[session_id] = cwd
                        return cwd
        except (OSError, UnicodeDecodeError):
            continue
        # Fallback: decode project dir name (replace - with /) — lossy but better than nothing
        decoded = "/" + project_dir.name.lstrip("-").replace("-", "/")
        _session_cwd_cache[session_id] = decoded
        return decoded
    return None


def _extract_spawn_meta(path):
    """Extract spawn_meta from the first few lines of a log file."""
    try:
        with open(path, "r") as f:
            for i, line in enumerate(f):
                if i >= 5:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "spawn_meta":
                    return ev
    except (OSError, UnicodeDecodeError):
        pass
    return None


_issue_titles_cache = {}
_issue_titles_cache_ts = 0

# Per-issue state map: {number_str: {'state': 'OPEN'|'CLOSED', 'labels': [..], 'title': ..}}
_issue_state_cache = {}
_issue_state_cache_ts = 0


_desktop_meta_cache = {}
_desktop_meta_cache_mtime = 0


def _load_desktop_app_metadata():
    """Read the Claude desktop app's per-session metadata overlay.

    The desktop app stores session metadata at
      ~/Library/Application Support/Claude/claude-code-sessions/<org>/<ws>/local_<sid>.json
    Each file has `cliSessionId` linking back to the CLI's .jsonl, plus
    human-friendly fields (title, model, cwd) the desktop UI surfaces.

    Returns {cliSessionId: {title, model, cwd, is_archived}}.
    Re-scans only when the root directory mtime changes; cheap enough
    to call on every request.
    """
    global _desktop_meta_cache, _desktop_meta_cache_mtime
    root = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
    if not root.is_dir():
        return {}
    try:
        mtime = root.stat().st_mtime
    except OSError:
        return _desktop_meta_cache
    if mtime == _desktop_meta_cache_mtime and _desktop_meta_cache:
        return _desktop_meta_cache
    out = {}
    try:
        for path in root.glob("*/*/local_*.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            cli_sid = data.get("cliSessionId")
            if not cli_sid:
                continue
            out[cli_sid] = {
                "title": data.get("title") or None,
                "model": data.get("model") or None,
                "cwd": data.get("cwd") or None,
                "is_archived": bool(data.get("isArchived")),
                "last_activity_at": data.get("lastActivityAt") or None,
            }
    except OSError:
        pass
    _desktop_meta_cache = out
    _desktop_meta_cache_mtime = mtime
    return out


def _fetch_issue_states():
    """Bulk-fetch state+labels+title for all issues. Cached 5 min."""
    global _issue_state_cache, _issue_state_cache_ts
    if time.time() - _issue_state_cache_ts < 300 and _issue_state_cache:
        return _issue_state_cache
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--state", "all", "--limit", "500",
             "--json", "number,title,state,labels"],
            capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
        )
        if out.returncode == 0:
            issues = json.loads(out.stdout)
            _issue_state_cache = {
                str(i["number"]): {
                    "state": i.get("state") or "OPEN",
                    "labels": [l.get("name", "") for l in (i.get("labels") or [])],
                    "title": _strip_title_prefix(i.get("title", "")),
                }
                for i in issues
            }
            _issue_state_cache_ts = time.time()
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return _issue_state_cache

# Backlog: full issue data (labels, body) for open issues
_backlog_issues_cache = []
_backlog_issues_cache_ts = 0


def _fetch_issue_titles():
    """Bulk-fetch GitHub issue titles. Cached for 5 minutes."""
    global _issue_titles_cache, _issue_titles_cache_ts
    if time.time() - _issue_titles_cache_ts < 300 and _issue_titles_cache:
        return _issue_titles_cache
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--state", "all", "--limit", "200",
             "--json", "number,title"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        if out.returncode == 0:
            issues = json.loads(out.stdout)
            _issue_titles_cache = {
                str(i["number"]): _strip_title_prefix(i["title"])
                for i in issues
            }
            _issue_titles_cache_ts = time.time()
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return _issue_titles_cache


def _fetch_backlog_issues():
    """Fetch open + recently-closed GitHub issues with labels and body.
    Cached 5 minutes. Closed issues get a `state_reason` field so the UI
    can route them (completed -> Verified, not planned -> Archived).
    """
    global _backlog_issues_cache, _backlog_issues_cache_ts
    if time.time() - _backlog_issues_cache_ts < 300 and _backlog_issues_cache is not None:
        return _backlog_issues_cache
    merged = []
    try:
        open_out = subprocess.run(
            ["gh", "issue", "list", "--state", "open", "--limit", "100",
             "--json", "number,title,labels,body,createdAt,updatedAt,state,stateReason"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        if open_out.returncode == 0:
            merged.extend(json.loads(open_out.stdout))
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        closed_out = subprocess.run(
            ["gh", "issue", "list", "--state", "closed", "--limit", "60",
             "--json", "number,title,labels,body,createdAt,updatedAt,closedAt,state,stateReason"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        if closed_out.returncode == 0:
            merged.extend(json.loads(closed_out.stdout))
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    if merged:
        _backlog_issues_cache = merged
        _backlog_issues_cache_ts = time.time()
    return _backlog_issues_cache or []


def _parse_todo_md():
    """Parse TODO.md for unchecked items (- [ ] lines)."""
    todo_path = REPO_ROOT / "TODO.md"
    items = []
    try:
        with open(todo_path, "r") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("- [ ]"):
                    text = stripped[5:].strip()
                    if text:
                        items.append(text)
    except (OSError, UnicodeDecodeError):
        pass
    return items


def _parse_parking_lot_md():
    """Parse PARKING_LOT.md for `## heading` items; body = text until the next
    heading or `---` separator. Returns [{title, body}] in file order."""
    # Case-insensitive filename match for the two common spellings
    candidates = [REPO_ROOT / "PARKING_LOT.md", REPO_ROOT / "parking-lot.md", REPO_ROOT / "parking_lot.md"]
    path = next((p for p in candidates if p.is_file()), None)
    if not path:
        return []
    items = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    current_title = None
    current_body = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_title:
                items.append({"title": current_title, "body": "\n".join(current_body).strip()})
            current_title = line[3:].strip()
            current_body = []
            continue
        # `---` is a section separator — flush the current item but don't start a new one
        if line.strip() == "---":
            if current_title:
                items.append({"title": current_title, "body": "\n".join(current_body).strip()})
                current_title = None
                current_body = []
            continue
        if current_title is not None:
            current_body.append(line)
    if current_title:
        items.append({"title": current_title, "body": "\n".join(current_body).strip()})
    return items


def find_backlog_items():
    """Return backlog cards from GitHub issues + TODO.md."""
    items = []

    # Source 1: GitHub Issues
    for issue in _fetch_backlog_issues():
        number = issue.get("number", 0)
        title = issue.get("title", "")
        body = issue.get("body", "") or ""
        labels = [l.get("name", "") for l in (issue.get("labels") or [])]
        # Parse createdAt ISO 8601 → unix timestamp
        created_ts = 0
        created_at = issue.get("createdAt", "")
        if created_at:
            try:
                from datetime import datetime, timezone
                # Format: "2026-04-12T05:39:47Z" — UTC
                dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                created_ts = dt.timestamp()
            except (ValueError, ImportError):
                pass
        state = (issue.get("state") or "OPEN").upper()
        reason = (issue.get("stateReason") or "").upper()  # COMPLETED, NOT_PLANNED, DUPLICATE, ""
        items.append({
            "id": f"backlog-issue-{number}",
            "session_id": f"backlog-issue-{number}",
            "display_name": f"#{number}: {title}",
            "first_message": body[:200],
            "source": "backlog",
            "backlog_type": "github",
            "issue_number": str(number),
            "issue_labels": labels,
            "issue_created_at": created_at,
            "issue_state": state,
            "issue_state_reason": reason,
            "org": _detect_issue_org(body),
            "modified": created_ts,
            "size": 0,
            "branch": "",
            "is_live": False,
            "archived": False,
            "verified": False,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "sidecar_status": None,
            "sidecar_tool": None,
            "sidecar_file": None,
            "sidecar_has_writes": False,
            "sidecar_ts": 0,
            "name_overridden": False,
        })

    # Source 2: TODO.md
    for i, text in enumerate(_parse_todo_md()):
        items.append({
            "id": f"backlog-todo-{i}",
            "session_id": f"backlog-todo-{i}",
            "display_name": text[:80],
            "first_message": text,
            "source": "backlog",
            "backlog_type": "todo",
            "issue_number": "",
            "issue_labels": [],
            "modified": 0,
            "size": 0,
            "branch": "",
            "is_live": False,
            "archived": False,
            "verified": False,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "sidecar_status": None,
            "sidecar_tool": None,
            "sidecar_file": None,
            "sidecar_has_writes": False,
            "sidecar_ts": 0,
            "name_overridden": False,
        })

    # Source 3: PARKING_LOT.md — richer items (heading + body)
    for i, it in enumerate(_parse_parking_lot_md()):
        title = it["title"]
        body = it["body"]
        items.append({
            "id": f"backlog-parking-{i}",
            "session_id": f"backlog-parking-{i}",
            "display_name": title[:120],
            "first_message": (title + "\n\n" + body) if body else title,
            "source": "backlog",
            "backlog_type": "parking",
            "issue_number": "",
            "issue_labels": [],
            "modified": 0,
            "size": 0,
            "branch": "",
            "is_live": False,
            "archived": False,
            "verified": False,
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "sidecar_status": None,
            "sidecar_tool": None,
            "sidecar_file": None,
            "sidecar_has_writes": False,
            "sidecar_ts": 0,
            "name_overridden": False,
        })

    return items


def find_log_files():
    """Return list of {issue, path, size, modified, session_id} dicts."""
    logs = []
    pattern = re.compile(r"issue-(\d+)\.log$")
    titles = _fetch_issue_titles()

    for directory in [LOG_DIR, FALLBACK_DIR]:
        if not directory.is_dir():
            continue
        for f in directory.iterdir():
            m = pattern.search(f.name)
            if m and f.is_file():
                issue = m.group(1)
                # Don't duplicate if found in both locations
                if any(l["issue"] == issue for l in logs):
                    continue
                sid = extract_session_id(f)
                meta = _extract_spawn_meta(f)
                mode = (meta or {}).get("mode", "worktree")
                # Inline spawns always run in REPO_ROOT
                if mode == "inline":
                    cwd = str(REPO_ROOT)
                else:
                    cwd = find_session_cwd(sid)
                gh_title = titles.get(issue, "")
                logs.append({
                    "issue": issue,
                    "issue_title": gh_title or (meta or {}).get("issue_title", ""),
                    "mode": mode,
                    "path": str(f),
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                    "modified_human": time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime)
                    ),
                    "session_id": sid,
                    "session_cwd": cwd,
                    "session_cwd_exists": bool(cwd and Path(cwd).is_dir()),
                })

    logs.sort(key=lambda x: x["modified"], reverse=True)
    return logs


def parse_log_file(path, after_line=0):
    """Parse a stream-json log file into structured events."""
    events = []
    line_num = 0

    try:
        with open(path, "r") as f:
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

                parsed = parse_event(ev, line_num)
                if parsed:
                    events.append(parsed)
    except FileNotFoundError:
        pass

    return {"events": events, "last_line": line_num}


def parse_event(ev, line_num):
    """Parse a single JSON event into a display-friendly dict."""
    t = ev.get("type", "")

    if t == "spawn_meta":
        # Synthetic metadata from inline issue spawns — skip display
        return None

    if t == "system":
        subtype = ev.get("subtype", "")
        model = ev.get("model", "")
        session = ev.get("session_id", "")[:12]
        return {
            "line": line_num,
            "type": "system",
            "subtype": subtype,
            "model": model,
            "session": session,
        }

    if t == "assistant":
        blocks = []
        for block in ev.get("message", {}).get("content", []):
            btype = block.get("type", "")
            if btype == "tool_use":
                inp = block.get("input", {})
                name = block.get("name", "?")
                detail = (
                    inp.get("file_path")
                    or inp.get("pattern")
                    or inp.get("command", "")
                    or inp.get("query", "")
                    or inp.get("prompt", "")
                    or ""
                )
                # No truncation — full detail shown in web UI
                blocks.append({"kind": "tool_use", "name": name, "detail": detail})
            elif btype == "text":
                txt = block.get("text", "").strip()
                if txt:
                    blocks.append({"kind": "text", "text": txt})
            elif btype == "thinking":
                thinking = block.get("thinking", "").strip()
                if thinking:
                    blocks.append({"kind": "thinking", "text": thinking})

        if blocks:
            return {"line": line_num, "type": "assistant", "blocks": blocks}

    if t == "user":
        content = ev.get("message", {}).get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    return {"line": line_num, "type": "tool_result"}
            # Check for human text
            texts = [
                item.get("text", "").strip()
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            if texts:
                return {
                    "line": line_num,
                    "type": "user_text",
                    "text": "\n".join(t for t in texts if t),
                }
        elif isinstance(content, str) and content.strip():
            return {"line": line_num, "type": "user_text", "text": content.strip()}

    if t == "result":
        cost = ev.get("cost_usd", "?")
        dur = ev.get("duration_ms", "?")
        r = ev.get("result")
        if isinstance(r, dict):
            cost = r.get("cost_usd", cost)
            dur = r.get("duration_ms", dur)
        return {
            "line": line_num,
            "type": "result",
            "cost_usd": cost,
            "duration_ms": dur,
        }

    return None


# ---------------------------------------------------------------------------
# Conversation parsing (Claude Code interactive sessions)
# ---------------------------------------------------------------------------

def _safe_parse_message(msg):
    """Parse a message field that may be a dict or a Python repr string."""
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, str):
        try:
            return json.loads(msg)
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            return ast.literal_eval(msg)
        except (ValueError, SyntaxError):
            pass
    return {}


def _extract_text_from_content(content):
    """Extract plain text from a message content field (string or list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text", "").strip()
                if t:
                    texts.append(t)
            elif isinstance(item, str):
                texts.append(item.strip())
        return "\n".join(texts)
    return ""


def find_conversations():
    """Return list of conversation metadata dicts, newest first."""
    conversations = []
    if not CONVERSATIONS_DIR.is_dir():
        return conversations
    name_overrides = _load_session_name_overrides()
    archived_set = set(_load_archived_conversations())
    verified_set = set(_load_verified_conversations())

    for f in CONVERSATIONS_DIR.iterdir():
        if not f.name.endswith(".jsonl") or not f.is_file():
            continue
        try:
            stat = f.stat()
        except OSError:
            continue

        # Peek at first 20 lines to extract metadata
        session_id = None
        timestamp = None
        git_branch = None
        first_message = None

        try:
            with open(f, "r") as fh:
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

                    ev_type = ev.get("type", "")

                    if ev_type in ("file-history-snapshot", "progress", "system"):
                        continue

                    if ev_type == "user":
                        if ev.get("isMeta"):
                            continue
                        if not session_id:
                            session_id = ev.get("sessionId", "")
                        if not timestamp:
                            timestamp = ev.get("timestamp", "")
                        if not git_branch:
                            git_branch = ev.get("gitBranch", "")
                        if not first_message:
                            msg = _safe_parse_message(ev.get("message", {}))
                            text = _extract_text_from_content(msg.get("content", ""))
                            if text and not text.lstrip().startswith("<command-name>"):
                                first_message = text

                    if ev_type == "assistant" and not session_id:
                        session_id = ev.get("sessionId", "")

        except (OSError, UnicodeDecodeError):
            continue

        conv_id = f.name[:-6]  # remove .jsonl
        sid = session_id or conv_id
        cwd = find_session_cwd(sid)
        tail_meta = _extract_tail_meta(f)
        override = name_overrides.get(sid) or name_overrides.get(conv_id)
        # Display value priority: authoritative jsonl > side-car override > None
        # (flipped: jsonl is authoritative because claude /rename may have
        # updated the name after our write-through)
        display_name = (
            tail_meta.get("custom_title")
            or tail_meta.get("agent_name")
            or override
            or None
        )
        # name_overridden means "user touched the name from the log viewer"
        # (used for teal visual marker). Decoupled from display value.
        name_overridden = bool(override)
        conversations.append({
            "id": conv_id,
            "session_id": sid,
            "timestamp": timestamp or "",
            "branch": git_branch or "",
            "first_message": (first_message or "")[:200],
            "display_name": display_name,
            "name_overridden": name_overridden,
            "last_prompt": (tail_meta.get("last_prompt") or "")[:200],
            "size": stat.st_size,
            # Use last meaningful event timestamp when available; fall back to mtime.
            # This prevents admin writes (custom-title etc.) from bumping "modified".
            "modified": tail_meta.get("last_meaningful_ts") or stat.st_mtime,
            "modified_human": time.strftime(
                "%Y-%m-%d %H:%M",
                time.localtime(tail_meta.get("last_meaningful_ts") or stat.st_mtime),
            ),
            "session_cwd": cwd,
            "session_cwd_exists": bool(cwd and Path(cwd).is_dir()),
            # Session signals
            "has_edit": tail_meta.get("has_edit", False),
            "has_commit": tail_meta.get("has_commit", False),
            "has_push": tail_meta.get("has_push", False),
            "last_edit_pos": tail_meta.get("last_edit_pos", 0),
            "last_commit_pos": tail_meta.get("last_commit_pos", 0),
            "last_push_pos": tail_meta.get("last_push_pos", 0),
            "last_event_type": tail_meta.get("last_event_type"),
            "pending_tool": tail_meta.get("pending_tool"),
            "pending_file": tail_meta.get("pending_file"),
            "last_assistant_text": tail_meta.get("last_assistant_text"),
            "tail_issue_number": tail_meta.get("tail_issue_number"),
            "session_state": _parse_session_state(tail_meta.get("last_assistant_text")),
            "archived": sid in archived_set,
            "verified": sid in verified_set,
        })

    # Primary sort: most recently modified (= latest response) first
    conversations.sort(key=lambda x: x["modified"], reverse=True)
    # Apply custom order (if any): listed sessions first in saved order,
    # unlisted (e.g. newly-created) sessions after, by mtime desc.
    order = _load_conversation_order()
    if order:
        by_sid = {c["session_id"]: c for c in conversations}
        by_id = {c["id"]: c for c in conversations}
        ordered = []
        seen = set()
        for key in order:
            c = by_sid.get(key) or by_id.get(key)
            if c and c["session_id"] not in seen:
                ordered.append(c)
                seen.add(c["session_id"])
        for c in conversations:
            if c["session_id"] not in seen:
                ordered.append(c)
        conversations = ordered
    return conversations


def _read_sidecar_state(session_id):
    """Read sidecar state for a session. Returns dict or None."""
    path = SIDECAR_STATE_DIR / f"{session_id}.json"
    try:
        if path.exists():
            return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _cleanup_stale_sidecars(live_session_ids):
    """Remove sidecar files for sessions that are no longer live."""
    if not SIDECAR_STATE_DIR.is_dir():
        return
    for f in SIDECAR_STATE_DIR.iterdir():
        if not f.is_file():
            continue
        name = f.stem
        # Strip _writes suffix to get session_id
        sid = name[:-7] if name.endswith("_writes") else name
        if sid not in live_session_ids:
            try:
                f.unlink()
            except OSError:
                pass


def _add_sidecar_fields(entry):
    """Add sidecar fields to a session entry, reading state if available."""
    sid = entry.get("session_id", "")
    sc = _read_sidecar_state(sid) if entry.get("is_live") else None
    entry["sidecar_status"] = sc.get("status") if sc else None
    entry["sidecar_tool"] = sc.get("tool") if sc else None
    entry["sidecar_file"] = sc.get("file") if sc else None
    entry["sidecar_has_writes"] = sc.get("has_writes", False) if sc else False
    entry["sidecar_ts"] = sc.get("timestamp", 0) if sc else 0


def find_all_sessions():
    """Return a unified list of sessions from both conversations and issue logs.

    Each entry has a 'source' field: 'interactive' or 'watcher'.
    Conversations come from find_conversations(), issue logs from find_log_files().
    Merged, custom-ordered, and sorted by mtime.
    """
    global _SESSION_ISSUES_CACHE
    _SESSION_ISSUES_CACHE = _load_session_issues()
    # Get conversations and tag them
    conversations = find_conversations()
    # Load session registry to mark which sessions have a running process
    registry = _load_session_registry()
    live_sids = set(registry.keys())
    spawned_pids = {s["pid"] for s in _spawned_sessions if s["proc"].poll() is None}
    for c in conversations:
        c["source"] = "interactive"
        c["is_live"] = c["session_id"] in live_sids
        reg_pid = (registry.get(c["session_id"]) or {}).get("pid")
        c["spawn_pid"] = reg_pid if reg_pid in spawned_pids else None

    # Get issue logs and transform to conversation-like shape.
    # Deduplicate: if a watcher log's session_id matches an interactive session,
    # skip the watcher entry (the interactive one has richer signal data).
    logs = find_log_files()
    interactive_sids = {c["session_id"] for c in conversations}
    for log in logs:
        if log.get("session_id") and log["session_id"] in interactive_sids:
            continue
        issue = log["issue"]
        issue_title = log.get("issue_title", "")
        display = f"#{issue}: {issue_title}" if issue_title else f"Issue #{issue}"
        conversations.append({
            "id": f"issue-{issue}",
            "session_id": log.get("session_id") or f"issue-{issue}",
            "timestamp": "",
            "branch": "",
            "first_message": "",
            "display_name": display,
            "name_overridden": False,
            "last_prompt": "",
            "size": log["size"],
            "modified": log["modified"],
            "modified_human": log["modified_human"],
            "session_cwd": log.get("session_cwd"),
            "session_cwd_exists": log.get("session_cwd_exists", False),
            "source": "watcher",
            "issue_number": issue,
            "issue_mode": log.get("mode", "worktree"),
            "is_live": (log.get("session_id") or "") in live_sids,
            # Session signals — empty for watcher logs
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "archived": False,
            "verified": False,
        })

    # Add pkood agents
    for agent in find_pkood_agents():
        conversations.append(agent)

    # Add backlog items (GitHub issues + TODO.md), skipping those with active sessions
    _issue_pattern = re.compile(r"(?:issue|fix)[/-](\d+)")
    active_issue_nums = set()
    for c in conversations:
        # Check branch for issue-N or fix/N patterns
        branch = c.get("branch", "") or ""
        for m in _issue_pattern.finditer(branch):
            active_issue_nums.add(m.group(1))
        # Check display_name for #N or issue-N patterns
        dname = c.get("display_name", "") or ""
        for m in re.finditer(r"#(\d+)", dname):
            active_issue_nums.add(m.group(1))
        for m in _issue_pattern.finditer(dname):
            active_issue_nums.add(m.group(1))
        # Also check first_message (the prompt) for #N
        fm = c.get("first_message", "") or ""
        for m in re.finditer(r"#(\d+)", fm):
            active_issue_nums.add(m.group(1))
        for m in _issue_pattern.finditer(fm):
            active_issue_nums.add(m.group(1))
    for item in find_backlog_items():
        inum = item.get("issue_number", "")
        if inum and inum in active_issue_nums:
            continue  # Active session already covers this issue
        conversations.append(item)

    # Sidecar: clean up stale files, then enrich every entry
    _cleanup_stale_sidecars(live_sids)
    issue_states = _fetch_issue_states()
    desktop_meta = _load_desktop_app_metadata()
    for c in conversations:
        _add_sidecar_fields(c)
        # Desktop-app metadata decoration: use human-friendly title if present,
        # and flag the session as having been touched by the desktop app.
        dm = desktop_meta.get(c.get("session_id"))
        if dm:
            c["desktop_app"] = True
            if dm.get("title") and not c.get("name_overridden"):
                # Only replace auto-slug / CLI-generated names; never overwrite a user rename.
                raw_name = (c.get("display_name") or "").strip()
                looks_like_slug = bool(re.match(r"^[a-z0-9\-]+$", raw_name))
                if not raw_name or looks_like_slug or raw_name.lower().startswith("issue-"):
                    c["display_name"] = dm["title"]
        # Link to GitHub issue (from side-car mapping or heuristic)
        if c.get("source") != "backlog":
            c["linked_issue"] = _detect_issue_number_for_session(c)
            # If linked to a real issue, enrich display_name with the issue title
            if c.get("linked_issue"):
                titles = _fetch_issue_titles()
                title = titles.get(c["linked_issue"])
                if title:
                    raw_name = (c.get("display_name") or "").strip().lower()
                    # Replace generic slugs like "issue-110" with the real title
                    if not raw_name or raw_name == f"issue-{c['linked_issue']}" or raw_name.startswith("fix-github-issue"):
                        c["display_name"] = f"#{c['linked_issue']}: {title}"
        # Attach GitHub state/labels if a linked issue is known
        inum = c.get("linked_issue") or c.get("issue_number")
        if inum:
            st = issue_states.get(str(inum))
            if st:
                c["gh_state"] = st["state"]  # "OPEN" / "CLOSED"
                c["gh_labels"] = st["labels"]
                c["gh_in_progress"] = "claude-in-progress" in st["labels"]
        # Backlog cards: mark WIP from their own labels
        if c.get("source") == "backlog":
            c["gh_state"] = "OPEN"
            c["gh_in_progress"] = "claude-in-progress" in (c.get("issue_labels") or [])

    # Sort by mtime desc, then apply custom order
    conversations.sort(key=lambda x: x["modified"], reverse=True)
    order = _load_conversation_order()
    if order:
        by_sid = {c["session_id"]: c for c in conversations}
        by_id = {c["id"]: c for c in conversations}
        ordered = []
        seen = set()
        for key in order:
            c = by_sid.get(key) or by_id.get(key)
            if c and c["session_id"] not in seen:
                ordered.append(c)
                seen.add(c["session_id"])
        for c in conversations:
            if c["session_id"] not in seen:
                ordered.append(c)
        conversations = ordered

    # Auto-verify: sessions with has_push linked to closed GH issues get verified.
    # Runs inline (cheap — just reads cached issue states + verified list).
    try:
        auto_verify_closed_issues()
    except Exception:
        pass

    return conversations


def parse_conversation(conversation_id, after_line=0):
    """Parse a conversation JSONL file into structured events."""
    filepath = CONVERSATIONS_DIR / (conversation_id + ".jsonl")
    events = []
    line_num = 0

    try:
        with open(filepath, "r") as f:
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

                parsed = _parse_conversation_event(ev, line_num)
                if parsed:
                    events.append(parsed)
    except FileNotFoundError:
        pass

    return {"events": events, "last_line": line_num}


def _parse_conversation_event(ev, line_num):
    """Parse a single conversation JSONL event."""
    ev_type = ev.get("type", "")

    # Skip non-message types
    if ev_type in ("file-history-snapshot", "progress", "system"):
        return None

    if ev_type == "user":
        if ev.get("isMeta"):
            return None
        msg = _safe_parse_message(ev.get("message", {}))
        content = msg.get("content", "")
        text = _extract_text_from_content(content)
        if text and text.lstrip().startswith("<command-name>"):
            return None
        if text:
            return {"line": line_num, "type": "user_text", "text": text}
        # Check for tool results in content list
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    return {"line": line_num, "type": "tool_result"}
        return None

    if ev_type == "assistant":
        msg = _safe_parse_message(ev.get("message", {}))
        blocks = []
        for block in msg.get("content", []):
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                inp = block.get("input", {})
                name = block.get("name", "?")
                detail = (
                    inp.get("file_path")
                    or inp.get("pattern")
                    or inp.get("command", "")
                    or inp.get("query", "")
                    or inp.get("prompt", "")
                    or ""
                )
                if isinstance(detail, str) and len(detail) > 200:
                    detail = detail[:200] + "..."
                blocks.append({"kind": "tool_use", "name": name, "detail": detail})
            elif btype == "text":
                txt = block.get("text", "").strip()
                if txt:
                    blocks.append({"kind": "text", "text": txt})
            elif btype == "thinking":
                thinking = block.get("thinking", "").strip()
                if thinking:
                    preview = thinking[:300] + ("..." if len(thinking) > 300 else "")
                    blocks.append({"kind": "thinking", "text": preview})

        if blocks:
            return {"line": line_num, "type": "assistant", "blocks": blocks}

    if ev_type == "result":
        cost = ev.get("cost_usd", "?")
        dur = ev.get("duration_ms", "?")
        r = ev.get("result")
        if isinstance(r, dict):
            cost = r.get("cost_usd", cost)
            dur = r.get("duration_ms", dur)
        return {
            "line": line_num,
            "type": "result",
            "cost_usd": cost,
            "duration_ms": dur,
        }

    return None


# ---------------------------------------------------------------------------
# Watcher process management
# ---------------------------------------------------------------------------

_watcher_lock = threading.Lock()


def _reader_thread(proc):
    """Background thread that reads watcher stdout line-by-line."""
    global _watcher_output_lines
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            with _watcher_lock:
                _watcher_output_lines.append(line)
                if len(_watcher_output_lines) > 500:
                    _watcher_output_lines = _watcher_output_lines[-500:]
    except (ValueError, OSError):
        pass  # pipe closed


def _find_zombie_watchers():
    """Find any existing watcher processes not managed by us."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "claude-issue-watcher\\.sh"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
            # Exclude our own managed process
            with _watcher_lock:
                our_pid = _watcher_proc.pid if _watcher_proc and _watcher_proc.poll() is None else None
            return [p for p in pids if p != our_pid]
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return []


def _kill_zombie_watchers():
    """Kill any orphaned watcher processes."""
    zombies = _find_zombie_watchers()
    for pid in zombies:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
    return zombies


def watcher_status():
    """Return watcher status dict."""
    global _watcher_proc
    zombies = _find_zombie_watchers()
    with _watcher_lock:
        if _watcher_proc is not None:
            ret = _watcher_proc.poll()
            if ret is not None:
                _watcher_proc = None
                return {"running": False, "exit_code": ret, "zombies": zombies, "output": _watcher_output_lines[-50:]}
            return {"running": True, "pid": _watcher_proc.pid, "zombies": zombies, "output": _watcher_output_lines[-50:]}
        # Not managed by us, but zombies exist
        if zombies:
            return {"running": False, "zombies": zombies, "output": _watcher_output_lines[-50:]}
        return {"running": False, "output": _watcher_output_lines[-50:]}


def watcher_start():
    """Start the watcher script as a subprocess."""
    global _watcher_proc, _watcher_output_lines

    # Kill any orphaned watchers first
    zombies = _kill_zombie_watchers()

    with _watcher_lock:
        if _watcher_proc is not None and _watcher_proc.poll() is None:
            return {"error": "Watcher is already running", "running": True, "pid": _watcher_proc.pid, "output": _watcher_output_lines[-50:]}

    if not WATCHER_SCRIPT.exists():
        return {"error": f"Watcher script not found: {WATCHER_SCRIPT}"}

    with _watcher_lock:
        _watcher_output_lines = []
        _watcher_proc = subprocess.Popen(
            [str(WATCHER_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(REPO_ROOT),
            preexec_fn=os.setsid,
        )
        # Start background reader so stdout never blocks
        t = threading.Thread(target=_reader_thread, args=(_watcher_proc,), daemon=True)
        t.start()

    return {"started": True, **watcher_status()}


def watcher_stop():
    """Stop the watcher subprocess."""
    global _watcher_proc
    with _watcher_lock:
        if _watcher_proc is None or _watcher_proc.poll() is not None:
            _watcher_proc = None
            return {"error": "Watcher is not running"}
        proc = _watcher_proc

    # Kill the entire process group (watcher + any children like claude CLI)
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)

    with _watcher_lock:
        _watcher_proc = None
    return {"stopped": True}


# ---------------------------------------------------------------------------
# Spawned headless Claude sessions
# ---------------------------------------------------------------------------

def _slugify(text, max_len=40):
    """Turn a prompt into a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def spawn_session(prompt, name=None):
    """Spawn a headless Claude Code session and return tracking info."""
    # Always slugify — name may come from firstSentence(body) and contain
    # filesystem-hostile chars like quotes, colons, slashes.
    session_name = _slugify(name or prompt)
    if not session_name:
        session_name = "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-{session_name}-{timestamp}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_filename

    cmd = [
        "claude", "-p", "--verbose",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--model", "opus",
        "--dangerously-skip-permissions",
        "--name", session_name,
    ]

    log_fh = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )

    # Write the initial prompt as the first stream-json user message.
    # Note: headless `claude -p` doesn't support TUI slash commands like /rename
    # or /color — they're treated as unknown skills. Tab naming/coloring only
    # happens when the user "jumps" into the TUI (see launch_terminal_for_session).
    _write_stream_json_user_message(proc, prompt)

    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
    }
    _spawned_sessions.append(entry)

    return {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path)}


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


def _write_stream_json_user_message(proc, text):
    """Emit a stream-json user message to a running headless claude."""
    msg = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }
    line = json.dumps(msg) + "\n"
    try:
        proc.stdin.write(line.encode("utf-8"))
        proc.stdin.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def inject_into_spawned(pid, text):
    """Send a follow-up user message to a previously spawned session."""
    for s in _spawned_sessions:
        if s["pid"] == pid:
            if s["proc"].poll() is not None:
                return {"ok": False, "error": "process exited"}
            ok = _write_stream_json_user_message(s["proc"], text)
            return {"ok": ok, "pid": pid}
    return {"ok": False, "error": "unknown pid (not spawned by this server)"}


def resume_session_headless(session_id, text):
    """Resume a dormant session headlessly (`claude --resume`) and send text.

    If we already resumed this session and the process is still alive, reuse it.
    """
    # Reuse existing resumed process
    for s in _spawned_sessions:
        if s.get("resumed_sid") == session_id and s["proc"].poll() is None:
            ok = _write_stream_json_user_message(s["proc"], text)
            return {"ok": ok, "pid": s["pid"], "resumed": True, "reused": True}

    cwd = find_session_cwd(session_id) or str(REPO_ROOT)
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-{session_id[:8]}-{timestamp}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_filename

    cmd = [
        "claude", "-p", "--verbose",
        "--resume", session_id,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
    ]

    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
    except FileNotFoundError:
        log_fh.close()
        return {"ok": False, "error": "claude CLI not in PATH"}

    ok = _write_stream_json_user_message(proc, text)
    entry = {
        "pid": proc.pid,
        "name": f"resume-{session_id[:8]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
    }
    _spawned_sessions.append(entry)
    return {"ok": ok, "pid": proc.pid, "log": str(log_path), "resumed": True}


def list_spawned_sessions():
    """Return spawned sessions with running/finished status."""
    result = []
    for s in _spawned_sessions:
        poll = s["proc"].poll()
        result.append({
            "pid": s["pid"],
            "name": s["name"],
            "log": s["log"],
            "prompt": s.get("prompt", ""),
            "started": s.get("started", ""),
            "status": "running" if poll is None else f"finished (exit {poll})",
        })
    return result


# ---------------------------------------------------------------------------
# Pkood agent orchestration
# ---------------------------------------------------------------------------

PKOOD_STATE_DIR = Path.home() / ".pkood" / "state"
PKOOD_LOGS_DIR = Path.home() / ".pkood" / "logs"
PKOOD_SOCKETS_DIR = Path.home() / ".pkood" / "sockets"
PKOOD_BIN = str(Path.home() / ".local" / "bin" / "pkood")


def find_pkood_agents():
    """Scan ~/.pkood/state/*_meta.json and return unified session dicts."""
    if not PKOOD_STATE_DIR.is_dir():
        return []
    agents = []
    for meta_file in PKOOD_STATE_DIR.glob("*_meta.json"):
        try:
            data = json.loads(meta_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        agent_id = data.get("agent_id", meta_file.stem.replace("_meta", ""))
        target_dir = data.get("target_dir", "")
        update_ts = data.get("update_ts", 0)
        # Verify tmux session is actually alive — stale meta files can lie
        status = data.get("status", "")
        sock = PKOOD_SOCKETS_DIR / f"{agent_id}.sock"
        if status == "RUNNING" and sock.exists():
            try:
                probe = subprocess.run(
                    ["tmux", "-S", str(sock), "list-sessions"],
                    capture_output=True, timeout=2,
                )
                if probe.returncode != 0:
                    status = "DEAD"
            except (subprocess.TimeoutExpired, FileNotFoundError):
                status = "DEAD"
        elif status == "RUNNING":
            status = "DEAD"
        agents.append({
            "id": f"pkood-{agent_id}",
            "session_id": f"pkood-{agent_id}",
            "display_name": agent_id,
            "first_message": data.get("command", ""),
            "last_prompt": (data.get("last_output_snippet") or "")[:200],
            "branch": "",
            "modified": update_ts,
            "modified_human": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(update_ts)
            ) if update_ts else "",
            "size": 0,
            "source": "pkood",
            "session_cwd": target_dir,
            "session_cwd_exists": bool(target_dir and Path(target_dir).is_dir()),
            "has_edit": False,
            "has_commit": False,
            "has_push": False,
            "last_event_type": None,
            "pending_tool": None,
            "pending_file": None,
            "archived": False,
            "verified": False,
            "name_overridden": False,
            # Pkood-specific fields
            "pkood_status": status,  # RUNNING, IDLE, BLOCKED, DEAD
            "pkood_is_stuck": data.get("is_stuck", False),
            "is_live": status not in ("DEAD", ""),
        })
    agents.sort(key=lambda x: x["modified"], reverse=True)
    return agents


def pkood_spawn(prompt, agent_id=None, target_dir=None):
    """Spawn a pkood agent. Returns {ok, agent_id} or {ok: False, error}."""
    if not agent_id:
        agent_id = _slugify(prompt, max_len=30) or "agent"
    if not target_dir:
        target_dir = str(REPO_ROOT)
    cmd = [PKOOD_BIN, "spawn", "--name", agent_id, "--dir", target_dir, prompt]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"ok": True, "agent_id": agent_id}
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown error").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pkood spawn timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "pkood not found on PATH"}


def pkood_inject(agent_id, message):
    """Inject a message into a pkood agent."""
    cmd = [PKOOD_BIN, "inject", agent_id, message]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown error").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pkood inject timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "pkood not found on PATH"}


def pkood_kill(agent_id):
    """Kill a pkood agent."""
    cmd = [PKOOD_BIN, "kill", agent_id]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown error").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pkood kill timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "pkood not found on PATH"}


def pkood_tail(agent_id):
    """Get recent output from a pkood agent."""
    cmd = [PKOOD_BIN, "tail", agent_id]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return {"ok": True, "output": result.stdout}
        return {"ok": False, "error": (result.stderr or result.stdout or "unknown error").strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pkood tail timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "pkood not found on PATH"}


# ---------------------------------------------------------------------------
# GitHub issues
# ---------------------------------------------------------------------------

def _gh(*args, timeout=10):
    """Run a gh CLI command and return parsed JSON or None."""
    try:
        result = subprocess.run(
            ["gh"] + list(args),
            capture_output=True, text=True, timeout=timeout, cwd=str(REPO_ROOT),
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return None


def list_issues():
    """Return open issues + recently closed issues (last 24h)."""
    log_issues = {l["issue"] for l in find_log_files()}

    # Open issues
    open_issues = _gh(
        "issue", "list", "--state", "open", "--limit", "50",
        "--json", "number,title,labels,createdAt,updatedAt,state",
    ) or []

    # Recently closed (last day)
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    closed_issues = _gh(
        "issue", "list", "--state", "closed", "--limit", "20",
        "--search", f"closed:>{since[:10]}",
        "--json", "number,title,labels,createdAt,updatedAt,closedAt,state",
    ) or []

    all_issues = []
    for issue in open_issues + closed_issues:
        labels = [l["name"] for l in issue.get("labels", [])]
        # Determine claude status
        if "claude-in-progress" in labels:
            claude_status = "in_progress"
        elif "claude-fix" in labels:
            claude_status = "queued"
        elif "claude-failed" in labels:
            claude_status = "failed"
        elif issue["state"] == "CLOSED":
            claude_status = "closed"
        else:
            claude_status = "open"
        all_issues.append({
            "number": issue["number"],
            "title": _strip_title_prefix(issue["title"]),
            "labels": labels,
            "state": issue["state"].lower(),
            "claude_status": claude_status,
            "has_log": str(issue["number"]) in log_issues,
            "updated_at": issue.get("updatedAt", ""),
            "closed_at": issue.get("closedAt", ""),
        })

    # Sort: in_progress first, then queued, then open, then closed
    order = {"in_progress": 0, "queued": 1, "failed": 2, "open": 3, "closed": 4}
    all_issues.sort(key=lambda x: (order.get(x["claude_status"], 9), -x["number"]))
    return all_issues


def add_claude_fix_label(issue_number):
    """Add 'claude-fix' label to an issue."""
    try:
        result = subprocess.run(
            ["gh", "issue", "edit", str(issue_number), "--add-label", "claude-fix"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        if result.returncode == 0:
            return {"ok": True}
        return {"error": result.stderr.strip() or "Failed to add label"}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"error": str(e)}


def spawn_issue_fix(issue_number):
    """Spawn a headless Claude session to fix an issue directly (no worktree)."""
    issue_number = str(issue_number)
    try:
        result = subprocess.run(
            ["gh", "issue", "view", issue_number, "--json", "title,body"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            return {"error": f"Failed to fetch issue #{issue_number}: {result.stderr.strip()}"}
        issue_data = json.loads(result.stdout)
        title = issue_data.get("title", "")
        body = issue_data.get("body", "")
    except Exception as e:
        return {"error": f"Failed to fetch issue: {e}"}

    # Mark as in-progress
    subprocess.run(
        ["gh", "issue", "edit", issue_number, "--add-label", "claude-in-progress", "--remove-label", "claude-fix"],
        capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
    )

    prompt = f"""You are fixing GitHub issue #{issue_number}.

**Title:** {title}

**Description:**
{body}

Instructions:
- Read and follow the project CLAUDE.md for coding standards.
- Make the minimal changes needed to fix this issue.
- Commit your changes with a descriptive message referencing the issue (e.g. Fix #{issue_number}: ...).
- Push the branch and create a PR that closes #{issue_number}.
- You are working directly in the repo root — NOT in a worktree."""

    session_name = f"issue-{issue_number}"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"issue-{issue_number}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_filename

    cmd = [
        "claude", "-p", "--verbose",
        "--output-format", "stream-json",
        "--model", "claude-opus-4-6",
        "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash",
        "--dangerously-skip-permissions",
        "--name", session_name,
        prompt,
    ]

    log_fh = open(log_path, "w")
    # Write synthetic metadata + prompt so the viewer shows the title and initial prompt
    meta = json.dumps({
        "type": "spawn_meta",
        "issue_number": issue_number,
        "issue_title": title,
        "mode": "inline",
        "session_id": "",
    })
    log_fh.write(meta + "\n")
    prompt_ev = json.dumps({
        "type": "user",
        "message": {"content": [{"type": "text", "text": prompt}]},
        "session_id": "",
        "_synthetic": True,
    })
    log_fh.write(prompt_ev + "\n")
    log_fh.flush()

    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(REPO_ROOT),
        start_new_session=True,
    )

    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
    }
    _spawned_sessions.append(entry)

    return {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path)}


VERCEL_PROJECT = os.environ.get("VERCEL_PROJECT", "")


def vercel_deploy_status():
    """Return latest production deployment status from Vercel CLI.

    No-op when VERCEL_PROJECT isn't set — Vercel integration is opt-in.
    """
    if not VERCEL_PROJECT:
        return {"error": "VERCEL_PROJECT not configured", "disabled": True}
    try:
        result = subprocess.run(
            ["vercel", "ls", VERCEL_PROJECT, "--environment", "production", "-F", "json"],
            capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or "vercel ls failed"}

        data = json.loads(result.stdout)
        deployments = data.get("deployments", [])
        if not deployments:
            return {"error": "No deployments found"}

        d = deployments[0]
        created = d.get("createdAt", 0)
        ready = d.get("ready", 0)
        meta = d.get("meta", {})

        return {
            "state": d.get("state", "UNKNOWN"),
            "url": d.get("url", ""),
            "created_at": created,
            "ready_at": ready,
            "duration_s": round((ready - created) / 1000) if ready and created else None,
            "commit_sha": meta.get("githubCommitSha", "")[:7],
            "commit_msg": (meta.get("githubCommitMessage", "") or "").split("\n")[0][:80],
            "commit_ref": meta.get("githubCommitRef", ""),
            "project": VERCEL_PROJECT,
        }
    except subprocess.TimeoutExpired:
        return {"error": "vercel CLI timed out"}
    except (json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


def _load_fix_deploy_spawned():
    if not FIX_DEPLOY_SPAWNED_FILE.exists():
        return {}
    try:
        return json.loads(FIX_DEPLOY_SPAWNED_FILE.read_text())
    except Exception:
        return {}


def _save_fix_deploy_spawned(data):
    LOG_VIEWER_STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FIX_DEPLOY_SPAWNED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, FIX_DEPLOY_SPAWNED_FILE)


def vercel_deploy_status_with_autofix():
    """Return deploy status; auto-spawn /fix-deploy session on new ERROR."""
    status = vercel_deploy_status()
    if status.get("state") == "ERROR":
        sha = status.get("commit_sha") or ""
        if sha:
            spawned = _load_fix_deploy_spawned()
            if sha not in spawned:
                try:
                    info = spawn_session("/fix-deploy", name=f"fix-deploy-{sha}")
                    spawned[sha] = {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "spawned_at": time.time(),
                        "commit_msg": status.get("commit_msg", ""),
                    }
                    _save_fix_deploy_spawned(spawned)
                    status["auto_fix_spawned"] = spawned[sha]
                except Exception as e:
                    status["auto_fix_error"] = str(e)
            else:
                status["auto_fix_spawned"] = spawned[sha]
    return status


def auto_verify_closed_issues():
    """For any session with has_push + linked to a CLOSED GitHub issue,
    auto-set verified=True if not already. Returns what was changed."""
    verified_list = _load_verified_conversations()
    verified_set = set(verified_list)
    issue_states = _fetch_issue_states()
    convs = find_conversations() or []
    newly_verified = []

    for c in convs:
        if c.get("verified") or c.get("archived"):
            continue
        if not c.get("has_push"):
            continue
        inum = c.get("linked_issue")
        if not inum:
            # Heuristic: parse display_name
            m = re.match(r"^issue-(\d+)$", c.get("display_name") or "")
            if m:
                inum = m.group(1)
        if not inum:
            # Last resort: the full detector (includes tail_issue_number from
            # in-session `gh issue` / `Closes #N` signals)
            inum = _detect_issue_number_for_session(c)
        if not inum:
            continue
        st = issue_states.get(str(inum))
        if not st or st["state"] != "CLOSED":
            continue
        sid = c.get("session_id") or c.get("id")
        if sid in verified_set:
            continue
        verified_list.append(sid)
        verified_set.add(sid)
        newly_verified.append({"session_id": sid, "issue": inum, "display_name": c.get("display_name", "")[:80]})
        # Also strip in-progress label
        remove_in_progress_label(inum)

    if newly_verified:
        _save_verified_conversations(verified_list)

    return {"ok": True, "newly_verified": newly_verified, "count": len(newly_verified)}


def backfill_in_progress_labels():
    """Scan current conversations; for each session whose display_name looks like
    'issue-N' and isn't verified/archived, mark its linked issue as in-progress.
    Skips issues that are already closed on GitHub.
    """
    marked = []
    skipped = []
    errors = []
    convs = find_conversations() or []
    # Collect currently-open issue numbers to avoid marking closed issues.
    open_issues = _fetch_backlog_issues() or []
    open_set = {str(i.get("number")) for i in open_issues}

    seen = set()
    for c in convs:
        if c.get("verified") or c.get("archived"):
            continue
        issue_num = None
        dn = c.get("display_name") or ""
        m = re.match(r"^issue-(\d+)$", dn)
        if m:
            issue_num = m.group(1)
        elif c.get("linked_issue"):
            issue_num = str(c["linked_issue"])
        if not issue_num or issue_num in seen:
            continue
        seen.add(issue_num)
        if issue_num not in open_set:
            skipped.append({"issue": issue_num, "reason": "not open"})
            continue
        r = mark_issue_in_progress(issue_num)
        if r.get("ok"):
            marked.append(issue_num)
        else:
            errors.append({"issue": issue_num, "error": r.get("error", "?")})
    return {"ok": True, "marked": marked, "skipped": skipped, "errors": errors}


def mark_issue_in_progress(issue_number):
    """Signal to GitHub that work is starting on an issue:
    - reopens the issue if closed
    - adds 'claude-in-progress' label
    - self-assigns to the authenticated gh user (@me)
    """
    global _backlog_issues_cache_ts, _issue_state_cache_ts
    result = {"ok": False, "issue_number": str(issue_number)}
    # Reopen if closed
    try:
        st_out = subprocess.run(
            ["gh", "issue", "view", str(issue_number), "--json", "state"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        if st_out.returncode == 0:
            st = json.loads(st_out.stdout).get("state", "").upper()
            if st == "CLOSED":
                subprocess.run(
                    ["gh", "issue", "reopen", str(issue_number)],
                    capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
                )
                result["reopened"] = True
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    try:
        out = subprocess.run(
            ["gh", "issue", "edit", str(issue_number),
             "--add-label", "claude-in-progress",
             "--add-assignee", "@me"],
            capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
        )
        if out.returncode == 0:
            result["ok"] = True
            _backlog_issues_cache_ts = 0
            _issue_state_cache_ts = 0
        else:
            result["error"] = (out.stderr or out.stdout or "").strip()[:300]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result["error"] = str(e)
    return result


def mark_issue_icebox(issue_number):
    """Signal that an issue is parked in the icebox (Planning column in the UI):
    - adds the `icebox` label
    - removes `claude-in-progress` since the issue is parked, not being worked
    """
    global _backlog_issues_cache_ts, _issue_state_cache_ts
    result = {"ok": False, "issue_number": str(issue_number)}
    try:
        out = subprocess.run(
            ["gh", "issue", "edit", str(issue_number),
             "--add-label", "icebox",
             "--remove-label", "claude-in-progress"],
            capture_output=True, text=True, timeout=15, cwd=str(REPO_ROOT),
        )
        if out.returncode == 0:
            result["ok"] = True
            _backlog_issues_cache_ts = 0
            _issue_state_cache_ts = 0
        else:
            result["error"] = (out.stderr or out.stdout or "").strip()[:300]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result["error"] = str(e)
    return result


def remove_in_progress_label(issue_number):
    """Strip the claude-in-progress label (ignore if absent)."""
    global _backlog_issues_cache_ts
    try:
        subprocess.run(
            ["gh", "issue", "edit", str(issue_number),
             "--remove-label", "claude-in-progress"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT),
        )
        _backlog_issues_cache_ts = 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def close_issue(issue_number, reason, duplicate_of=None):
    """Close a GitHub issue with the given reason.

    reason ∈ {'completed', 'not planned', 'duplicate'}
    For 'duplicate', we close with reason='not planned' and add a comment
    "Duplicate of #N" (GitHub doesn't have a native 'duplicate' close reason).
    """
    global _backlog_issues_cache_ts
    reason = (reason or "").strip().lower()
    result = {"ok": False}
    try:
        if reason == "duplicate":
            if not duplicate_of:
                result["error"] = "duplicate_of is required for duplicate close"
                return result
            dup = str(duplicate_of).lstrip("#")
            comment = f"Duplicate of #{dup}"
            subprocess.run(
                ["gh", "issue", "comment", str(issue_number), "--body", comment],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["gh", "issue", "close", str(issue_number), "--reason", "not planned"],
                check=True, capture_output=True, text=True,
            )
            remove_in_progress_label(issue_number)
            _backlog_issues_cache_ts = 0
            result["ok"] = True
            result["comment"] = comment
            return result
        elif reason in ("completed", "not planned"):
            subprocess.run(
                ["gh", "issue", "close", str(issue_number), "--reason", reason],
                check=True, capture_output=True, text=True,
            )
            remove_in_progress_label(issue_number)
            _backlog_issues_cache_ts = 0
            result["ok"] = True
            return result
        else:
            result["error"] = f"unknown reason: {reason}"
            return result
    except subprocess.CalledProcessError as e:
        result["error"] = (e.stderr or e.stdout or str(e)).strip()[:300]
        return result


def get_issue_details(issue_number):
    """Return the full GitHub issue (title, body, labels, comments, URL)."""
    data = _gh(
        "issue", "view", str(issue_number),
        "--json", "title,body,labels,comments,url,author,state,createdAt,updatedAt",
    )
    if not data:
        return {"ok": False, "error": "gh issue view failed"}
    return {"ok": True, "issue": data}


def get_issue_summary(issue_number):
    """Get Claude's summary comment from a closed issue."""
    comments = _gh(
        "issue", "view", str(issue_number),
        "--json", "comments",
        "--jq", ".comments",
    )
    if not comments:
        # Try without jq
        data = _gh("issue", "view", str(issue_number), "--json", "comments,body")
        comments = (data or {}).get("comments", [])

    # Find Claude's closing comment (contains "Fixed and merged" or "Claude Code")
    for c in reversed(comments or []):
        body = c.get("body", "")
        if "Fixed and merged" in body or "Claude Code" in body or "failed" in body.lower():
            return {"summary": body}
    return {"summary": None}


# ---------------------------------------------------------------------------
# Morning launch — spawn-or-resume for a strategy's Claude session.
# Called from the POST /api/morning/launch route. Lives here (not in
# morning.py) because it calls spawn_session / resume_session_headless /
# _extract_spawn_meta, which are server-side process primitives.
# ---------------------------------------------------------------------------

def _morning_resume_framing(goal_name, strategy_text):
    return (
        f"Still working on the overall goal \"{goal_name}\". "
        f"Let's focus right now on:\n\n{strategy_text}"
    )


def _morning_spawn_prompt(goal_name, intent_markdown, strategy_text):
    # Full context for a never-seen-before strategy session.
    return (
        f"You're picking up a new focused work session on the goal \"{goal_name}\" "
        f"(from my Morning view in Claude Command Center).\n\n"
        f"## Goal intent\n\n{intent_markdown}\n\n"
        f"## Current strategy\n\n{strategy_text}\n\n"
        f"This is a fresh session for this strategy. Please help me move forward "
        f"on it, asking any clarifying questions first if needed."
    )


def _morning_resolve_session_id_from_log(log_path, max_wait_s=8.0, interval_s=0.25):
    """Poll a spawn log for a session_id in any of the first ~20 jsonl lines.

    Claude Code writes SessionStart hook events early with a `session_id`
    field, so we can resolve within a second or two even though the spawn
    prompt hasn't been processed yet. Scans any event type, not just the
    older `spawn_meta` convention that `_extract_spawn_meta` expects.
    """
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        sid = _scan_session_id_in_log(log_path)
        if sid:
            return sid
        time.sleep(interval_s)
    return _scan_session_id_in_log(log_path)


def _scan_session_id_in_log(log_path, max_lines=20):
    try:
        with open(log_path, "r") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = ev.get("session_id")
                if sid:
                    return sid
    except OSError:
        return None
    return None


def parse_conversation_by_sid(session_id, after_line=0):
    """Like parse_conversation() but searches every project dir for the sid.

    Morning-spawned sessions can land in any ~/.claude/projects/<slug>/
    depending on spawn cwd, so the CONVERSATIONS_DIR-anchored function
    misses them.
    """
    if not PROJECTS_ROOT.is_dir():
        return {"events": [], "last_line": 0}
    for pd in PROJECTS_ROOT.iterdir():
        if not pd.is_dir():
            continue
        cand = pd / f"{session_id}.jsonl"
        if cand.is_file():
            events = []
            line_num = 0
            try:
                with open(cand, "r") as f:
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
                        parsed = _parse_conversation_event(ev, line_num)
                        if parsed:
                            events.append(parsed)
            except OSError:
                break
            return {"events": events, "last_line": line_num}
    return {"events": [], "last_line": 0}


_MORNING_BRAINDUMP_PROMPT = """You are analyzing Amir's morning brain-dump.

For each item in his dump, classify as exactly one of:
- NEW: a fresh task/idea not already in his system
- EXISTING: matches or refines something already tracked; identify which
- CONTEXT: not a task — a thought, update, reflection, or meeting note
- DISCARD: noise ("ok", "hmm", filler)

Also suggest which GOAL it maps to (or null if unclear). Goal slugs are shown below.

## Goals

{goals}

## Existing tactical items (sample)

{tactical}

## Amir's braindump

```
{dump}
```

Return ONLY a JSON array. No prose. No markdown fences. Each item looks like:
{{"original_text": "...", "classification": "NEW"|"EXISTING"|"CONTEXT"|"DISCARD", "matched_existing": "short text of what it matched, or null", "suggested_goal": "slug or null", "notes": "one-sentence why"}}

Items in the dump are separated by newlines. Preserve Amir's original phrasing in original_text.
"""


def morning_braindump(text):
    """Run `claude -p --model haiku` on a brain-dump with context about
    existing goals/tactical items. Returns the parsed analysis array.
    """
    import morning_store as _store
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty dump"}

    try:
        goals = _store.load_all_goals()
    except Exception:
        goals = []
    goal_lines = []
    for g in goals:
        strats = g.get("strategies") or []
        slug = g.get("slug", "?")
        name = g.get("name", slug)
        strat_ids = ", ".join(s.get("id", "?") for s in strats if s.get("status") == "active")
        goal_lines.append(f"- {slug}: {name} (active strategies: {strat_ids or 'none'})")
    goals_block = "\n".join(goal_lines) or "(no goals configured)"

    # Grab current tactical items so Claude can match against them.
    import morning as _morning
    try:
        state = _morning.get_morning_state()
        tactical_sample = state.get("tactical", [])[:30]
    except Exception:
        tactical_sample = []
    tact_lines = []
    for t in tactical_sample:
        tact_lines.append(f"- [{t.get('source','?')}] {t.get('text','')}")
    tact_block = "\n".join(tact_lines) or "(no tactical items)"

    prompt = _MORNING_BRAINDUMP_PROMPT.format(
        goals=goals_block,
        tactical=tact_block,
        dump=text,
    )

    try:
        r = subprocess.run(
            ["claude", "-p", "--model", "haiku"],
            input=prompt, capture_output=True, text=True, timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "error": f"claude -p failed: {e}"}
    if r.returncode != 0:
        return {"ok": False, "error": f"claude -p exited {r.returncode}: {r.stderr[:200]}"}

    out = (r.stdout or "").strip()
    out = re.sub(r"^```(?:json)?\s*|\s*```$", "", out, flags=re.M).strip()
    m = re.search(r"\[.*\]", out, flags=re.S)
    if not m:
        return {"ok": False, "error": "no JSON array in response", "raw": out[:500]}
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON parse: {e}", "raw": out[:500]}

    return {"ok": True, "items": items}


def _morning_session_ids():
    """Return a dict {session_id: {"goal_slug": ..., "strategy_id": ...}}
    for every strategy across all goal.md files that has a claude_session_id.
    Used to route sessions to the Morning Kanban vs. the Dev Kanban.
    """
    import morning_store as _store
    out = {}
    try:
        goals = _store.load_all_goals()
    except Exception:
        return out
    for g in goals:
        for s in g.get("strategies", []):
            sid = s.get("claude_session_id")
            if sid:
                out[sid] = {
                    "goal_slug": g["slug"],
                    "goal_name": g.get("name"),
                    "goal_accent": g.get("accent"),
                    "strategy_id": s.get("id"),
                    "strategy_text": s.get("text"),
                    "strategy_status": s.get("status"),
                }
    return out


def morning_launch(goal_slug, strategy_id, custom_message=None):
    """Spawn a new Claude session for the strategy, or resume/inject if one
    already exists. Returns a dict describing the action taken.

    When `custom_message` is provided, a resume/inject uses it verbatim
    instead of the default "Still working on..." framing. Ignored for
    fresh spawns (those always get the full goal brief).
    """
    # Lazy import to avoid a cycle at module import time.
    import morning as _morning
    import morning_store as _store

    detail = _morning.get_goal_detail(goal_slug)
    if detail is None:
        return {"ok": False, "error": f"unknown goal: {goal_slug}"}
    strategy = next(
        (s for s in detail.get("strategies", []) if s.get("id") == strategy_id),
        None,
    )
    if strategy is None:
        return {"ok": False, "error": f"unknown strategy: {strategy_id}"}
    if strategy.get("status") == "dropped":
        return {"ok": False, "error": "strategy is dropped"}

    goal_name = detail.get("name") or goal_slug
    intent = detail.get("intent_markdown") or ""
    strategy_text = strategy.get("text") or strategy_id
    session_id = strategy.get("claude_session_id")

    if session_id:
        # Resume into the existing session and inject a message.
        message = (custom_message or "").strip() or _morning_resume_framing(goal_name, strategy_text)
        try:
            result = resume_session_headless(session_id, message)
        except Exception as e:  # pragma: no cover — best-effort
            return {"ok": False, "error": f"resume failed: {e}"}
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error") or "resume_session_headless failed",
                "action": "resume",
            }
        return {
            "ok": True,
            "action": "resumed",
            "session_id": session_id,
            "pid": result.get("pid"),
        }

    # Fresh spawn.
    name = f"{goal_slug}--{strategy_id}"
    try:
        spawn = spawn_session(
            _morning_spawn_prompt(goal_name, intent, strategy_text),
            name=name,
        )
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"spawn failed: {e}"}

    if not spawn.get("ok"):
        return {
            "ok": False,
            "error": spawn.get("error") or "spawn_session failed",
            "action": "spawn",
        }

    # Try to resolve the session_id from the spawn log so we can persist it.
    resolved_sid = None
    log_path = spawn.get("log")
    if log_path:
        resolved_sid = _morning_resolve_session_id_from_log(log_path)

    saved = False
    if resolved_sid:
        try:
            saved = _store.save_strategy_session_id(goal_slug, strategy_id, resolved_sid)
        except Exception:
            saved = False

    return {
        "ok": True,
        "action": "spawned",
        "pid": spawn.get("pid"),
        "name": name,
        "session_id": resolved_sid,
        "session_id_saved": saved,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

_INDEX_HTML_PATH = STATIC_DIR / "index.html"
def _load_index_html():
    try:
        return _INDEX_HTML_PATH.read_text()
    except OSError as e:
        return "<h1>index.html missing</h1><pre>" + str(e) + "</pre>"
HTML_PAGE = _load_index_html()


class LogViewerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "" or path == "/":
            # Re-read on every request so edits to static/index.html are live.
            self.send_html(_load_index_html())
        elif path == "/api/attention":
            self.send_json(compute_attention_items())
        elif path == "/api/config":
            self.send_json(get_app_config())
        elif path == "/api/logs":
            logs = find_log_files()
            # Strip internal path from response
            for log in logs:
                del log["path"]
            self.send_json(logs)
        elif path == "/api/watcher":
            self.send_json(watcher_status())
        elif path == "/api/issues":
            self.send_json(list_issues())
        elif path == "/api/vercel-deploy":
            self.send_json(vercel_deploy_status_with_autofix())
        elif re.match(r"^/api/issues/\d+/summary$", path):
            num = path.split("/")[3]
            self.send_json(get_issue_summary(num))
        elif re.match(r"^/api/issues/\d+/details$", path):
            num = path.split("/")[3]
            self.send_json(get_issue_details(num))
        elif path == "/api/sessions/spawned":
            self.send_json(list_spawned_sessions())
        elif path == "/api/sessions":
            self.send_json(find_all_sessions())
        elif path == "/api/conversations":
            convs = find_conversations() or []
            qs = urllib.parse.parse_qs(parsed.query)
            include_morning = qs.get("include_morning", ["0"])[0] in ("1", "true")
            if not include_morning:
                morning_sids = _morning_session_ids()
                convs = [c for c in convs if c.get("session_id") not in morning_sids]
            self.send_json(convs)
        elif path == "/api/morning/sessions":
            # Morning-spawned sessions may live in ANY project slug under
            # ~/.claude/projects/ (spawn cwd determines the slug), not only
            # the project CCC is watching. find_conversations() only scans
            # CONVERSATIONS_DIR — too narrow. Scan all project dirs for the
            # specific session_ids we care about.
            morning_sids = _morning_session_ids()
            registry = _load_session_registry() if PROJECTS_ROOT.is_dir() else {}
            out = []
            if PROJECTS_ROOT.is_dir():
                for sid, meta in morning_sids.items():
                    jsonl = None
                    for pd in PROJECTS_ROOT.iterdir():
                        if not pd.is_dir():
                            continue
                        cand = pd / f"{sid}.jsonl"
                        if cand.is_file():
                            jsonl = cand
                            break
                    if not jsonl:
                        continue
                    try:
                        stat = jsonl.stat()
                    except OSError:
                        continue
                    out.append({
                        "session_id": sid,
                        "display_name": meta.get("strategy_text"),
                        "first_message": meta.get("strategy_text"),
                        "modified": stat.st_mtime,
                        "modified_human": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                        "is_live": sid in registry,
                        "morning": meta,
                    })
            # Also surface strategies that have NO session yet ("never started"),
            # so the Morning Kanban Backlog column has something to launch from.
            never_started = []
            seen = set(morning_sids.keys())
            try:
                import morning_store as _store
                for g in _store.load_all_goals():
                    for s in g.get("strategies", []):
                        if s.get("status") in ("dropped", "done"):
                            continue
                        if s.get("claude_session_id"):
                            continue
                        never_started.append({
                            "goal_slug": g["slug"],
                            "goal_name": g.get("name"),
                            "goal_accent": g.get("accent"),
                            "strategy_id": s.get("id"),
                            "strategy_text": s.get("text"),
                            "strategy_status": s.get("status"),
                        })
            except Exception:
                pass
            self.send_json({"sessions": out, "never_started": never_started})
        elif re.match(r"^/api/morning/conversation/[a-zA-Z0-9-]+$", path):
            sid = path.rsplit("/", 1)[-1]
            qs = urllib.parse.parse_qs(parsed.query)
            after_line = int(qs.get("after", ["0"])[0])
            self.send_json(parse_conversation_by_sid(sid, after_line))
        elif path == "/morning/kanban":
            try:
                self.send_html((MORNING_STATIC_DIR / "kanban.html").read_text())
            except OSError as e:
                self.send_json({"error": "morning/kanban.html missing", "detail": str(e)}, 500)
        elif path == "/api/session-status":
            qs = urllib.parse.parse_qs(parsed.query)
            sid = qs.get("session_id", [""])[0]
            cwd = qs.get("cwd", [""])[0]
            if not cwd:
                cwd = find_session_cwd(sid)
            status = session_live_status(sid, cwd)
            status["cwd"] = cwd
            status["cwd_exists"] = bool(cwd and Path(cwd).is_dir())
            self.send_json(status)
        elif re.match(r"^/api/conversations/[a-f0-9-]+/stream$", path):
            conv_id = path.split("/")[-2]
            qs = urllib.parse.parse_qs(parsed.query)
            after_line = int(qs.get("after", ["0"])[0])
            self._stream_conversation(conv_id, after_line)
        elif re.match(r"^/api/conversations/[a-f0-9-]+$", path):
            conv_id = path.split("/")[-1]
            qs = urllib.parse.parse_qs(parsed.query)
            after_line = int(qs.get("after", ["0"])[0])
            result = parse_conversation(conv_id, after_line)
            self.send_json(result)
        elif path == "/api/pkood/tail":
            qs = urllib.parse.parse_qs(parsed.query)
            agent_id = qs.get("id", [""])[0]
            if not agent_id:
                self.send_json({"ok": False, "error": "missing id parameter"}, 400)
            else:
                self.send_json(pkood_tail(agent_id))
        elif path.startswith("/api/logs/"):
            issue = path.split("/")[-1]
            qs = urllib.parse.parse_qs(parsed.query)
            after_line = int(qs.get("after", ["0"])[0])

            # Find the log file
            log_file = None
            for log in find_log_files():
                if log["issue"] == issue:
                    log_file = log["path"]
                    break

            if not log_file:
                self.send_json({"error": f"No log found for issue #{issue}"}, 404)
                return

            result = parse_log_file(log_file, after_line)
            self.send_json(result)
        elif path.startswith("/static/morning/"):
            rel = path[len("/static/morning/"):]
            target = MORNING_STATIC_DIR / rel
            try:
                resolved = target.resolve(strict=False)
                base = MORNING_STATIC_DIR.resolve()
            except OSError as e:
                self.send_json({"error": str(e)}, 500)
                return
            # Prevent path traversal (../../etc/passwd). Check before .is_file().
            try:
                resolved.relative_to(base)
            except ValueError:
                self.send_json({"error": f"not found: {path}"}, 404)
                return
            if not resolved.is_file():
                self.send_json({"error": f"not found: {path}"}, 404)
            else:
                try:
                    body = resolved.read_bytes()
                except OSError as e:
                    self.send_json({"error": str(e)}, 500)
                    return
                ct = "text/plain"
                if rel.endswith(".js"):
                    ct = "application/javascript"
                elif rel.endswith(".css"):
                    ct = "text/css"
                elif rel.endswith(".html"):
                    ct = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(body)
        elif path == "/morning":
            try:
                self.send_html((MORNING_STATIC_DIR / "index.html").read_text())
            except OSError as e:
                self.send_json({"error": "morning/index.html missing", "detail": str(e)}, 500)
        elif re.match(r"^/morning/goals/[A-Za-z0-9_-]+$", path):
            try:
                self.send_html((MORNING_STATIC_DIR / "goal-detail.html").read_text())
            except OSError as e:
                self.send_json({"error": "morning/goal-detail.html missing", "detail": str(e)}, 500)
        elif path == "/api/morning/state":
            self.send_json(morning.get_morning_state())
        elif re.match(r"^/api/morning/goals/[A-Za-z0-9_-]+$", path):
            slug = path.rsplit("/", 1)[-1]
            detail = morning.get_goal_detail(slug)
            if detail is None:
                self.send_json({"error": f"unknown goal: {slug}"}, 404)
            else:
                self.send_json(detail)
        elif path.startswith("/static/affiliates/"):
            rel = path[len("/static/affiliates/"):]
            target = AFFILIATES_STATIC_DIR / rel
            try:
                resolved = target.resolve(strict=False)
                base = AFFILIATES_STATIC_DIR.resolve()
            except OSError as e:
                self.send_json({"error": str(e)}, 500)
                return
            try:
                resolved.relative_to(base)
            except ValueError:
                self.send_json({"error": f"not found: {path}"}, 404)
                return
            if not resolved.is_file():
                self.send_json({"error": f"not found: {path}"}, 404)
            else:
                try:
                    body = resolved.read_bytes()
                except OSError as e:
                    self.send_json({"error": str(e)}, 500)
                    return
                ct = "text/plain"
                if rel.endswith(".js"):
                    ct = "application/javascript"
                elif rel.endswith(".css"):
                    ct = "text/css"
                elif rel.endswith(".html"):
                    ct = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Cache-Control", "no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(body)
        elif path == "/affiliates":
            try:
                self.send_html((AFFILIATES_STATIC_DIR / "index.html").read_text())
            except OSError as e:
                self.send_json({"error": "affiliates/index.html missing", "detail": str(e)}, 500)
        elif path == "/api/affiliates":
            self.send_json({"leads": affiliates_store.list_leads()})
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path == "/api/morning/ingest/run":
            # Fire-and-forget: spawn the Apple Notes ingester in the background.
            # The morning page refreshes its state right after this call returns,
            # so new candidates will appear on the *next* scan (after Claude -p
            # finishes extracting).
            script = CCC_ROOT / "scripts" / "ingest_apple_notes.py"
            if not script.is_file():
                self.send_json({"ok": False, "error": "ingester not found"}, 500)
            else:
                log_path = LOG_DIR / f"ingest-{int(time.time())}.log"
                try:
                    lf = open(log_path, "w")
                    subprocess.Popen(
                        ["python3", str(script)],
                        stdout=lf, stderr=subprocess.STDOUT,
                        cwd=str(CCC_ROOT),
                    )
                    self.send_json({"ok": True, "log": str(log_path), "script": str(script)})
                except (OSError, subprocess.SubprocessError) as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
        elif re.match(r"^/api/morning/goals/[A-Za-z0-9_-]+/context/attach$", path):
            slug = path.split("/")[4]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            try:
                import morning_store as _store
                result = _store.attach_context(
                    slug,
                    source=(payload.get("source") or "").strip(),
                    source_id=(payload.get("source_id") or "").strip(),
                    title=(payload.get("title") or "").strip(),
                    body_markdown=payload.get("body_markdown") or "",
                )
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            self.send_json(result, 200 if result.get("ok") else 400)
        elif path in ("/api/morning/inbox/promote", "/api/morning/inbox/dismiss"):
            action = path.rsplit("/", 1)[-1]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            cid = (payload.get("id") or "").strip()
            if not cid:
                self.send_json({"ok": False, "error": "missing id"}, 400)
            else:
                import morning_store as _store
                if action == "promote":
                    goal_slug = (payload.get("goal_slug") or "").strip()
                    as_kind = (payload.get("as") or "tactical").strip()  # tactical | strategy | context
                    if not goal_slug:
                        self.send_json({"ok": False, "error": "missing goal_slug"}, 400)
                        return
                    result = _store.mark_inbox_item(
                        cid,
                        promoted_to=goal_slug,
                        promoted_as=as_kind,
                    )
                else:  # dismiss
                    import time as _t
                    result = _store.mark_inbox_item(
                        cid,
                        dismissed_at=_t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime()),
                    )
                self.send_json(result)
        elif path == "/api/morning/braindump/accept":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            goal_slug = (payload.get("goal_slug") or "").strip()
            action = (payload.get("action") or "").strip()  # "tactical" | "context"
            text = (payload.get("text") or "").strip()
            if not goal_slug or not text or action not in ("tactical", "context"):
                self.send_json({"ok": False, "error": "need goal_slug, text, action in (tactical|context)"}, 400)
            else:
                import morning_store as _store
                try:
                    if action == "tactical":
                        result = _store.add_user_tactical(goal_slug, text, source_note="braindump")
                    else:
                        result = _store.attach_context(
                            goal_slug,
                            source="braindump",
                            source_id=(payload.get("source_id") or "")[:60],
                            title=text[:80],
                            body_markdown=text,
                        )
                except Exception as e:
                    result = {"ok": False, "error": str(e)}
                self.send_json(result, 200 if result.get("ok") else 400)
        elif path == "/api/morning/braindump":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            text = (payload.get("text") or "").strip()
            if not text:
                self.send_json({"ok": False, "error": "missing text"}, 400)
            else:
                try:
                    self.send_json(morning_braindump(text))
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/morning/launch":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            goal_slug = (payload.get("goal_slug") or "").strip()
            strategy_id = (payload.get("strategy_id") or "").strip()
            custom_message = payload.get("message")
            if not goal_slug or not strategy_id:
                self.send_json({"ok": False, "error": "missing goal_slug or strategy_id"}, 400)
            else:
                try:
                    self.send_json(morning_launch(goal_slug, strategy_id, custom_message=custom_message))
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/affiliates":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            try:
                lead = affiliates_store.create_lead(payload)
                self.send_json({"ok": True, "lead": lead})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif re.match(r"^/api/affiliates/lead_[A-Za-z0-9_]+/delete$", path):
            lead_id = path.split("/")[3]
            try:
                deleted = affiliates_store.delete_lead(lead_id)
                if deleted:
                    self.send_json({"ok": True, "id": lead_id})
                else:
                    self.send_json({"ok": False, "error": "not found"}, 404)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif re.match(r"^/api/affiliates/lead_[A-Za-z0-9_]+$", path):
            lead_id = path.split("/")[3]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            try:
                updated = affiliates_store.update_lead(lead_id, payload)
                if updated is None:
                    self.send_json({"ok": False, "error": "not found"}, 404)
                else:
                    self.send_json({"ok": True, "lead": updated})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/upload-image":
            ctype = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 25 * 1024 * 1024:
                self.send_json({"ok": False, "error": "bad length"}, 400)
            else:
                raw = self.rfile.read(length)
                # Determine extension from content type
                ext_map = {
                    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
                    "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg",
                }
                ext = ext_map.get(ctype.split(";")[0].strip().lower(), "png")
                repo = os.environ.get("CCC_WATCH_REPO") or os.getcwd()
                img_dir = os.path.join(repo, ".claude", "pasted-images")
                os.makedirs(img_dir, exist_ok=True)
                fname = f"paste-{int(time.time()*1000)}.{ext}"
                fpath = os.path.join(img_dir, fname)
                try:
                    with open(fpath, "wb") as f:
                        f.write(raw)
                    self.send_json({"ok": True, "path": fpath, "name": fname, "bytes": len(raw)})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/open":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            target = (payload.get("path") or "").strip()
            if not target:
                self.send_json({"ok": False, "error": "missing path"}, 400)
            else:
                candidates = []
                if os.path.isabs(target):
                    candidates.append(target)
                else:
                    cwd = os.getcwd()
                    candidates.append(os.path.join(cwd, target))
                    home = os.path.expanduser("~")
                    for root in ("dev/my-finance-app", "dev/claude-command-center", "dev"):
                        candidates.append(os.path.join(home, root, target))
                resolved = next((p for p in candidates if os.path.exists(p)), None)
                if not resolved:
                    self.send_json({"ok": False, "error": "not found", "tried": candidates}, 404)
                else:
                    try:
                        subprocess.Popen(["open", resolved])
                        self.send_json({"ok": True, "path": resolved})
                    except Exception as e:
                        self.send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/watcher/start":
            self.send_json(watcher_start())
        elif path == "/api/watcher/stop":
            self.send_json(watcher_stop())
        elif path == "/api/sessions/spawn":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            prompt = (payload.get("prompt") or "").strip()
            name = (payload.get("name") or "").strip() or None
            if not prompt:
                self.send_json({"ok": False, "error": "missing prompt"})
            else:
                try:
                    self.send_json(spawn_session(prompt, name=name))
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
        elif re.match(r"^/api/sessions/spawned/\d+/inject$", path):
            pid = int(path.split("/")[4])
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            text = (payload.get("text") or "").strip()
            if not text:
                self.send_json({"ok": False, "error": "missing text"})
            else:
                self.send_json(inject_into_spawned(pid, text))
        elif re.match(r"^/api/issues/\d+/add-label$", path):
            num = path.split("/")[3]
            self.send_json(add_claude_fix_label(num))
        elif re.match(r"^/api/issues/\d+/spawn$", path):
            num = path.split("/")[3]
            try:
                self.send_json(spawn_issue_fix(num))
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/conversations/order":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            order = payload.get("order", [])
            try:
                _save_conversation_order(order)
                self.send_json({"ok": True, "count": len(order)})
            except OSError as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif re.match(r"^/api/issues/\d+/mark-icebox$", path):
            num = re.findall(r"\d+", path)[-1]
            self.send_json(mark_issue_icebox(num))
        elif re.match(r"^/api/issues/\d+/mark-in-progress$", path):
            num = path.split("/")[3]
            self.send_json(mark_issue_in_progress(num))
        elif path == "/api/issues/auto-verify":
            self.send_json(auto_verify_closed_issues())
        elif path == "/api/issues/backfill-in-progress":
            self.send_json(backfill_in_progress_labels())
        elif re.match(r"^/api/issues/\d+/close$", path):
            num = path.split("/")[3]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            reason = payload.get("reason") or "completed"
            duplicate_of = payload.get("duplicate_of")
            self.send_json(close_issue(num, reason, duplicate_of))
        elif re.match(r"^/api/conversations/[a-f0-9-]+/summarize$", path):
            conv_id = path.split("/")[-2]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            sid = payload.get("session_id") or conv_id
            try:
                result = summarize_session_title(sid)
                result["session_id"] = sid
                self.send_json(result)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif re.match(r"^/api/conversations/[a-f0-9-]+/rename$", path) or re.match(r"^/api/conversations/issue-\d+/rename$", path) or re.match(r"^/api/conversations/pkood-[^/]+/rename$", path):
            conv_id = path.split("/")[-2]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            name = (payload.get("name") or "").strip()
            sid = payload.get("session_id") or conv_id
            result = rename_session(sid, name)
            result["session_id"] = sid
            result["name"] = name
            self.send_json(result)
        elif re.match(r"^/api/conversations/[a-f0-9-]+/archive$", path) or re.match(r"^/api/conversations/issue-\d+/archive$", path) or re.match(r"^/api/conversations/pkood-[^/]+/archive$", path) or re.match(r"^/api/conversations/backlog-(issue|todo)-\d+/archive$", path):
            conv_id = path.split("/")[-2]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            sid = payload.get("session_id") or conv_id
            # Backlog GitHub issue: close with "not planned" reason
            backlog_match = re.match(r"^backlog-issue-(\d+)$", conv_id)
            if backlog_match:
                issue_num = backlog_match.group(1)
                try:
                    gh_out = subprocess.run(
                        ["gh", "issue", "close", issue_num,
                         "--reason", "not planned",
                         "--comment", "Archived via command center (not planned)"],
                        capture_output=True, text=True, timeout=10,
                        cwd=str(REPO_ROOT),
                    )
                    global _backlog_issues_cache_ts, _issue_titles_cache_ts
                    _backlog_issues_cache_ts = 0
                    _issue_titles_cache_ts = 0
                    self.send_json({
                        "ok": gh_out.returncode == 0,
                        "archived": True,
                        "github": {"action": "close-not-planned", "issue": issue_num,
                                   "ok": gh_out.returncode == 0,
                                   "stderr": gh_out.stderr.strip()[:200]},
                    })
                except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return
            # Backlog TODO item: nothing to persist server-side; frontend hides it
            if re.match(r"^backlog-todo-\d+$", conv_id):
                self.send_json({"ok": True, "archived": True, "note": "todo hidden client-side"})
                return
            try:
                archived = _load_archived_conversations()
                if sid in archived:
                    archived.remove(sid)
                    now_archived = False
                else:
                    archived.append(sid)
                    now_archived = True
                _save_archived_conversations(archived)
                # If this is a watcher issue session, also close/reopen the GitHub issue
                issue_match = re.match(r"^issue-(\d+)$", conv_id)
                gh_result = None
                if issue_match:
                    issue_num = issue_match.group(1)
                    action = "close" if now_archived else "reopen"
                    try:
                        gh_out = subprocess.run(
                            ["gh", "issue", action, issue_num],
                            capture_output=True, text=True, timeout=10,
                            cwd=str(REPO_ROOT),
                        )
                        gh_result = {"action": action, "ok": gh_out.returncode == 0}
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        gh_result = {"action": action, "ok": False}
                self.send_json({"ok": True, "archived": now_archived, "github": gh_result})
            except OSError as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif re.match(r"^/api/conversations/[a-f0-9-]+/verify$", path) or re.match(r"^/api/conversations/issue-\d+/verify$", path) or re.match(r"^/api/conversations/pkood-[^/]+/verify$", path):
            conv_id = path.split("/")[-2]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            sid = payload.get("session_id") or conv_id
            try:
                verified = _load_verified_conversations()
                if sid in verified:
                    verified.remove(sid)
                    now_verified = False
                else:
                    verified.append(sid)
                    now_verified = True
                _save_verified_conversations(verified)
                # Also close linked GitHub issue with commit SHA comment
                gh_result = None
                if now_verified:
                    # Find the issue: from watcher id OR side-car mapping OR
                    # fallback: parse "issue-N" display name (how /Start Session names sessions).
                    issue_num = None
                    m = re.match(r"^issue-(\d+)$", conv_id)
                    if m:
                        issue_num = m.group(1)
                    else:
                        issue_num = _load_session_issues().get(sid)
                    if not issue_num:
                        display_name = payload.get("display_name") or ""
                        dm = re.match(r"^issue-(\d+)$", display_name)
                        if dm:
                            issue_num = dm.group(1)
                            _save_session_issue(sid, issue_num)
                    if issue_num:
                        # Build a minimal conv dict for helper
                        conv_info = {
                            "session_id": sid,
                            "session_cwd": payload.get("cwd") or str(REPO_ROOT),
                            "display_name": payload.get("display_name", ""),
                        }
                        ok = close_github_issue_with_commit(issue_num, conv_info)
                        gh_result = {"action": "close", "issue": issue_num, "ok": ok}
                self.send_json({"ok": True, "verified": now_verified, "github": gh_result})
            except OSError as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif re.match(r"^/api/conversations/[a-zA-Z0-9-]+/create-issue$", path):
            conv_id = path.split("/")[-2]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            # Build a conv dict from payload (frontend sends what it knows)
            conv = {
                "session_id": payload.get("session_id") or conv_id,
                "display_name": payload.get("display_name", ""),
                "first_message": payload.get("first_message", ""),
                "last_prompt": payload.get("last_prompt", ""),
                "branch": payload.get("branch", ""),
            }
            self.send_json(create_github_issue_for_session(conv))
        elif re.match(r"^/api/conversations/[a-zA-Z0-9-]+/link-issue$", path):
            conv_id = path.split("/")[-2]
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            sid = payload.get("session_id") or conv_id
            issue_num = payload.get("issue_number")
            try:
                _save_session_issue(sid, issue_num)
                self.send_json({"ok": True, "session_id": sid, "issue_number": str(issue_num) if issue_num else None})
            except OSError as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
        elif path == "/api/pkood/spawn":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            prompt = (payload.get("prompt") or "").strip()
            if not prompt:
                self.send_json({"ok": False, "error": "missing prompt"})
            else:
                self.send_json(pkood_spawn(
                    prompt,
                    agent_id=payload.get("id"),
                    target_dir=payload.get("target_dir"),
                ))
        elif path == "/api/pkood/inject":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            agent_id = (payload.get("agent_id") or "").strip()
            message = (payload.get("message") or "").strip()
            if not agent_id or not message:
                self.send_json({"ok": False, "error": "missing agent_id or message"})
            else:
                self.send_json(pkood_inject(agent_id, message))
        elif path == "/api/pkood/kill":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            agent_id = (payload.get("agent_id") or "").strip()
            if not agent_id:
                self.send_json({"ok": False, "error": "missing agent_id"})
            else:
                self.send_json(pkood_kill(agent_id))
        elif path == "/api/inject-input":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            sid = payload.get("session_id", "")
            text = payload.get("text", "")
            if not sid or not text:
                self.send_json({"ok": False, "error": "missing session_id or text"})
            else:
                cwd = find_session_cwd(sid)
                status = session_live_status(sid, cwd)
                tty = status.get("tty")
                term_app = status.get("terminal_app")
                if not status.get("live") or not tty:
                    # Fall back: resume the session headlessly and inject via stream-json
                    result = resume_session_headless(sid, text)
                    self.send_json(result)
                else:
                    result = inject_input_via_keystroke(
                        tty, term_app or "Terminal", text
                    )
                    self.send_json(result)
        elif path == "/api/launch-terminal":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            sid = payload.get("session_id", "")
            cwd = payload.get("cwd") or None
            term_app = payload.get("terminal_app") or None
            self.send_json(launch_terminal_for_session(sid, cwd, term_app))
        elif path == "/api/jump-terminal":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            tty = payload.get("tty", "")
            term_app = payload.get("terminal_app", "")
            # If the caller only sent session_id, resolve tty/terminal_app from live state
            if not tty and payload.get("session_id"):
                sid = payload["session_id"]
                cwd = payload.get("cwd") or find_session_cwd(sid)
                status = session_live_status(sid, cwd)
                tty = status.get("tty") or ""
                term_app = status.get("terminal_app") or ""
            self.send_json(focus_terminal_by_tty(tty, term_app))
        else:
            self.send_json({"error": "Not found"}, 404)

    def _stream_conversation(self, conversation_id, after_line):
        """SSE endpoint for real-time conversation tailing."""
        filepath = CONVERSATIONS_DIR / (conversation_id + ".jsonl")
        if not filepath.exists():
            self.send_json({"error": "Conversation not found"}, 404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        line_num = 0
        last_keepalive = time.time()
        timeout_at = time.time() + 300  # 5 minute max

        try:
            while time.time() < timeout_at:
                events = []
                try:
                    with open(filepath, "r") as f:
                        for line in f:
                            line_num_current = line_num + 1
                            if line_num_current <= after_line:
                                line_num = line_num_current
                                continue
                            line_num = line_num_current
                            stripped = line.strip()
                            if not stripped:
                                continue
                            try:
                                ev = json.loads(stripped)
                            except json.JSONDecodeError:
                                continue
                            parsed = _parse_conversation_event(ev, line_num)
                            if parsed:
                                events.append(parsed)
                except FileNotFoundError:
                    break

                if events:
                    payload = {"events": events, "last_line": line_num}
                    self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                    self.wfile.flush()
                    after_line = line_num

                # Reset line_num for next read — we'll re-read from start and skip
                # Actually, keep line_num as-is; on next iteration we re-scan from 0
                # but skip up to after_line
                line_num = 0

                now = time.time()
                if now - last_keepalive >= 5:
                    self.wfile.write(b"event: keepalive\ndata: {}\n\n")
                    self.wfile.flush()
                    last_keepalive = now

                time.sleep(0.3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # Client disconnected

    def send_html(self, content):
        # Inject repo name for GitHub links
        repo = self._get_repo()
        content = content.replace('<body>', f'<body data-repo="{repo}">', 1)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # Never cache the single-page app. The server re-reads index.html on every
        # request; this header stops browsers from serving a stale JS snapshot
        # after edits (main cause of "I clicked the button and nothing happened").
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(content.encode())

    @staticmethod
    def _get_repo():
        try:
            r = subprocess.run(
                ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                capture_output=True, text=True, timeout=5, cwd=str(REPO_ROOT),
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        # Quieter logging — only errors
        if args and "404" in str(args[0]):
            super().log_message(format, *args)


def _warm_cache():
    """Pre-warm the conversation metadata cache in a background thread."""
    try:
        t0 = time.time()
        find_all_sessions()
        print(f"  Cache warmed in {time.time() - t0:.1f}s ({len(_conv_meta_cache)} files)")
    except Exception as e:
        print(f"  Cache warm failed: {e}")


_app_config_cache = None
_app_config_cache_ts = 0


def _classify_attention(c):
    """For a single conv, decide whether it needs user attention and in what way.
    Returns a dict {kind, priority, where, did, insight, next_step} or None.

    Priority ordering (lower = more urgent):
      1 pending_tool         agent paused waiting for tool approval
      2 sidecar_waiting      live session idle, expecting next prompt
      3 pushed_open          pushed but linked issue still OPEN (PR missing Closes #N?)
      4 uncommitted_edits    dormant with edits but no commit (the "fix done" case)
      5 committed_not_pushed commits exist locally but never pushed
      6 needs_attention_label  backlog issue flagged by the reporter
      7 open_backlog         unflagged open backlog item
    """
    if c.get("archived") or c.get("verified"):
        return None
    bt = c.get("backlog_type")
    if bt in ("todo", "parking"):
        return None  # explicit: "don't flood me with TODO.md noise"

    state = c.get("session_state") or {}
    has_structured = bool(state.get("did") or state.get("insight") or state.get("next_step_user"))

    sid = c.get("session_id") or c.get("id")
    name = (c.get("display_name") or c.get("first_message") or "")[:100]
    inum = c.get("linked_issue") or c.get("issue_number") or c.get("tail_issue_number") or ""

    # ── Session (non-backlog) cases ────────────────────────────────────────
    if c.get("source") != "backlog":
        live = bool(c.get("is_live"))
        pending_tool = c.get("pending_tool")
        pending_file = c.get("pending_file") or ""
        last_event = c.get("last_event_type")
        sidecar_status = c.get("sidecar_status")

        if live and pending_tool:
            return {
                "kind": "pending_tool", "priority": 1,
                "session_id": sid, "name": name,
                "where": "Working · blocked on tool approval",
                "did": state.get("did"),
                "insight": state.get("insight"),
                "next_step": state.get("next_step_user") or
                    (f"Jump to terminal — Claude paused on {pending_tool}" +
                     (f" on {pending_file}" if pending_file else "")),
                "has_structured": has_structured,
            }

        if live and sidecar_status == "waiting":
            return {
                "kind": "sidecar_waiting", "priority": 2,
                "session_id": sid, "name": name,
                "where": "Working · idle, awaiting your prompt",
                "did": state.get("did"),
                "insight": state.get("insight"),
                "next_step": state.get("next_step_user") or
                    "Open the session and send the next instruction",
                "has_structured": has_structured,
            }

        # Pushed but the linked GH issue never auto-closed (PR missing `Closes #N`)
        if (c.get("has_push") and inum and
                (c.get("gh_state") or "").upper() == "OPEN"):
            return {
                "kind": "pushed_open", "priority": 3,
                "session_id": sid, "name": name,
                "where": f"Review · pushed, issue #{inum} still open",
                "did": state.get("did"),
                "insight": state.get("insight"),
                "next_step": state.get("next_step_user") or
                    f"Verify the deploy then close #{inum} manually",
                "has_structured": has_structured,
            }

        # Dormant with edits but nothing committed — the "agent finished, work is
        # sitting in the working tree" case the user specifically flagged.
        if (not live) and c.get("has_edit") and not c.get("has_commit"):
            return {
                "kind": "uncommitted_edits", "priority": 4,
                "session_id": sid, "name": name,
                "where": "Review · uncommitted edits",
                "did": state.get("did"),
                "insight": state.get("insight"),
                "next_step": state.get("next_step_user") or
                    "Open the card, read the summary, verify diff, tap Commit & resolve",
                "has_structured": has_structured,
            }

        if c.get("has_commit") and not c.get("has_push"):
            return {
                "kind": "committed_not_pushed", "priority": 5,
                "session_id": sid, "name": name,
                "where": "Review · commits unpushed",
                "did": state.get("did"),
                "insight": state.get("insight"),
                "next_step": state.get("next_step_user") or
                    "Open the card and push the branch (or send `push` via input bar)",
                "has_structured": has_structured,
            }

        return None

    # ── Backlog (GitHub) cases ─────────────────────────────────────────────
    if bt != "github":
        return None  # covered above — TODO/parking already returned None
    labels = c.get("issue_labels") or []
    is_needs_attn = "needs-attention" in labels
    is_icebox = "icebox" in labels
    has_wip = c.get("gh_in_progress") or ("claude-in-progress" in labels)

    if is_needs_attn:
        return {
            "kind": "needs_attention_label", "priority": 6,
            "session_id": sid, "name": name,
            "where": f"Backlog · flagged needs-attention",
            "did": None, "insight": None,
            "next_step": f"Read issue #{inum}, respond to reporter, then remove the label",
            "has_structured": False,
        }

    if not has_wip and not is_icebox:
        return {
            "kind": "open_backlog", "priority": 7,
            "session_id": sid, "name": name,
            "where": "Backlog · open",
            "did": None, "insight": None,
            "next_step": "Triage: start session, icebox, or close",
            "has_structured": False,
        }
    return None


def compute_attention_items():
    """Rank-and-cap list of cards that need user attention.

    Caps: 8 total, max 3 backlog — stay scannable.
    Sort: priority ASC, then most recent activity first.
    """
    try:
        convs = find_all_sessions() or []
    except Exception:
        convs = []
    raw = []
    for c in convs:
        item = _classify_attention(c)
        if item:
            item["_modified"] = c.get("modified") or 0
            raw.append(item)
    raw.sort(key=lambda i: (i["priority"], -i["_modified"]))
    MAX_TOTAL = 8
    MAX_BACKLOG = 3
    out = []
    backlog_count = 0
    backlog_kinds = ("needs_attention_label", "open_backlog")
    for it in raw:
        if it["kind"] in backlog_kinds:
            if backlog_count >= MAX_BACKLOG:
                continue
            backlog_count += 1
        it.pop("_modified", None)
        out.append(it)
        if len(out) >= MAX_TOTAL:
            break
    return {"ok": True, "items": out}


def get_app_config():
    """Surface the detected environment to the frontend so the UI can
    conditionally render panels (Vercel, Watcher, pkood) and avoid hardcoded
    user-specific defaults. Cached 30s."""
    global _app_config_cache, _app_config_cache_ts
    if _app_config_cache and time.time() - _app_config_cache_ts < 30:
        return _app_config_cache
    import shutil
    # Detect GitHub repo via gh
    repo_slug = ""
    try:
        out = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            capture_output=True, text=True, timeout=5, cwd=str(REPO_ROOT),
        )
        if out.returncode == 0:
            repo_slug = (out.stdout or "").strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    config = {
        "app_name": "Claude Command Center",
        "title_strip": TITLE_STRIP_PREFIXES,
        "repo": repo_slug,
        "vercel_enabled": bool(VERCEL_PROJECT),
        "vercel_project": VERCEL_PROJECT,
        "pkood_enabled": bool(shutil.which("pkood")),
        "watcher_enabled": WATCHER_SCRIPT.exists(),
        "gh_enabled": bool(shutil.which("gh")),
        "orgs": [label for label, _ in ORG_PATTERNS],
    }
    _app_config_cache = config
    _app_config_cache_ts = time.time()
    return config


def ensure_hooks_installed():
    """Ensure our PostToolUse and Stop hooks are registered in ~/.claude/settings.json.

    Also copies the hook scripts from this repo's hooks/ into
    ~/.claude/log-viewer/hooks/ so ~/.claude/settings.json can reference them
    from a stable location independent of where this repo is checked out.
    """
    # Copy hook scripts into the well-known install location, keeping them
    # in sync with whatever version is in this repo.
    import shutil
    repo_hooks = CCC_ROOT / "hooks"
    HOOK_SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("post-tool-use.py", "stop.py"):
        src = repo_hooks / name
        if not src.exists():
            continue
        dst = HOOK_SCRIPTS_DIR / name
        try:
            if not dst.exists() or dst.read_bytes() != src.read_bytes():
                shutil.copy2(src, dst)
                print(f"  [hooks] Synced {name} -> {dst}")
        except OSError as e:
            print(f"  [hooks] Could not copy {name}: {e}")

    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
        else:
            settings = {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [hooks] Could not read settings.json: {e}")
        return

    hooks = settings.setdefault("hooks", {})

    # PostToolUse hook
    post_tool_hooks = hooks.setdefault("PostToolUse", [])
    has_post_tool = any(
        HOOK_MARKER in h.get("command", "")
        for entry in post_tool_hooks
        for h in entry.get("hooks", [])
    )
    if not has_post_tool:
        post_tool_hooks.append({
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": f"python3 {HOOK_SCRIPTS_DIR / 'post-tool-use.py'}"
            }]
        })
        print("  [hooks] Installed PostToolUse hook")

    # Stop hook
    stop_hooks = hooks.setdefault("Stop", [])
    has_stop = any(
        HOOK_MARKER in h.get("command", "")
        for entry in stop_hooks
        for h in entry.get("hooks", [])
    )
    if not has_stop:
        stop_hooks.append({
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": f"python3 {HOOK_SCRIPTS_DIR / 'stop.py'}"
            }]
        })
        print("  [hooks] Installed Stop hook")

    if not has_post_tool or not has_stop:
        tmp_path = settings_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(settings, indent=4) + "\n")
            tmp_path.replace(settings_path)
            print("  [hooks] settings.json updated")
        except OSError as e:
            print(f"  [hooks] Failed to write settings.json: {e}")
            tmp_path.unlink(missing_ok=True)


def main():
    import socketserver
    class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        allow_reuse_address = True
        daemon_threads = True
    ensure_hooks_installed()
    server = ThreadedHTTPServer(("", PORT), LogViewerHandler)
    print(f"Claude Log Viewer running at http://localhost:{PORT}")
    print(f"  Log dir:       {LOG_DIR}")
    print(f"  Fallback:      {FALLBACK_DIR}/claude-issue-*.log")
    print(f"  Conversations: {CONVERSATIONS_DIR}/*.jsonl")
    print(f"  Press Ctrl+C to stop")
    # Warm the metadata cache in the background so the first /api/sessions
    # request returns instantly instead of taking ~3s.
    threading.Thread(target=_warm_cache, daemon=True).start()
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
