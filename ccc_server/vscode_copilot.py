"""Extracted from server.py (originally lines 41400-41589).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import os
import platform
import re
import time

import server as _core

# ---------------------------------------------------------------------------
# VS Code Copilot Chat conversation ingestion (read-only).
#
# GitHub Copilot Chat sessions (chat panel / agent mode) live inside VS
# Code's user-data dir, per workspace:
#   <User>/workspaceStorage/<workspace-hash>/chatSessions/<sessionId>.json
#     — pre-1.109 flat ISerializableChatData, or
#   <User>/workspaceStorage/<workspace-hash>/chatSessions/<sessionId>.jsonl
#     — current append-only ChatSessionOperationLog journal. When both exist
#     for one session the .jsonl wins (matches VS Code's own read order).
#   <User>/workspaceStorage/<workspace-hash>/workspace.json — {"folder":
#     "file:///abs/path"} gives the workspace association (session_cwd).
#   <User>/globalStorage/emptyWindowChatSessions/ — empty-window sessions
#     (same file formats, no workspace.json; session_cwd "").
# <User> roots per OS: ~/Library/Application Support/<APP>/User (macOS),
# ~/.config/<APP>/User (Linux), %APPDATA%/<APP>/User (Windows), with <APP>
# in {"Code", "Code - Insiders", "VSCodium", "Code - OSS"}. The env var
# CCC_VSCODE_USER_DIRS (os.pathsep-separated list of User dirs) replaces the
# defaults entirely — it is also the test injection point.
#
# The state.vscdb chat.ChatSessionStore.index is deliberately NOT read: the
# chatSessions files are the source of truth (the index can disagree), and
# everything CCC needs — a title candidate from the first user message plus
# timestamps — is mined from the files themselves, so skipping the SQLite
# index removes a whole failure mode for no real gain.
#
# The JSONL journal op vocabulary is internal and churning, so replay is
# defensive: snapshot records (carrying a `requests` list) replace state,
# append-ish ops carrying a request extend it, unknown op shapes are
# skipped, and corrupt input (BOM, missing base record, truncated final
# line, empty-stub overwrite) degrades instead of crashing. A journal that
# yields no requests falls back to treating each line's message-ish content
# as best-effort turns; if even that fails the session still gets a row with
# an empty transcript.
#
# Live-flush caveat: VS Code persists chat sessions on store/shutdown, so
# rows are stale while VS Code is open — expected. Listing + transcript
# view only; no spawn / resume support.
# ---------------------------------------------------------------------------

COPILOTCHAT_LIVE_WINDOW_S = 180

_COPILOTCHAT_APP_NAMES = ("Code", "Code - Insiders", "VSCodium", "Code - OSS")
_COPILOTCHAT_SID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Module-level cache for the session-file index so the engine-detection
# probe (_is_copilotchat_session runs for every foreign sid on the claude
# fallback path) stays a dict lookup instead of a workspaceStorage walk.
_COPILOTCHAT_INDEX = None      # {session_id: Path}
_COPILOTCHAT_INDEX_KEY = None  # dirs signature the index was built from
_COPILOTCHAT_INDEX_TS = 0.0
_COPILOTCHAT_INDEX_TTL_S = 30.0


def _copilotchat_user_dirs():
    """VS Code 'User' data dirs to scan. CCC_VSCODE_USER_DIRS overrides the
    per-OS defaults entirely (tests point it at a tmp fixture dir)."""
    raw = os.environ.get("CCC_VSCODE_USER_DIRS", "").strip()
    if raw:
        out = []
        for piece in raw.split(os.pathsep):
            piece = piece.strip()
            if not piece:
                continue
            d = Path(os.path.expanduser(piece))
            try:
                if d.is_dir():
                    out.append(d)
            except OSError:
                continue
        return out
    roots = []
    system = platform.system()
    if system == "Darwin":
        roots.append(Path.home() / "Library" / "Application Support")
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            roots.append(Path(appdata))
    else:
        roots.append(Path.home() / ".config")
    out = []
    for root in roots:
        for app in _COPILOTCHAT_APP_NAMES:
            d = root / app / "User"
            try:
                if d.is_dir():
                    out.append(d)
            except OSError:
                continue
    return out


def _copilotchat_chat_dirs():
    """All chatSessions dirs under the scanned User dirs: one per workspace
    plus each app's empty-window store. Missing stores are skipped."""
    dirs = []
    for user_dir in _copilotchat_user_dirs():
        ws_root = user_dir / "workspaceStorage"
        try:
            workspaces = list(ws_root.iterdir()) if ws_root.is_dir() else []
        except OSError:
            workspaces = []
        for ws in workspaces:
            try:
                chat = ws / "chatSessions"
                if chat.is_dir():
                    dirs.append(chat)
            except OSError:
                continue
        try:
            empty = user_dir / "globalStorage" / "emptyWindowChatSessions"
            if empty.is_dir():
                dirs.append(empty)
        except OSError:
            pass
    return dirs


def _copilotchat_scan_dir(chat_dir):
    """{session_id: file Path} for one chatSessions dir; the .jsonl journal
    wins when both formats exist for a session (VS Code's read order)."""
    found = {}
    try:
        entries = list(chat_dir.iterdir())
    except OSError:
        return found
    for entry in entries:
        name = entry.name
        if name.endswith(".jsonl"):
            sid = name[: -len(".jsonl")]
        elif name.endswith(".json"):
            sid = name[: -len(".json")]
        else:
            continue
        if not sid:
            continue
        existing = found.get(sid)
        if existing is None or (
            existing.suffix != ".jsonl" and entry.suffix == ".jsonl"
        ):
            found[sid] = entry
    return found


def _copilotchat_session_index():
    """Cached {session_id: Path} across every scanned chatSessions dir.
    Rebuilt when the dir set changes or the short TTL expires."""
    global _COPILOTCHAT_INDEX, _COPILOTCHAT_INDEX_KEY, _COPILOTCHAT_INDEX_TS
    dirs = _copilotchat_chat_dirs()
    key = tuple(str(d) for d in dirs)
    now = time.time()
    if (
        _COPILOTCHAT_INDEX is not None
        and _COPILOTCHAT_INDEX_KEY == key
        and now - _COPILOTCHAT_INDEX_TS < _COPILOTCHAT_INDEX_TTL_S
    ):
        return _COPILOTCHAT_INDEX
    index = {}
    for chat_dir in dirs:
        for sid, path in _copilotchat_scan_dir(chat_dir).items():
            existing = index.get(sid)
            if existing is None or (
                existing.suffix != ".jsonl" and path.suffix == ".jsonl"
            ):
                index[sid] = path
    _COPILOTCHAT_INDEX = index
    _COPILOTCHAT_INDEX_KEY = key
    _COPILOTCHAT_INDEX_TS = now
    return index


def _copilotchat_session_file(session_id):
    """Path to a session's chatSessions file, or None. Cheap probe — also
    used by engine detection, so it must return fast for foreign ids."""
    sid = str(session_id or "").strip()
    if not _COPILOTCHAT_SID_RE.match(sid):
        return None
    try:
        return _copilotchat_session_index().get(sid)
    except Exception:
        return None


def _is_copilotchat_session(session_id):
    return _copilotchat_session_file(session_id) is not None

