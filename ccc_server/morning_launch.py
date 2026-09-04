# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 48046-50204).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import collections
import json
import math
import os
import re
import stat
import subprocess
import threading
import time

from ccc_server import core as _core
from ccc_server import github_quota as _github_quota

# How many trailing assistant turns /usage ships for the status-rail
# per-turn token graph. Older turns are noise in the rail; the full history
# stays in the transcript.
USAGE_TURN_SERIES_MAX = 30

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
        f"(from my Morning view in Command Center).\n\n"
        f"## Goal intent\n\n{intent_markdown}\n\n"
        f"## Current strategy\n\n{strategy_text}\n\n"
        f"This is a fresh session for this strategy. Please help me move forward "
        f"on it, asking any clarifying questions first if needed."
    )


def _morning_task_spawn_prompt(goal_name, intent_markdown, task_text, status):
    # Lighter framing for a tactical-task session (not a full strategy).
    status_line = f"## Current status (my note)\n\n{status}\n\n" if status else ""
    return (
        f"You're picking up a focused work session on a task I committed to today "
        f"(from my Morning view in Command Center).\n\n"
        f"## Goal\n\n{goal_name}\n\n"
        f"## Goal intent\n\n{intent_markdown}\n\n"
        f"## Task\n\n{task_text}\n\n"
        f"{status_line}"
        f"This is a fresh session for this task. Please help me move forward on it, "
        f"asking any clarifying questions first if needed."
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


def _log_session_ids(log_path, max_lines=30):
    """Return the set of session_ids that appear in a log's first N lines.

    Resume subprocesses mint a fresh session_id of their own AND reference
    the original session_id they're continuing — both end up in the log
    header. So matching by "is the target sid in this log?" is the right
    contract, not "does the first event have this sid?".
    """
    sids = set()
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
                s = ev.get("session_id")
                if s:
                    sids.add(s)
    except OSError:
        return sids
    return sids


def _resolve_spawn_log_for_session(session_id):
    """Return (log_path, alive) for a CCC-spawned session, or (None, False).

    A single conversation can have multiple spawn logs over its life
    (original spawn + N resumes). We scan all of them and prefer the
    most recent log with a live PID; if none are live we fall back to
    the most recent log so the SSE handler can decide what to do.
    """
    if not session_id:
        return None, False

    candidates = []  # (sort_key, log_path, alive)

    for s in _core._spawned_sessions:
        log = s.get("log")
        if not log:
            continue
        if s.get("session_id") == session_id:
            matches = True
        elif s.get("resumed_sid") == session_id:
            matches = True
        elif s.get("engine") == "codex":
            matches = _core._extract_codex_thread_id_from_log(log) == session_id
        elif s.get("engine") == "gemini":
            matches = _core._extract_gemini_session_id_from_log(log) == session_id
        elif s.get("engine") == "cursor":
            matches = (
                _core._extract_cursor_chat_id_from_log(log) == session_id
                or _core._cursor_session_id_for_spawn_entry(s) == session_id
            )
        else:
            matches = session_id in _log_session_ids(log)
        if matches:
            try:
                alive = _core._poll_spawn_entry(s) is None
            except Exception:
                alive = False
            sort_key = s.get("started", "") or os.path.basename(log)
            candidates.append((sort_key, log, alive))

    try:
        for entry in _core._load_spawn_registry():
            log = entry.get("log")
            if not log:
                continue
            recorded_sid = entry.get("session_id")
            sids_in_log = None
            matches = recorded_sid == session_id
            if not matches:
                if entry.get("engine") == "codex":
                    matches = _core._extract_codex_thread_id_from_log(log) == session_id
                elif entry.get("engine") == "gemini":
                    matches = _core._extract_gemini_session_id_from_log(log) == session_id
                elif entry.get("engine") == "cursor":
                    matches = (
                        _core._extract_cursor_chat_id_from_log(log) == session_id
                        or _core._cursor_session_id_for_spawn_entry(entry) == session_id
                    )
                else:
                    sids_in_log = _log_session_ids(log)
                    matches = session_id in sids_in_log
            if matches:
                pid = entry.get("pid")
                alive = bool(pid and _pid_alive(pid))
                sort_key = entry.get("spawned_at", "") or os.path.basename(log)
                candidates.append((sort_key, log, alive))
    except Exception:
        pass

    if not candidates:
        return None, False
    # Dedupe by log path (in-memory + registry can both report the same log).
    seen = {}
    for key, log, alive in candidates:
        prev = seen.get(log)
        if prev is None or (alive and not prev[1]) or key > prev[0]:
            seen[log] = (key, alive)
    deduped = [(k, log, a) for log, (k, a) in seen.items()]
    # Prefer alive, then most-recent.
    deduped.sort(key=lambda c: (1 if c[2] else 0, c[0]), reverse=True)
    _, log, alive = deduped[0]
    return log, alive


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


class _SpawnEventNormalizer:
    """Stateful stream-json normalizer for one spawn-log connection.

    Claude's partial stream events do not repeat the message id on every
    token, while the later completed ``assistant`` event contains the whole
    text again. Remembering the active message lets CCC paint each delta as
    it arrives and suppress only the duplicated finalized text. Tool blocks
    in that finalized event remain visible.
    """

    def __init__(self):
        self._message_ids_by_stream = {}
        self._partial_text_message_ids = set()
        self._partial_text_stream_keys = set()

    @staticmethod
    def _parent_tool_use_id(ev, stream_event=None, message=None):
        for candidate in (ev, stream_event, message):
            if not isinstance(candidate, dict):
                continue
            parent_id = candidate.get("parent_tool_use_id")
            if parent_id:
                return str(parent_id)
        return ""

    def normalize(self, ev):
        if not isinstance(ev, dict):
            return None
        if ev.get("type") == "stream_event":
            stream_event = ev.get("event") or {}
            if not isinstance(stream_event, dict):
                return None
            event_type = stream_event.get("type")
            message = stream_event.get("message") or {}
            parent_tool_use_id = self._parent_tool_use_id(ev, stream_event, message)
            stream_key = parent_tool_use_id or "__root__"
            if event_type == "message_start":
                if isinstance(message, dict):
                    self._message_ids_by_stream[stream_key] = str(message.get("id") or "")
                return None
            if event_type != "content_block_delta":
                return None
            delta = stream_event.get("delta") or {}
            if not isinstance(delta, dict) or delta.get("type") != "text_delta":
                return None
            text = delta.get("text") or ""
            if not text:
                return None
            message_id = (
                self._message_ids_by_stream.get(stream_key)
                or str(ev.get("message_id") or stream_event.get("message_id") or "claude-stream")
            )
            self._partial_text_message_ids.add(message_id)
            self._partial_text_stream_keys.add(stream_key)
            normalized = {
                "type": "assistant_block",
                "message_id": message_id,
                "blocks": [{"type": "text", "text": text}],
                "partial": True,
            }
            if parent_tool_use_id:
                normalized["parent_tool_use_id"] = parent_tool_use_id
            return normalized

        if ev.get("type") == "assistant":
            message = ev.get("message") or {}
            message_id = str(message.get("id") or "") if isinstance(message, dict) else ""
            parent_tool_use_id = self._parent_tool_use_id(ev, message=message)
            stream_key = parent_tool_use_id or "__root__"
            if (
                message_id in self._partial_text_message_ids
                or stream_key in self._partial_text_stream_keys
            ):
                content = message.get("content") or []
                remaining = [
                    block for block in content
                    if not isinstance(block, dict) or block.get("type") != "text"
                ]
                if not remaining:
                    self._partial_text_stream_keys.discard(stream_key)
                    self._partial_text_message_ids.discard(message_id)
                    return None
                ev = dict(ev)
                ev["message"] = dict(message, content=remaining)
                self._partial_text_stream_keys.discard(stream_key)
                self._partial_text_message_ids.discard(message_id)
        return _normalize_spawn_event(ev)


def _normalize_spawn_event(ev):
    """Boil a stream-json event down to the minimum the UI needs.

    We intentionally drop fields the UI doesn't render (full hook bodies,
    long tool inputs) so the SSE payload stays small and the browser
    doesn't have to filter on its end. Returns None for events the UI
    should skip entirely.
    """
    if not isinstance(ev, dict):
        return None
    t = ev.get("type")
    if t == "message" and ev.get("role") == "assistant" and isinstance(ev.get("content"), str):
        text = ev.get("content") or ""
        if not text:
            return None
        return {
            "type": "assistant_block",
            "message_id": "gemini-stream",
            "blocks": [{"type": "text", "text": text}],
        }
    if (not t or t == "message") and _core._cursor_event_role(ev) == "assistant":
        blocks = []
        for c in _core._cursor_content_blocks(ev):
            ct = c.get("type")
            if ct == "text":
                blocks.append({"type": "text", "text": c.get("text", "")})
            elif ct == "tool_use":
                inp = _core._cursor_tool_args(c)
                summary = _core._tool_use_detail(_core._cursor_tool_name(c), inp, max_len=160)
                if not summary:
                    summary = (
                        inp.get("file_path") or inp.get("path")
                        or inp.get("pattern") or inp.get("command")
                        or inp.get("description") or ""
                    )
                blocks.append({
                    "type": "tool_use",
                    "name": _core._cursor_tool_name(c),
                    "id": c.get("id", ""),
                    "summary": _core._prompt_fragment(str(summary), 160) if summary else "",
                })
        if not blocks:
            return None
        return {
            "type": "assistant_block",
            "message_id": ev.get("id") or ev.get("message_id") or "cursor-stream",
            "blocks": blocks,
        }
    if t == "tool_use":
        params = ev.get("parameters") if isinstance(ev.get("parameters"), dict) else {}
        name = ev.get("tool_name") or "tool"
        summary = _core._tool_use_detail(name, params, max_len=160)
        if not summary:
            summary = params.get("description") or params.get("command") or ""
        return {
            "type": "assistant_block",
            "message_id": "gemini-stream",
            "blocks": [{
                "type": "tool_use",
                "name": name,
                "id": ev.get("tool_id") or "",
                "summary": _core._prompt_fragment(str(summary), 160) if summary else "",
            }],
        }
    if t == "result" and ("status" in ev or "stats" in ev):
        return {
            "type": "result",
            "subtype": ev.get("status", ""),
            "duration_ms": (ev.get("stats") or {}).get("duration_ms") if isinstance(ev.get("stats"), dict) else None,
            "num_turns": None,
        }
    if t == "assistant":
        msg = ev.get("message") or {}
        content = msg.get("content") or []
        blocks = []
        for c in content:
            if not isinstance(c, dict):
                continue
            ct = c.get("type")
            if ct == "text":
                blocks.append({"type": "text", "text": c.get("text", "")})
            elif ct == "tool_use":
                tu = {
                    "type": "tool_use",
                    "name": c.get("name", ""),
                    "id": c.get("id", ""),
                }
                # Surface a one-line summary for common tools so the live
                # bubble can show "⚙ Read foo.py" instead of an opaque
                # spinner. Trim aggressively — full inputs land in the
                # JSONL render at end-of-turn.
                inp = c.get("input") or {}
                if isinstance(inp, dict):
                    summary = _core._tool_use_detail(c.get("name", ""), inp, max_len=160)
                    if not summary:
                        summary = (
                            inp.get("file_path") or inp.get("path")
                            or inp.get("pattern") or inp.get("command")
                            or inp.get("description") or ""
                        )
                    if summary:
                        tu["summary"] = _core._prompt_fragment(str(summary), 160)
                blocks.append(tu)
            elif ct == "thinking":
                blocks.append({"type": "thinking"})
        if not blocks:
            return None
        # Claude Code stream-json sets `parent_tool_use_id` on assistant
        # events emitted by a Task-tool subagent — pass it through so the
        # client can render those bubbles distinctly (label "subagent",
        # different border) instead of letting them masquerade as the
        # parent's own mid-turn content.
        out = {
            "type": "assistant_block",
            "message_id": msg.get("id", ""),
            "blocks": blocks,
        }
        ptui = ev.get("parent_tool_use_id") or msg.get("parent_tool_use_id")
        if ptui:
            out["parent_tool_use_id"] = ptui
        return out
    if t == "result":
        return {
            "type": "result",
            "subtype": ev.get("subtype", ""),
            "duration_ms": ev.get("duration_ms"),
            "num_turns": ev.get("num_turns"),
        }
    return None


def parse_conversation_by_sid(session_id, after_line=0):
    """Like parse_conversation() but searches every project dir for the sid.

    Morning-spawned sessions can land in any ~/.claude/projects/<slug>/
    depending on spawn cwd, so repo-specific lookup misses them.
    """
    if _core._is_devin_cli_session(session_id):
        return _core._parse_devin_cli_conversation(session_id, after_line=after_line)
    if not _core.PROJECTS_ROOT.is_dir():
        stub = _core._registry_only_conversation_stub(session_id, after_line=after_line)
        return stub or {"events": [], "last_line": 0}
    for pd in _core.PROJECTS_ROOT.iterdir():
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
                        parsed = _core._parse_conversation_event(ev, line_num)
                        if parsed:
                            events.append(parsed)
            except OSError:
                break
            events_copy = list(events)
            events_copy = _core._merge_synthetic_conversation_events(events_copy, _core._get_queued_events_for_session(session_id))
            return {"events": events_copy, "last_line": line_num}
    stub = _core._registry_only_conversation_stub(session_id, after_line=after_line)
    return stub or {"events": [], "last_line": 0}



# Patterns for the session-timeline endpoint. Bash command prefixes that
# represent shipping-relevant events; we capture them so the conv pane can
# render a chronological strip ("Turn 4: commit, Turn 7: push, Turn 12: PR").
# `\bgit\s+(?:-\w+\s+\S+\s+)*commit\b` matches:
#   git commit ...
#   git -C /path commit ...
#   git -c user.email=foo commit ...
#   cd /path && git commit ...   (\bgit matches mid-string)
# `cd /abs/path` and `git -C /abs/path ...` — capture the path argument so
# we can attribute the session's edits to a repo even when its launch cwd
# is an empty stub directory.
# Match `cd` / `git -C` only when they start a command — i.e., at the
# beginning of the bash string or after a separator (`;`, `&&`, `||`,
# newline). Without this, a quoted argument like `grep 'cd /path'`
# false-positives as the session having relocated to that path.
_BASH_CD_RE = re.compile(r"(?:^|\n|;|&&|\|\|)\s*cd\s+(?:--\s+)?([^\s;&|<>]+)")
_BASH_GIT_C_RE = re.compile(r"(?:^|\n|;|&&|\|\|)\s*git\s+-C\s+([^\s;&|<>]+)")
# The native EnterWorktree tool relocates tool-execution cwd without ever
# running a Bash `cd`/`git -C`, so it needs its own relocation signal. A
# bare `name` input doesn't tell us the resolved path up front — only the
# tool_result text does ("Created worktree at <path> on branch <branch>").
_ENTER_WORKTREE_RESULT_RE = re.compile(r"Created worktree at (\S+) on branch")

_TIMELINE_COMMIT_RE = re.compile(r"\bgit\s+(?:-\w+\s+\S+\s+)*commit\b")
_TIMELINE_COMMIT_MSG_RE = re.compile(r"-m\s+[\"']([^\"']{1,200})[\"']")
_TIMELINE_PUSH_RE = re.compile(r"\bgit\s+(?:-\w+\s+\S+\s+)*push\b")
_TIMELINE_PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")
_TIMELINE_PR_TITLE_RE = re.compile(r"--title\s+[\"']([^\"']{1,200})[\"']")
_TIMELINE_PR_NUMBER_FROM_URL_RE = re.compile(r"/pull/(\d+)")
# `git commit` output starts with `[branch sha] subject` — capture both.
_TIMELINE_COMMIT_RESULT_RE = re.compile(r"\[[^\]]+\s+([0-9a-f]{7,40})\]\s*(.+)")


# Process-wide path→toplevel memo for _git_toplevel_for_path. Toplevels are
# stable for a process's lifetime; sharing one dict across callers kills the
# repeat `git rev-parse` subprocess each per-call cache used to re-fire.
_GIT_TOPLEVEL_CACHE = {}
_GIT_TOPLEVEL_CACHE_MAX = 2000
# Thirteen callers across server.py and every engine finder open a THROWAWAY
# `git_top_cache = {}` per scan, so each one re-forked `git rev-parse
# --show-toplevel` for directories the process had already resolved -- and
# re-forked them again on the next request. A directory's git toplevel does
# not change under a running server, so memoise the subprocess result itself,
# process-wide, below whatever per-call dict the caller passed. Callers keep
# their local fast path; nobody pays for the same fork twice.
_GIT_TOPLEVEL_RESULTS = {}
_GIT_TOPLEVEL_RESULTS_MAX = 4000
_GIT_TOPLEVEL_RESULTS_LOCK = threading.Lock()


def _git_toplevel_for_path(path, cache=None):
    """Return the git toplevel for `path` (the dir if it exists, else its
    closest existing ancestor). Cached per-call so a session that touched
    100 files in the same repo only shells out once; callers that pass no
    dict share the process-wide _GIT_TOPLEVEL_CACHE.

    Display-only: callers must NOT use this to dispatch git writes. The
    answer is inferred from what tool calls *referenced*, which can include
    files that don't exist yet (e.g. a new file path passed to Write).
    """
    if cache is None:
        cache = _GIT_TOPLEVEL_CACHE
    try:
        p = Path(path).expanduser()
    except (ValueError, OSError):
        return None
    # Walk up to the closest existing ancestor — git rev-parse needs a real
    # directory to start from. New-file paths (Write to a not-yet-created
    # file) still resolve via their parent.
    probe = p if p.exists() else None
    if probe is None:
        for ancestor in p.parents:
            if ancestor.exists():
                probe = ancestor
                break
    if probe is None:
        return None
    if not probe.is_dir():
        probe = probe.parent
    key = str(probe)
    if key in cache:
        return cache[key]
    # Fast path: if `probe` sits inside a toplevel we already resolved this
    # scan, it shares that toplevel — skip the git shell-out. Sessions in one
    # repo touch many distinct subdirs, so without this a cold session scan
    # fires hundreds of `git rev-parse` subprocesses (≈9s for ~400 sessions);
    # this collapses them to one per repo. Tops are tracked under a sentinel
    # key (real keys are always absolute paths, so "\x00tops" never collides).
    tops = cache.get("\x00tops")
    if tops is None:
        tops = cache["\x00tops"] = []
    for top in tops:
        if key == top or key.startswith(top + os.sep):
            cache[key] = top
            return top
    with _GIT_TOPLEVEL_RESULTS_LOCK:
        memo_hit = key in _GIT_TOPLEVEL_RESULTS
        memo_top = _GIT_TOPLEVEL_RESULTS.get(key)
    if memo_hit:
        top = memo_top
    else:
        try:
            r = subprocess.run(
                ["git", "-C", key, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=2,
            )
            top = r.stdout.strip() if r.returncode == 0 else None
        except (subprocess.SubprocessError, OSError):
            top = None
        with _GIT_TOPLEVEL_RESULTS_LOCK:
            if len(_GIT_TOPLEVEL_RESULTS) > _GIT_TOPLEVEL_RESULTS_MAX:
                _GIT_TOPLEVEL_RESULTS.clear()
            _GIT_TOPLEVEL_RESULTS[key] = top
    if len(cache) > _GIT_TOPLEVEL_CACHE_MAX:
        # Evict the oldest half instead of clearing outright. A wholesale
        # clear also dropped the "\x00tops" prefix list, which is what lets a
        # subdir resolve without shelling out -- so a scan that overflowed the
        # bound went straight back to one `git rev-parse` fork per directory
        # and thrashed there for the rest of the walk (observed: the same
        # repo path re-forking 6x in a single /api/sessions load). Keep tops.
        victims = [k for k in cache if k != "\x00tops"][:_GIT_TOPLEVEL_CACHE_MAX // 2]
        for victim in victims:
            cache.pop(victim, None)
    cache[key] = top
    if top and top not in tops:
        tops.append(top)
    return top


def _scan_session_tool_paths(session_id, max_events=400):
    """Walk a session's JSONL and collect absolute paths it touched.

    Returns a tuple (file_paths, cd_targets) where:
    - file_paths: paths from Read/Edit/Write `file_path` (with duplicates).
    - cd_targets: paths from Bash `cd <path>` and `git -C <path>` (deduped,
      preserving discovery order). These are *strong* hints about where
      the session relocated to — useful for remapping stale file_paths
      whose prefix points at an empty stub directory.

    Capped at ~400 assistant events for bounded latency on long sessions.
    """
    if _core._is_cursor_session(session_id):
        path = _core._cursor_transcript_path(session_id)
        file_paths = []
        cd_targets = []
        cd_seen = set()
        seen_events = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if seen_events >= max_events:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if _core._cursor_event_role(ev) != "assistant":
                        continue
                    seen_events += 1
                    for block in _core._cursor_content_blocks(ev):
                        if block.get("type") != "tool_use":
                            continue
                        args = _core._cursor_tool_args(block)
                        for key in ("file_path", "target_file", "path", "notebook_path"):
                            raw = args.get(key)
                            if isinstance(raw, str) and raw.startswith("/"):
                                file_paths.append(raw)
                        cmd = _core._cursor_tool_command(block)
                        if cmd:
                            for m in _BASH_CD_RE.finditer(cmd):
                                cd_path = m.group(1).strip("'\"")
                                if (cd_path.startswith("/") or cd_path.startswith("~")) and cd_path not in cd_seen:
                                    cd_seen.add(cd_path)
                                    cd_targets.append(cd_path)
                            for m in _BASH_GIT_C_RE.finditer(cmd):
                                gc_path = m.group(1).strip("'\"")
                                if (gc_path.startswith("/") or gc_path.startswith("~")) and gc_path not in cd_seen:
                                    cd_seen.add(gc_path)
                                    cd_targets.append(gc_path)
        except OSError:
            return [], []
        return file_paths, cd_targets

    if _core._is_antigravity_session(session_id):
        path = _core._antigravity_transcript_path(session_id)
        file_paths = []
        cd_targets = []
        cd_seen = set()
        seen_events = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if seen_events >= max_events:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") != "PLANNER_RESPONSE":
                        continue
                    seen_events += 1
                    for call in ev.get("tool_calls") or []:
                        if not isinstance(call, dict):
                            continue
                        name = _core._antigravity_tool_name(call).lower()
                        args = _core._antigravity_tool_args(call)
                        for key in ("AbsolutePath", "TargetFile", "File", "Path"):
                            raw = args.get(key)
                            path_text = _core._antigravity_normalize_path(raw)
                            if path_text:
                                file_paths.append(path_text)
                        if name == "run_command":
                            cmd = _core._antigravity_tool_command(call)
                            for m in _BASH_CD_RE.finditer(cmd):
                                cd_path = _core._antigravity_normalize_path(m.group(1).strip("'\""))
                                if cd_path and cd_path not in cd_seen:
                                    cd_seen.add(cd_path)
                                    cd_targets.append(cd_path)
                            for m in _BASH_GIT_C_RE.finditer(cmd):
                                gc_path = _core._antigravity_normalize_path(m.group(1).strip("'\""))
                                if gc_path and gc_path not in cd_seen:
                                    cd_seen.add(gc_path)
                                    cd_targets.append(gc_path)
        except OSError:
            return [], []
        return file_paths, cd_targets

    if not _core.PROJECTS_ROOT.is_dir():
        return [], []
    jsonl = None
    for pd in _core.PROJECTS_ROOT.iterdir():
        if not pd.is_dir():
            continue
        cand = pd / f"{session_id}.jsonl"
        if cand.is_file():
            jsonl = cand
            break
    if not jsonl:
        return [], []
    file_paths = []
    cd_targets = []
    cd_seen = set()
    pending_worktree_ids = set()
    seen_events = 0
    try:
        with open(jsonl, "r") as f:
            for line in f:
                if seen_events >= max_events:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_type = ev.get("type")
                if ev_type == "assistant":
                    if ev.get("isSidechain"):
                        continue
                    seen_events += 1
                    msg = _core._safe_parse_message(ev.get("message", {}))
                    for block in msg.get("content", []):
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        inp = block.get("input") or {}
                        if name in ("Read", "Edit", "Write", "NotebookEdit"):
                            fp = inp.get("file_path")
                            if isinstance(fp, str) and fp.startswith("/"):
                                file_paths.append(fp)
                        elif name == "Bash":
                            cmd = inp.get("command", "")
                            if not isinstance(cmd, str):
                                continue
                            for m in _BASH_CD_RE.finditer(cmd):
                                cd_path = m.group(1).strip("'\"")
                                if (cd_path.startswith("/") or cd_path.startswith("~")) and cd_path not in cd_seen:
                                    cd_seen.add(cd_path)
                                    cd_targets.append(cd_path)
                            for m in _BASH_GIT_C_RE.finditer(cmd):
                                gc_path = m.group(1).strip("'\"")
                                if (gc_path.startswith("/") or gc_path.startswith("~")) and gc_path not in cd_seen:
                                    cd_seen.add(gc_path)
                                    cd_targets.append(gc_path)
                        elif name == "EnterWorktree":
                            # An explicit `path` input names the target
                            # directly; a bare `name` only resolves once we
                            # see the matching tool_result below.
                            wt_path = inp.get("path")
                            if isinstance(wt_path, str) and wt_path.startswith("/"):
                                if wt_path not in cd_seen:
                                    cd_seen.add(wt_path)
                                    cd_targets.append(wt_path)
                            else:
                                tu_id = block.get("id")
                                if tu_id:
                                    pending_worktree_ids.add(tu_id)
                elif ev_type == "user" and pending_worktree_ids:
                    msg_content = ev.get("message", {}).get("content")
                    if not isinstance(msg_content, list):
                        continue
                    for sub in msg_content:
                        if not isinstance(sub, dict) or sub.get("type") != "tool_result":
                            continue
                        tu_id = sub.get("tool_use_id", "")
                        if tu_id not in pending_worktree_ids:
                            continue
                        pending_worktree_ids.discard(tu_id)
                        rc = sub.get("content")
                        text = ""
                        if isinstance(rc, str):
                            text = rc
                        elif isinstance(rc, list):
                            text = "\n".join(
                                b.get("text", "") for b in rc
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        m = _ENTER_WORKTREE_RESULT_RE.search(text)
                        if m:
                            wt_path = m.group(1)
                            if wt_path not in cd_seen:
                                cd_seen.add(wt_path)
                                cd_targets.append(wt_path)
    except OSError:
        return [], []
    return file_paths, cd_targets


def _remap_stale_path(path, literal_cwd, cd_targets):
    """If `path` is rooted at the session's launch cwd but the file no
    longer exists there, try prefix-substitution against each known
    `cd <target>` redirect — return the first variant that exists.

    This catches the BYM+Finie pattern: session launched from
    `~/my-finance-app` (an empty stub), then ran `cd ~/Apps/BYM+Finie`,
    then issued Reads with paths like `~/my-finance-app/apps/...` which
    actually live under `~/Apps/BYM+Finie/apps/...`.

    Returns the remapped path or None if no candidate works.
    """
    if not literal_cwd or not path or not path.startswith(literal_cwd):
        return None
    try:
        if Path(path).exists():
            return None
    except OSError:
        return None
    suffix = path[len(literal_cwd):].lstrip("/")
    for target in cd_targets:
        try:
            t = Path(target).expanduser()
        except (ValueError, OSError):
            continue
        if not t.is_dir():
            continue
        candidate = t / suffix
        try:
            if candidate.exists():
                return str(candidate)
        except OSError:
            continue
    return None


# Cache effective-repo inference per (session_id, jsonl_mtime, literal_cwd,
# exclude_top). Each call walks up to 400 JSONL events + does git shellouts;
# the conversation-list endpoint runs this for every session on every 10s
# refresh, so a bare cache here knocks the hot-path latency down by an order
# of magnitude. Invalidated naturally when the JSONL appends new events.
_EFFECTIVE_REPO_CACHE = {}
# Newest entry per session id, for the revalidation window below, plus a hard
# cap so per-turn mtime keys can't grow the dict without bound.
_EFFECTIVE_REPO_SID_LATEST = {}
_EFFECTIVE_REPO_REVALIDATE_S = 30.0
_EFFECTIVE_REPO_CACHE_MAX = 4000


def _effective_repo_cache_put(cache_key, value):
    if len(_EFFECTIVE_REPO_CACHE) > _EFFECTIVE_REPO_CACHE_MAX:
        _EFFECTIVE_REPO_CACHE.clear()
        _EFFECTIVE_REPO_SID_LATEST.clear()
    _EFFECTIVE_REPO_CACHE[cache_key] = value
    _EFFECTIVE_REPO_SID_LATEST[cache_key[0]] = (cache_key[1], time.time(), value)


def _infer_effective_repo(session_id, literal_cwd=None, exclude_top=None, jsonl_mtime=None):
    """From a session's tool-call file paths, find the dominant git repo.

    Returns dict with keys: top, count, total, branch, kind, ahead, behind
    — or None if no repo dominates the resolved paths (or no paths).

    Stale-path remap: a session whose launch cwd is an empty stub may
    issue Reads with paths under that stub that actually live in another
    repo it `cd`'d into. We try prefix substitution against known cd
    targets so those paths still count as evidence.

    `exclude_top` lets callers say "I already know cwd resolves to repo X,
    only surface inference if a *different* repo dominates."

    `jsonl_mtime` lets callers (e.g. find_all_conversations) pass the
    mtime they already stat'd, skipping the PROJECTS_ROOT walk that
    otherwise dominates cache-hit cost for batch users.
    """
    # Cache key: jsonl mtime makes the entry self-invalidate when new
    # tool calls land. literal_cwd / exclude_top affect the result so
    # they're part of the key.
    if jsonl_mtime is None:
        jsonl_mtime = 0.0
        if _core.PROJECTS_ROOT.is_dir():
            for pd in _core.PROJECTS_ROOT.iterdir():
                if not pd.is_dir():
                    continue
                cand = pd / f"{session_id}.jsonl"
                if cand.is_file():
                    try:
                        jsonl_mtime = cand.stat().st_mtime
                    except OSError:
                        jsonl_mtime = 0.0
                    break
    cache_key = (session_id, jsonl_mtime, literal_cwd, exclude_top)
    if cache_key in _EFFECTIVE_REPO_CACHE:
        return _EFFECTIVE_REPO_CACHE[cache_key]
    # Active sessions append to their JSONL every turn, so the exact-key
    # entry above misses on every poll. Re-walking tool paths + re-spawning
    # git on that cadence is the CPU spiral — the dominant repo changes on
    # minute timescales at best. Serve the newest per-session entry for up
    # to 30s instead of re-inferring on every poll.
    recent = _EFFECTIVE_REPO_SID_LATEST.get(session_id)
    if recent is not None:
        r_mtime, r_at, r_value = recent
        if r_mtime != jsonl_mtime and (time.time() - r_at) < _EFFECTIVE_REPO_REVALIDATE_S:
            return r_value

    file_paths, cd_targets = _core._scan_session_tool_paths(session_id)
    if not file_paths and not cd_targets:
        _effective_repo_cache_put(cache_key, None)
        return None

    # When the literal cwd is itself a worktree, the row pill already
    # shows the correct branch. Skip inference so brief cd's into the
    # main repo or sibling repos don't override the worktree label.
    if literal_cwd:
        try:
            if (Path(literal_cwd) / ".git").is_file():
                _effective_repo_cache_put(cache_key, None)
                return None
        except OSError:
            pass

    cache = _GIT_TOPLEVEL_CACHE

    def _build_result(top, count, total):
        def git(*args, timeout=2):
            try:
                r = subprocess.run(
                    ["git", "-C", top, *args],
                    capture_output=True, text=True, timeout=timeout,
                )
                if r.returncode == 0:
                    return r.stdout.strip()
            except (subprocess.SubprocessError, OSError):
                pass
            return None
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            branch = None
        upstream = git("rev-parse", "--abbrev-ref", "@{u}")
        base = upstream or "main"
        ahead = behind = None
        rl = git("rev-list", "--left-right", "--count", f"{base}...HEAD")
        if rl:
            try:
                b_str, a_str = rl.split()
                behind = int(b_str)
                ahead = int(a_str)
            except (ValueError, IndexError):
                pass
        kind = "clone"
        try:
            gp = Path(top) / ".git"
            if gp.is_file():
                kind = "worktree"
        except OSError:
            pass
        return {
            "top": top, "count": count, "total": total,
            "branch": branch, "kind": kind,
            "ahead": ahead, "behind": behind,
        }

    # Worktree shortcut: if the session explicitly cd'd into a registered
    # sibling worktree of its launch repo, surface that worktree directly
    # instead of relying on the count heuristic. The count path treats
    # Read/Edit hits in the launch cwd as overwhelming evidence and
    # excludes that repo as the cwd, dropping a clear sibling-worktree
    # signal on the floor (see the "drifted into a worktree" case where a
    # session reads README.md many times in the shared clone but is
    # actively editing in `<repo>-wt-<name>`).
    #
    # Only redirect *into* true worktrees (`.git` is a file) — a session
    # launched in a worktree that briefly cd's back to the shared clone
    # shouldn't be reclassified as living on main; the launch worktree is
    # still the right answer.
    if exclude_top and cd_targets:
        siblings = set()
        for wt in _list_worktrees(exclude_top):
            wt_path = wt.get("path")
            if not wt_path or wt_path == exclude_top:
                continue
            try:
                if (Path(wt_path) / ".git").is_file():
                    siblings.add(wt_path)
            except OSError:
                continue
        if siblings:
            matches = 0
            picked = None
            for target in cd_targets:
                t_top = _git_toplevel_for_path(target, cache)
                if t_top and t_top in siblings:
                    matches += 1
                    picked = t_top  # last match wins → most recent cd
            if picked:
                result = _build_result(picked, matches, len(cd_targets))
                _effective_repo_cache_put(cache_key, result)
                return result

    counts = {}

    # Strong evidence: every cd/git-C target counts once. If the session
    # explicitly relocated, that's a clear "I'm working here" signal.
    for target in cd_targets:
        top = _git_toplevel_for_path(target, cache)
        if top:
            counts[top] = counts.get(top, 0) + 1

    # File-path evidence with stale-path remap fallback.
    for raw in file_paths:
        top = _git_toplevel_for_path(raw, cache)
        if not top:
            remapped = _remap_stale_path(raw, literal_cwd, cd_targets)
            if remapped:
                top = _git_toplevel_for_path(remapped, cache)
        if top:
            counts[top] = counts.get(top, 0) + 1

    if not counts:
        _effective_repo_cache_put(cache_key, None)
        return None
    total = sum(counts.values())
    top, count = max(counts.items(), key=lambda kv: kv[1])
    # Need at least 2 evidence points so a single incidental match doesn't
    # win, AND >50% of resolved paths so a clear winner exists.
    if count < 2 or count * 2 <= total:
        _effective_repo_cache_put(cache_key, None)
        return None
    if exclude_top and top == exclude_top:
        _effective_repo_cache_put(cache_key, None)
        return None

    result = _build_result(top, count, total)
    _effective_repo_cache_put(cache_key, result)
    return result


def _worktree_is_dirty(path):
    """True if `git status --porcelain` reports any change in this worktree.

    Best-effort with a short timeout — a hung filesystem can't be allowed
    to block the modal render. Bare exceptions => report as not-dirty so
    we don't flag healthy worktrees just because the check timed out.
    """
    if not path:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", path, "status", "--porcelain"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0:
            return False
        return bool(r.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


_OPEN_PRS_CACHE = {}  # legacy alias; the live cache is ccc_server/github_quota.py


def _open_prs_cached(repo_top):
    """Return open PRs for a repo, served from the shared GraphQL-aware cache.

    `gh pr list` with `statusCheckRollup` is the most expensive GraphQL call
    CCC makes (2.9 points, measured -- lane W6-1). It used to live behind a
    30s memory-only cache here AND a second one in ccc_server/fleet.py, with
    no single-flight, so the worktrees modal and a fleet scan each paid full
    price and concurrent requests multiplied it. Both now share
    github_quota.open_prs: one TTL (CCC_GH_PR_TTL_S, default 300s), one
    in-flight fetch per repo.

    Empty list on any failure (no `gh`, no GitHub remote, no auth, network
    blip) -- the worktrees modal must keep working without GitHub access.
    """
    if not repo_top:
        return []
    prs, _error = _github_quota.open_prs(repo_top, checks=True, timeout=8)
    out = []
    for p in prs:
        if not p.get("number"):
            continue
        out.append({
            "number": int(p.get("number") or 0),
            "title": p.get("title") or "",
            "headRefName": p.get("headRefName") or "",
            "isDraft": bool(p.get("isDraft")),
            "url": p.get("url") or "",
            "updatedAt": p.get("updatedAt") or "",
            "createdAt": p.get("createdAt") or "",
            "statusCheckRollup": p.get("statusCheckRollup") or [],
            "mergeable": p.get("mergeable") or "",
            "reviewDecision": p.get("reviewDecision") or "",
        })
    return out


# path -> (last_session_event_ts, dirty, polled_at). The sidebar list
# refreshes every 10s and may include 20+ sessions; a bare git shellout
# per row would dominate the response. Two layers:
#   * Hard floor: never shell out twice for the same path inside 5s.
#     Multiple sessions sharing a worktree dedupe inside one response,
#     and active paths still cap at one shellout per poll.
#   * Soft TTL: between 5s and 30s, only shell out if the session's
#     last meaningful event has advanced — the user's "if no update,
#     don't re-poll" rule. Past 30s we re-poll regardless to catch
#     commits that happen outside the agent (manual commit in another
#     shell).
_WORKTREE_DIRTY_CACHE = {}
_WORKTREE_DIRTY_FLOOR = 5.0
_WORKTREE_DIRTY_TTL = 30.0


def _worktree_dirty_cached(path, event_ts):
    if not path:
        return False
    now = time.time()
    hit = _WORKTREE_DIRTY_CACHE.get(path)
    if hit is not None:
        cached_event_ts, cached_dirty, polled_at = hit
        age = now - polled_at
        if age < _WORKTREE_DIRTY_FLOOR:
            return cached_dirty
        if age < _WORKTREE_DIRTY_TTL and cached_event_ts == event_ts:
            return cached_dirty
    dirty = _worktree_is_dirty(path)
    _WORKTREE_DIRTY_CACHE[path] = (event_ts, dirty, now)
    return dirty


def list_repo_worktrees(repo_top, include_prs=True):
    """Return all worktrees for a repo with a `dirty` flag (uncommitted
    changes). Powers the topbar's "open worktrees" modal.

    Also attaches matching open-PR metadata: each worktree gets a `pr`
    field (or None) when its branch matches an open PR's head ref, and
    the response includes `orphan_prs` for open PRs whose branch has no
    local worktree.

    `include_prs=False` skips the `gh pr list` subprocess entirely. The
    Fleet view's first pass uses it: `gh` is a network round-trip (~5s
    cold) and a fleet scan touches every mapped repo, so PR data is
    fetched in a second, enriching pass rather than blocking first paint.
    """
    repo_top = _core.resolve_repo_path(repo_top)
    wts = _list_worktrees(repo_top)
    dirty_n = 0
    agent_n = 0
    for wt in wts:
        wt["dirty"] = _worktree_is_dirty(wt.get("path"))
        if wt["dirty"]:
            dirty_n += 1
        reason = (wt.get("lock_reason") or "").lower()
        wt["is_agent"] = reason.startswith("claude agent")
        if wt["is_agent"]:
            agent_n += 1

    prs = _open_prs_cached(repo_top) if include_prs else []
    pr_by_branch = {p["headRefName"]: p for p in prs if p.get("headRefName")}
    matched_branches = set()
    for wt in wts:
        branch = wt.get("branch")
        pr = pr_by_branch.get(branch) if branch else None
        wt["pr"] = pr
        if pr:
            matched_branches.add(branch)
    orphan_prs = [p for p in prs if p.get("headRefName") not in matched_branches]

    return {
        "repo": repo_top,
        "worktrees": wts,
        "total": len(wts),
        "dirty_count": dirty_n,
        "agent_count": agent_n,
        "open_prs_count": len(prs),
        "orphan_prs": orphan_prs,
        # Distinguishes "checked, found none" from "not checked yet" so the
        # Fleet view's first pass cannot render an absence as a fact.
        "prs_skipped": not include_prs,
    }


# One `git worktree list` per repo per scan, not per row. An /api/sessions
# load fired 45 identical `git -C <same repo> worktree list --porcelain`
# subprocesses; the answer cannot change 45 times inside one request. Short
# TTL so a freshly added worktree still shows up on the next poll.
_WORKTREE_LIST_CACHE = {}
_WORKTREE_LIST_TTL_S = 10.0
_WORKTREE_LIST_LOCK = threading.Lock()


def _list_worktrees(repo_top):
    """Run `git worktree list --porcelain` for a repo and return its
    worktrees as a list of dicts: {path, branch, detached, locked,
    lock_reason}. The lock_reason often distinguishes user-created
    worktrees from subagent-spawned ones — superpowers / orchestration
    skills typically lock with a reason starting with "claude agent".

    Returns [] on any failure.
    """
    if not repo_top:
        return []
    now = time.time()
    cache_key = str(repo_top)
    with _WORKTREE_LIST_LOCK:
        hit = _WORKTREE_LIST_CACHE.get(cache_key)
        if hit is not None and now - hit[0] < _WORKTREE_LIST_TTL_S:
            return hit[1]
    try:
        r = subprocess.run(
            ["git", "-C", repo_top, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0:
            with _WORKTREE_LIST_LOCK:
                _WORKTREE_LIST_CACHE[cache_key] = (now, [])
            return []
    except (subprocess.SubprocessError, OSError):
        with _WORKTREE_LIST_LOCK:
            _WORKTREE_LIST_CACHE[cache_key] = (now, [])
        return []
    out = []
    cur = {}

    def flush():
        if cur.get("path"):
            out.append({
                "path": cur.get("path"),
                "branch": cur.get("branch"),
                "detached": cur.get("detached", False),
                "locked": cur.get("locked", False),
                "lock_reason": cur.get("lock_reason") or "",
            })

    for line in r.stdout.splitlines():
        if not line.strip():
            flush()
            cur = {}
            continue
        parts = line.split(maxsplit=1)
        key = parts[0]
        val = parts[1] if len(parts) > 1 else ""
        if key == "worktree":
            cur["path"] = val
        elif key == "branch":
            cur["branch"] = val.replace("refs/heads/", "", 1)
        elif key == "detached":
            cur["detached"] = True
        elif key == "locked":
            cur["locked"] = True
            cur["lock_reason"] = val
    flush()
    with _WORKTREE_LIST_LOCK:
        _WORKTREE_LIST_CACHE[cache_key] = (now, out)
    return out


def _session_tail_worktree_hint(session_id):
    """Return a worktree path/branch explicitly recorded in session tail meta."""
    if not session_id:
        return None
    tail = None
    source = None
    try:
        if _core._is_codex_session(session_id):
            path = _core._resolve_codex_rollout_path(session_id)
            tail = _core._extract_codex_tail_meta(path) if path else None
            source = "worktree-add"
        elif _core._is_gemini_session(session_id):
            path = _core._resolve_gemini_chat_path(session_id)
            tail = _core._extract_gemini_tail_meta(path) if path else None
            source = "worktree-add"
        elif _core._is_cursor_session(session_id):
            path = _core._cursor_transcript_path(session_id)
            tail = _core._extract_cursor_tail_meta(path) if path else None
            source = "worktree-add"
        elif _core._is_antigravity_session(session_id):
            path = _core._antigravity_transcript_path(session_id)
            tail = _core._extract_antigravity_tail_meta(path) if path else None
            source = "worktree-add"
    except Exception:
        tail = None
    if not tail:
        return None
    worktree_path = tail.get("tail_worktree_path") or ""
    if not worktree_path:
        return None
    return {
        "path": worktree_path,
        "branch": tail.get("tail_branch") or "",
        "source": source or "session-tail",
    }


def _workspace_git_snapshot(path, branch_hint=None):
    if not path:
        return None
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not p.is_dir():
        return None

    git_path = p / ".git"
    if git_path.is_file():
        kind = "worktree"
    elif git_path.is_dir():
        kind = "clone"
    else:
        kind = "other"

    def git(*args, timeout=2):
        try:
            r = subprocess.run(
                ["git", "-C", str(p), *args],
                capture_output=True, text=True, timeout=timeout,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
        return None

    branch = branch_hint or git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        branch = None
    upstream = git("rev-parse", "--abbrev-ref", "@{u}")
    base = upstream or "main"
    ahead = behind = None
    counts = git("rev-list", "--left-right", "--count", f"{base}...HEAD")
    if counts:
        try:
            behind_s, ahead_s = counts.split()
            behind = int(behind_s)
            ahead = int(ahead_s)
        except (ValueError, IndexError):
            pass

    return {
        "path": str(p),
        "branch": branch,
        "kind": kind,
        "ahead": ahead,
        "behind": behind,
    }


def extract_session_workspace(session_id):
    """Resolve which workspace (shared clone vs. git worktree) a session
    is editing in, plus branch + ahead/behind. Powers the conv pane's
    "Workspace" panel so users can tell at a glance whether a session is
    working on main or in a feature worktree.
    """
    out = {
        "cwd": None, "exists": False, "is_repo": False,
        "is_worktree": False, "branch": None,
        "main_repo_path": None,
        "commits_ahead": None, "commits_behind": None,
        "co_tenants": 0,
        # Tool-call-inferred effective workspace — set below when the
        # session's actual edits land somewhere other than its launch cwd
        # (e.g. cwd is an empty stub directory but the session is editing
        # a real repo elsewhere). Display-only; never used to dispatch
        # writes, since inference can be wrong.
        "effective_cwd": None,
        "effective_branch": None,
        "effective_kind": None,
        "effective_commits_ahead": None,
        "effective_commits_behind": None,
        "effective_path_count": 0,
        "effective_total_paths": 0,
        "effective_source": None,
        # Sibling worktrees of the session's repo (excluding the session's
        # own worktree). Each entry: {path, branch, detached, locked,
        # lock_reason, is_agent}. is_agent is true when the lock_reason
        # starts with "claude agent" — superpowers / orchestration skills
        # auto-spawn locked agent worktrees that the user may not realise
        # exist.
        "worktrees": [],
        "worktrees_agent_count": 0,
        "worktrees_manual_count": 0,
    }
    cwd = _core.find_session_cwd(session_id)
    if not cwd:
        return out
    out["cwd"] = cwd
    p = Path(cwd)
    if not p.is_dir():
        return out
    out["exists"] = True

    # A worktree's `.git` is a file containing `gitdir: <path>`.
    # The shared clone's `.git` is a directory.
    git_path = p / ".git"
    if git_path.is_file():
        out["is_repo"] = True
        out["is_worktree"] = True
        try:
            line = git_path.read_text().strip()
            if line.startswith("gitdir:"):
                gitdir = Path(line[len("gitdir:"):].strip())
                # gitdir typically points at <main>/.git/worktrees/<name>,
                # so the main repo dir is two parents up.
                if gitdir.is_absolute():
                    candidate_dot_git = gitdir.parent.parent
                    if candidate_dot_git.name == ".git":
                        out["main_repo_path"] = str(candidate_dot_git.parent)
        except OSError:
            pass
    elif git_path.is_dir():
        out["is_repo"] = True

    # Don't early-exit on non-repo cwd: we still want to run tool-call
    # inference for sessions whose launch cwd is an empty stub directory
    # but whose actual edits land in a real repo elsewhere (the BYM+Finie
    # case). The git()-on-cwd block below is harmless to skip in that case.

    def git(*args, timeout=2):
        try:
            r = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=timeout,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
        return None

    if out["is_repo"]:
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        if branch and branch != "HEAD":
            out["branch"] = branch

        # Compare against the configured upstream if any, else `main`.
        upstream = git("rev-parse", "--abbrev-ref", "@{u}")
        base = upstream or "main"
        counts = git("rev-list", "--left-right", "--count", f"{base}...HEAD")
        if counts:
            try:
                behind, ahead = counts.split()
                out["commits_behind"] = int(behind)
                out["commits_ahead"] = int(ahead)
            except (ValueError, IndexError):
                pass

    # Co-tenants: how many OTHER live sessions are in this same cwd?
    try:
        registry = _core._load_session_registry()
        for sid_other, info in registry.items():
            if sid_other == session_id:
                continue
            if (info or {}).get("cwd") == cwd:
                out["co_tenants"] += 1
    except Exception:
        pass

    # Tool-call inference. Resolve the literal cwd's git toplevel once so
    # we only surface "effective" when it actually disagrees with cwd.
    cwd_top = None
    if out["is_repo"]:
        cwd_top = _git_toplevel_for_path(cwd, {})
    tail_hint = _core._session_tail_worktree_hint(session_id)
    if tail_hint:
        snap = _workspace_git_snapshot(tail_hint.get("path"), tail_hint.get("branch"))
        try:
            same_as_cwd = bool(snap and Path(snap["path"]).resolve() == Path(cwd).resolve())
        except (OSError, RuntimeError, ValueError):
            same_as_cwd = False
        if snap and not same_as_cwd:
            out["effective_cwd"] = snap["path"]
            out["effective_branch"] = snap["branch"]
            out["effective_kind"] = snap["kind"]
            out["effective_commits_ahead"] = snap["ahead"]
            out["effective_commits_behind"] = snap["behind"]
            out["effective_path_count"] = 1
            out["effective_total_paths"] = 1
            out["effective_source"] = tail_hint.get("source") or "session-tail"

    eff = None
    if not out["effective_cwd"]:
        try:
            eff = _core._infer_effective_repo(session_id, literal_cwd=cwd, exclude_top=cwd_top)
        except Exception:
            eff = None
    if eff:
        out["effective_cwd"] = eff["top"]
        out["effective_branch"] = eff["branch"]
        out["effective_kind"] = eff["kind"]
        out["effective_commits_ahead"] = eff["ahead"]
        out["effective_commits_behind"] = eff["behind"]
        out["effective_path_count"] = eff["count"]
        out["effective_total_paths"] = eff["total"]
        out["effective_source"] = "tool-calls"

    # Sibling worktrees of whatever repo the session is actually editing.
    # Pick a single canonical "anchor" repo so `git worktree list` emits
    # the same set regardless of which worktree we query from:
    #   - if cwd is a worktree → its main_repo_path
    #   - else if cwd is a repo (shared clone) → cwd itself
    #   - else if inference picked an effective repo → that
    anchor = None
    if out["is_worktree"] and out["main_repo_path"]:
        anchor = out["main_repo_path"]
    elif out["is_repo"]:
        anchor = cwd
    elif out["effective_cwd"]:
        anchor = out["effective_cwd"]
    if anchor:
        try:
            wts = _list_worktrees(anchor)
        except Exception:
            wts = []
        # Exclude the session's own worktree from the list — the user
        # already sees that one as the "main" pill.
        self_path = cwd if (cwd and out["is_repo"]) else out.get("effective_cwd")
        siblings = []
        agent_n = manual_n = 0
        for wt in wts:
            if self_path and wt.get("path") == self_path:
                continue
            reason = (wt.get("lock_reason") or "").strip()
            is_agent = reason.lower().startswith("claude agent")
            wt["is_agent"] = is_agent
            if is_agent:
                agent_n += 1
            else:
                manual_n += 1
            siblings.append(wt)
        out["worktrees"] = siblings
        out["worktrees_agent_count"] = agent_n
        out["worktrees_manual_count"] = manual_n

    return out


def extract_session_timeline(session_id):
    """Walk a session's JSONL transcript and return chronological commit /
    push / PR events with their assistant-turn position. Used by the conv
    pane to render a session-activity strip under the "Original ask" header.

    Returns: {events: [{kind, turn, ts, subject?, sha?, pr_number?, success}],
              total_turns}
    """
    if _core._is_codex_session(session_id):
        return _core._extract_codex_timeline(session_id)
    if _core._is_gemini_session(session_id):
        return _core._extract_gemini_timeline(session_id)
    if _core._is_cursor_session(session_id):
        return _core._extract_cursor_timeline(session_id)
    if _core._is_antigravity_session(session_id):
        return _core._extract_antigravity_timeline(session_id)
    if _core._is_hermes_session(session_id):
        return _core._extract_hermes_timeline(session_id)
    if _core._is_devin_cli_session(session_id):
        return _core._extract_devin_cli_timeline(session_id)
    if not _core.PROJECTS_ROOT.is_dir():
        return {"events": [], "total_turns": 0}
    jsonl = None
    for pd in _core.PROJECTS_ROOT.iterdir():
        if not pd.is_dir():
            continue
        cand = pd / f"{session_id}.jsonl"
        if cand.is_file():
            jsonl = cand
            break
    if not jsonl:
        return {"events": [], "total_turns": 0}

    events = []
    pending_by_id = {}  # tool_use_id -> index into events (so result can update success/sha/pr#)
    turn = 0
    try:
        with open(jsonl, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev_type = ev.get("type", "")
                ts = ev.get("timestamp", "")
                if ev_type == "assistant":
                    # Sidechain (subagent) turns don't count toward the user-
                    # facing turn count; they're internal to a Task tool call.
                    if ev.get("isSidechain"):
                        continue
                    turn += 1
                    msg = _core._safe_parse_message(ev.get("message", {}))
                    for block in msg.get("content", []):
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_use":
                            continue
                        if block.get("name") != "Bash":
                            continue
                        cmd = (block.get("input") or {}).get("command", "")
                        if not isinstance(cmd, str) or not cmd:
                            continue
                        kind = None
                        subject = ""
                        if _TIMELINE_PR_CREATE_RE.search(cmd):
                            kind = "pr"
                            m = _TIMELINE_PR_TITLE_RE.search(cmd)
                            if m:
                                subject = m.group(1)
                        elif _TIMELINE_PUSH_RE.search(cmd):
                            kind = "push"
                        elif _TIMELINE_COMMIT_RE.search(cmd):
                            kind = "commit"
                            m = _TIMELINE_COMMIT_MSG_RE.search(cmd)
                            if m:
                                subject = m.group(1)
                        if not kind:
                            continue
                        entry = {
                            "kind": kind,
                            "turn": turn,
                            "ts": ts,
                            "subject": subject,
                            "success": None,  # filled by tool_result
                        }
                        events.append(entry)
                        tu_id = block.get("id") or ""
                        if tu_id:
                            pending_by_id[tu_id] = len(events) - 1
                elif ev_type == "user":
                    # Tool results land as a user-role event with a content list.
                    msg = _core._safe_parse_message(ev.get("message", {}))
                    content = msg.get("content")
                    if not isinstance(content, list):
                        continue
                    for sub in content:
                        if not isinstance(sub, dict) or sub.get("type") != "tool_result":
                            continue
                        tu_id = sub.get("tool_use_id", "")
                        if tu_id not in pending_by_id:
                            continue
                        idx = pending_by_id.pop(tu_id)
                        e = events[idx]
                        e["success"] = not bool(sub.get("is_error"))
                        # Try to extract richer detail from the result text:
                        # commit SHA, PR number.
                        result_text = ""
                        rc = sub.get("content")
                        if isinstance(rc, str):
                            result_text = rc
                        elif isinstance(rc, list):
                            parts = [t.get("text", "") for t in rc if isinstance(t, dict) and t.get("type") == "text"]
                            result_text = "\n".join(parts)
                        if e["kind"] == "commit":
                            m = _TIMELINE_COMMIT_RESULT_RE.search(result_text)
                            if m:
                                e["sha"] = m.group(1)
                                # Replace shell-mangled subjects (heredoc syntax
                                # like `$(cat <<` etc.) with the real subject
                                # line git itself emitted on commit.
                                real_subject = m.group(2).strip()
                                if real_subject and (not e.get("subject") or e["subject"].startswith("$(") or e["subject"].startswith("cat ")):
                                    e["subject"] = real_subject[:200]
                        elif e["kind"] == "pr":
                            m = _TIMELINE_PR_NUMBER_FROM_URL_RE.search(result_text)
                            if m:
                                e["pr_number"] = int(m.group(1))
    except OSError:
        return {"events": [], "total_turns": 0}

    return {"events": events, "total_turns": turn}


# Anthropic API list-price rates ($ per million tokens) by model family.
# Subscription users (Claude Pro / Max / API console credits) don't pay these
# rates per turn, but the breakdown is still the cleanest signal of "how
# expensive is this session" — same units for everyone, comparable across
# models. UI surfaces this as "API list-price equivalent".
#
# Sources: Anthropic/OpenAI list pricing and AgentsView fallback pricing, last
# checked 2026-07. If rates change, edit here. Rates are
# (input_per_mtok, cache_write, cache_read, output_per_mtok).
_MODEL_RATES = {
    "claude-fable-5": (10.00, 12.50, 1.00, 50.00),
    "claude-sonnet-5": (2.00, 2.50, 0.20, 10.00),
    "claude-opus-5": (5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-8": (5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-7": (5.00, 6.25, 0.50, 25.00),
    "claude-opus-4-6": (5.00, 6.25, 0.50, 25.00),
    "claude-sonnet-4-6": (3.00, 3.75, 0.30, 15.00),
    "claude-sonnet-4-5-20250514": (3.00, 3.75, 0.30, 15.00),
    "claude-sonnet-4-20250514": (3.00, 3.75, 0.30, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 1.25, 0.10, 5.00),
    # https://developers.openai.com/api/docs/models/gpt-6-astra (2026-09).
    "gpt-6-astra": (10.00, 0.00, 1.00, 50.00),
    "gpt-5.5": (5.00, 0.00, 0.50, 30.00),
    # GPT-5.6 cache writes are 1.25x input and cache reads are 10% of input.
    # https://openai.com/index/gpt-5-6/ (checked 2026-08)
    "gpt-5.6-sol": (5.00, 6.25, 0.50, 30.00),
    "gpt-5.6-terra": (2.50, 3.125, 0.25, 15.00),
    "gpt-5.6-luna": (1.00, 1.25, 0.10, 6.00),
    "gpt-5.4": (2.50, 0.00, 0.00, 15.00),
    "gpt-5.3-codex": (1.75, 0.00, 0.00, 14.00),
    "gpt-5.2-codex": (1.75, 0.00, 0.00, 14.00),
    "gpt-5.1-codex-max": (1.25, 0.00, 0.00, 10.00),
    "gpt-5.4-mini": (0.75, 0.00, 0.00, 4.50),
    "gpt-5.4-nano": (0.20, 0.00, 0.00, 1.25),
    # Kimi lists cache misses, cache hits, and output separately. Kimi's
    # inputCacheCreation bucket is priced as a cache miss.
    # https://www.kimi.com/resources/kimi-k3-pricing
    # https://www.kimi.com/resources/kimi-k2-7-code-pricing (checked 2026-08)
    "k3": (3.00, 3.00, 0.30, 15.00),
    "k3-256k": (3.00, 3.00, 0.30, 15.00),
    "kimi-k3": (3.00, 3.00, 0.30, 15.00),
    "kimi-for-coding": (0.95, 0.95, 0.19, 4.00),
    "kimi-k2.7-code": (0.95, 0.95, 0.19, 4.00),
    "kimi-for-coding-highspeed": (1.90, 1.90, 0.38, 8.00),
    "kimi-k2.7-code-highspeed": (1.90, 1.90, 0.38, 8.00),
    # Older families kept for archival sessions.
    "claude-opus-4-20250514": (15.00, 18.75, 1.50, 75.00),
    "claude-opus-3": (15.00, 18.75, 1.50, 75.00),
    "claude-sonnet-3": (3.00, 3.75, 0.30, 15.00),
    "claude-haiku-3-5-20241022": (0.80, 1.00, 0.08, 4.00),
    "claude-haiku-3": (0.25, 0.30, 0.03, 1.25),
}
_FALLBACK_RATES = (3.00, 3.75, 0.30, 15.00)  # Sonnet — sane middle ground.


def _canonical_model_name(model):
    m = (model or "").lower()
    if "/" in m:
        m = m.rsplit("/", 1)[-1]
    m = m.strip().replace(".", "-")
    m = re.sub(r"\s*[\[(].*?[\])]\s*$", "", m).strip()
    m = re.sub(r"-\d{8}$", "", m)
    return m


def _rates_for_model_known(model):
    m = _canonical_model_name(model)
    if m in _MODEL_RATES:
        return list(_MODEL_RATES[m]), True
    for key, rates in _MODEL_RATES.items():
        canonical_key = _canonical_model_name(key)
        if canonical_key == m:
            return list(rates), True
        if (
            canonical_key.startswith("claude-")
            and m.startswith(canonical_key + "-")
        ):
            return list(rates), True
    return list(_FALLBACK_RATES), False


_SESSION_COST_FALLBACK_MODELS = {
    "claude": "claude-sonnet-4-6",
    "codex": "gpt-6-astra",
    "kimi": "kimi-code/k3",
}


def _session_usage_cost(engine, model, totals):
    """Price normalized lifetime token buckets without any additional I/O."""
    engine = str(engine or "").strip().lower()
    model = str(model or "").strip()
    configured = ""
    try:
        configured = _core._spawn_fallback_model_for_engine(engine)
    except (AttributeError, TypeError, ValueError):
        pass
    resolved_model = ""
    rates = (0.0, 0.0, 0.0, 0.0)
    direct = False
    candidates = (
        model,
        configured,
        _SESSION_COST_FALLBACK_MODELS.get(engine, ""),
    )
    for candidate in candidates:
        if not candidate:
            continue
        candidate_rates, known = _rates_for_model_known(candidate)
        if known:
            resolved_model = candidate
            rates = candidate_rates
            direct = candidate == model and bool(model)
            break
    rate_in, rate_cw, rate_cr, rate_out = rates

    def tokens(key):
        return max(_core._codex_int((totals or {}).get(key)), 0)

    breakdown = {
        "input": tokens("total_input_tokens") * rate_in / 1_000_000,
        "cache_creation": (
            tokens("total_cache_creation_tokens") * rate_cw / 1_000_000
        ),
        "cache_read": tokens("total_cache_read_tokens") * rate_cr / 1_000_000,
        "output": tokens("total_output_tokens") * rate_out / 1_000_000,
    }
    rounded = {key: round(value, 6) for key, value in breakdown.items()}
    return {
        "cost_usd": round(sum(breakdown.values()), 6),
        "cost_breakdown_usd": rounded,
        "cost_basis": "api_list_price" if direct else "engine_fallback",
        "cost_model": resolved_model,
    }


def _rates_for_model(model):
    rates, _known = _rates_for_model_known(model)
    return rates


def _diagnostic_context_tokens(diagnostics):
    """Best-effort context-size hint from newer Claude Code diagnostics.

    Some recent Claude transcripts omit the normal `message.usage` object
    entirely but still record how many input tokens missed the prompt cache.
    That value is not billable-usage data, but it is a useful lower-bound
    context sample for the footer instead of rendering a blank/unknown row.
    """
    if not isinstance(diagnostics, dict):
        return 0

    candidates = []
    miss = diagnostics.get("cache_miss_reason")
    if isinstance(miss, dict):
        candidates.append(miss.get("cache_missed_input_tokens"))

    for key in (
        "context_tokens",
        "input_tokens",
        "prompt_tokens",
        "cache_missed_input_tokens",
    ):
        candidates.append(diagnostics.get(key))

    best = 0
    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            best = max(best, value)
        elif isinstance(value, str) and value.isdigit():
            best = max(best, int(value))
    return best


_CONTEXT_AMOUNT_RE = r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM]?)"
_CONTEXT_TOKENS_RE = re.compile(
    r"\*{0,2}Tokens:\*{0,2}\s*"
    + _CONTEXT_AMOUNT_RE
    + r"\s*/\s*"
    + _CONTEXT_AMOUNT_RE
    + r"(?:\s*\(([0-9]+)%\))?",
    re.IGNORECASE,
)
_CONTEXT_MODEL_RE = re.compile(r"\*{0,2}Model:\*{0,2}\s*([^\n<]+)", re.IGNORECASE)


def _context_amount_to_tokens(number, suffix):
    try:
        value = float(str(number or "0").replace(",", ""))
    except (TypeError, ValueError):
        return 0
    scale = {"k": 1_000, "m": 1_000_000}.get(str(suffix or "").lower(), 1)
    return int(round(value * scale))


def _local_command_context_usage(ev):
    """Parse Claude Code `/context` output captured as a local_command event."""
    if not isinstance(ev, dict):
        return None
    content = ev.get("content")
    if not isinstance(content, str) or "Context Usage" not in content:
        return None
    match = _CONTEXT_TOKENS_RE.search(content)
    if not match:
        return None
    tokens = _context_amount_to_tokens(match.group(1), match.group(2))
    limit = _context_amount_to_tokens(match.group(3), match.group(4))
    if tokens <= 0 or limit <= 0:
        return None
    percent = _core._codex_int(match.group(5))
    if not percent:
        percent = int(round(tokens * 100 / limit))
    model_match = _CONTEXT_MODEL_RE.search(content)
    model = model_match.group(1).strip() if model_match else ""
    return {
        "tokens": tokens,
        "limit": limit,
        "percent": percent,
        "model": model,
        "timestamp": ev.get("timestamp") or "",
    }


def _token_optimizer_quality_grade(score):
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


# _TOKEN_OPTIMIZER_QUALITY_INDEX / _RUNTIME_STATE live in server.py (tests
# patch them through the server module); read and rebound via _core.
_TOKEN_OPTIMIZER_QUALITY_INDEX_LOCK = threading.Lock()
_TOKEN_OPTIMIZER_QUALITY_INDEX_REFRESH_S = 60.0
_TOKEN_OPTIMIZER_QUALITY_INDEX_REFRESH_STARTED = False


def _token_optimizer_quality_index_paths():
    """The two producer-owned files; callers must not enumerate their dirs."""
    home = Path.home()
    return (
        ("claude", home / ".claude" / "token-optimizer" / "quality-index.json"),
        ("codex", home / ".codex" / "token-optimizer" / "quality-index.json"),
    )


def _token_optimizer_quality_safe_sid(value):
    sid = str(value or "").strip()
    if not sid or re.fullmatch(r"[A-Za-z0-9_-]+", sid) is None:
        return ""
    return sid


def _token_optimizer_quality_index_records(raw):
    """Validate a complete producer index and return its usable records."""
    if not isinstance(raw, dict) or raw.get("version") != 1:
        return None
    source = raw.get("records")
    if not isinstance(source, dict):
        return None
    records = {}
    for session_id, data in source.items():
        sid = _token_optimizer_quality_safe_sid(session_id)
        if not sid or not isinstance(data, dict):
            continue
        try:
            score = float(data.get("score"))
            source_mtime = float(data.get("source_mtime"))
            transcript_mtime = float(data.get("transcript_mtime"))
        except (TypeError, ValueError):
            continue
        if (
            not math.isfinite(score)
            or not math.isfinite(source_mtime)
            or not math.isfinite(transcript_mtime)
            or not 0 <= score <= 100
            or source_mtime < 0
            or transcript_mtime < 0
        ):
            continue
        rounded = round(score, 1)
        if rounded.is_integer():
            rounded = int(rounded)
        records[sid] = {
            "source_mtime": source_mtime,
            "value": {
                "quality_score": rounded,
                "quality_grade": str(
                    data.get("grade") or _token_optimizer_quality_grade(score)
                ).strip(),
                "quality_timestamp": str(data.get("timestamp") or ""),
                "quality_summary": str(data.get("summary") or ""),
                "quality_source": "token-optimizer-index",
            },
        }
    return records


def _refresh_token_optimizer_quality_index():
    """Refresh changed producer indexes; this is the only TO filesystem reader."""
    previous = _core._TOKEN_OPTIMIZER_QUALITY_RUNTIME_STATE
    next_state = dict(previous)
    changed = False
    for runtime, index_path in _token_optimizer_quality_index_paths():
        prior = previous.get(runtime) or {"stamp": None, "records": {}}
        try:
            stat = index_path.stat()
        except FileNotFoundError:
            if prior.get("stamp") is not None or prior.get("records"):
                next_state[runtime] = {"stamp": None, "records": {}}
                changed = True
            continue
        except OSError:
            # A temporarily unreadable index is not a reason to drop pills.
            continue
        stamp = (stat.st_mtime_ns, stat.st_size)
        if prior.get("stamp") == stamp:
            continue
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Preserve the last good runtime map until the next interval.
            continue
        records = _token_optimizer_quality_index_records(raw)
        if records is None:
            continue
        next_state[runtime] = {"stamp": stamp, "records": records}
        changed = True

    if not changed:
        return False
    merged = {}
    # Equal source mtimes use runtime then path order so duplicate pills do not
    # flicker. `codex` sorts after `claude`, therefore wins an exact tie.
    for runtime, index_path in _token_optimizer_quality_index_paths():
        records = (next_state.get(runtime) or {}).get("records") or {}
        for sid, record in records.items():
            candidate_key = (record["source_mtime"], runtime, str(index_path))
            previous_record = merged.get(sid)
            if previous_record is None or candidate_key > previous_record[0]:
                merged[sid] = (candidate_key, record["value"])
    complete_map = {sid: value for sid, (_key, value) in merged.items()}
    with _TOKEN_OPTIMIZER_QUALITY_INDEX_LOCK:
        _core._TOKEN_OPTIMIZER_QUALITY_RUNTIME_STATE = next_state
        _core._TOKEN_OPTIMIZER_QUALITY_INDEX = complete_map
    return True


# New identity per module (re)load. A refresher thread born under an older
# load must not keep writing the live index through _core after the test
# suite re-imports server (which reloads this module): it captures this token
# at birth and exits when a reload replaces it.
_TOKEN_OPTIMIZER_REFRESH_GENERATION = object()


def _token_optimizer_quality_index_loop():
    birth = _TOKEN_OPTIMIZER_REFRESH_GENERATION
    while True:
        if _TOKEN_OPTIMIZER_REFRESH_GENERATION is not birth:
            return
        try:
            _core._refresh_token_optimizer_quality_index()
        except Exception:
            # Advisory metadata must not destabilize CCC's background work.
            pass
        time.sleep(_TOKEN_OPTIMIZER_QUALITY_INDEX_REFRESH_S)


def _start_token_optimizer_quality_index_refresher():
    global _TOKEN_OPTIMIZER_QUALITY_INDEX_REFRESH_STARTED
    with _TOKEN_OPTIMIZER_QUALITY_INDEX_LOCK:
        if _TOKEN_OPTIMIZER_QUALITY_INDEX_REFRESH_STARTED:
            return
        _TOKEN_OPTIMIZER_QUALITY_INDEX_REFRESH_STARTED = True
    threading.Thread(
        target=_token_optimizer_quality_index_loop,
        daemon=True,
        name="ccc-token-optimizer-quality-index",
    ).start()


def _token_optimizer_quality_for_session(session_id):
    """Pure request-time lookup of the most recently published quality map."""
    sid = _token_optimizer_quality_safe_sid(session_id)
    return dict(_core._TOKEN_OPTIMIZER_QUALITY_INDEX.get(sid) or {})


def _with_token_optimizer_quality(payload, session_id):
    if not isinstance(payload, dict):
        return payload
    quality = _core._token_optimizer_quality_for_session(session_id)
    if quality:
        return {**payload, **quality}
    return payload


def _extract_kimi_usage(session_id):
    """Usage stats for a kimi session, read from its wire.jsonl.

    The wire records `context.update_token_count {tokenCount}` (the live
    context size after each context mutation — the number the TUI shows) and
    `usage.record {usage: {inputOther, output, inputCacheRead,
    inputCacheCreation}}` per LLM call. No usage data streams over ACP (see
    docs/kimi-code-reference.md §1), so the file is the only source.
    """
    try:
        limit = int(os.environ.get("CCC_KIMI_CONTEXT_LIMIT", "256000") or "256000")
    except ValueError:
        limit = 256000
    override = _core._get_session_override(session_id)
    result = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "context_limit": limit,
        "compact_count": 0,
        "live_context_tokens": 0,
        "live_context_limit": 0,
        "live_context_percent": 0,
        "live_context_timestamp": "",
        "live_context_source": "",
        "engine": "kimi",
        "override": override,
        # Kimi's thinking effort never streams back over ACP, so the value CCC
        # spawned or last picked is the only source. Top-level to match the
        # codex usage shape.
        "reasoning_effort": (override or {}).get("reasoning_effort") or "",
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
    }
    # Per-turn tail for the status-rail column graph — same shape as Claude's
    # turn_series (raw counts; the client applies the cache-read discount).
    # Kimi's `usage.record` events are per-LLM-call, so one entry per bar.
    turn_series = collections.deque(maxlen=USAGE_TURN_SERIES_MAX)
    try:
        session_dir = (_core._kimi_session_index().get(session_id) or {}).get("session_dir") or ""
        wire = Path(session_dir) / "agents" / "main" / "wire.jsonl" if session_dir else None
        if wire is None or not wire.is_file():
            return result
        last_step_usage = None
        with wire.open() as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype == "config.update":
                    alias = str(ev.get("modelAlias") or "").strip()
                    if alias:
                        result["model"] = alias
                elif etype == "context.update_token_count":
                    count = int(ev.get("tokenCount") or 0)
                    result["latest_input_tokens"] = count
                    result["peak_input_tokens"] = max(result["peak_input_tokens"], count)
                elif etype == "usage.record":
                    usage = ev.get("usage") or {}
                    fresh = int(usage.get("inputOther") or 0)
                    cached = int(usage.get("inputCacheRead") or 0)
                    created = int(usage.get("inputCacheCreation") or 0)
                    output = int(usage.get("output") or 0)
                    result["total_input_tokens"] += fresh
                    result["total_cache_read_tokens"] += cached
                    result["total_cache_creation_tokens"] += created
                    result["total_output_tokens"] += output
                    if not result["model"]:
                        result["model"] = str(ev.get("model") or "")
                    window = fresh + cached + created
                    if window or output:
                        ts = ""
                        ms = ev.get("time")
                        if isinstance(ms, (int, float)) and ms > 0:
                            ts = datetime.fromtimestamp(
                                ms / 1000, tz=timezone.utc
                            ).isoformat().replace("+00:00", "Z")
                        turn_series.append({
                            "ts": ts,
                            "tokens_in": window,
                            "tokens_cached": cached,
                            "tokens_out": output,
                        })
                elif etype == "context.append_loop_event":
                    loop = ev.get("event") or {}
                    if loop.get("type") == "step.end" and isinstance(loop.get("usage"), dict):
                        last_step_usage = loop["usage"]
                elif etype == "full_compaction.complete":
                    result["compact_count"] += 1
        if not result["latest_input_tokens"] and last_step_usage:
            # No token-count record yet: the last step's window total is the
            # closest estimate of the live context size.
            u = last_step_usage
            result["latest_input_tokens"] = (
                int(u.get("inputOther") or 0)
                + int(u.get("inputCacheRead") or 0)
                + int(u.get("inputCacheCreation") or 0)
            )
            result["peak_input_tokens"] = max(
                result["peak_input_tokens"], result["latest_input_tokens"])
    except OSError:
        pass
    result["turn_series"] = list(turn_series)
    result.update(_session_usage_cost("kimi", result.get("model"), result))
    return result


def extract_session_usage(session_id):
    """Walk a session's JSONL transcript and return token-usage stats.

    Each assistant turn carries a `usage` object: input_tokens +
    cache_creation_input_tokens + cache_read_input_tokens is the size of
    the prompt window at that turn (cache reads count against the window
    even though they're billed cheaper). The peak across all assistant
    turns is the closest the session got to the model's context limit.

    Returns: {latest_input_tokens, peak_input_tokens, total_output_tokens,
              total_input_tokens, total_cache_creation_tokens,
              total_cache_read_tokens, model, context_limit, cost_usd,
              reasoning_effort, cost_breakdown_usd}.
    """
    override = _core._get_session_override(session_id)
    empty = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "context_limit": 0,
        "compact_count": 0,
        "live_context_tokens": 0,
        "live_context_limit": 0,
        "live_context_percent": 0,
        "live_context_timestamp": "",
        "live_context_source": "",
        "engine": "claude",
        "override": override,
        "reasoning_effort": (override or {}).get("reasoning_effort") or "",
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
    }
    if _core._is_codex_session(session_id):
        result = _core._extract_codex_usage(session_id)
        result.setdefault("engine", "codex")
        return _with_token_optimizer_quality(result, session_id)
    if _core._is_gemini_session(session_id):
        result = _core._extract_gemini_usage(session_id)
        result.setdefault("engine", "gemini")
        return _with_token_optimizer_quality(result, session_id)
    if _core._is_cursor_session(session_id):
        result = _core._extract_cursor_usage(session_id)
        result.setdefault("engine", "cursor")
        return _with_token_optimizer_quality(result, session_id)
    if _core._is_antigravity_session(session_id):
        result = _core._extract_antigravity_usage(session_id)
        result.setdefault("engine", "antigravity")
        return _with_token_optimizer_quality(result, session_id)
    if _core._is_hermes_session(session_id):
        return _with_token_optimizer_quality(_core._extract_hermes_usage(session_id), session_id)
    if _core._is_kimi_session(session_id):
        return _with_token_optimizer_quality(_extract_kimi_usage(session_id), session_id)
    if _core._is_grok_session(session_id):
        return _with_token_optimizer_quality(_core._extract_grok_usage(session_id), session_id)
    if _core._is_devin_cli_session(session_id):
        result = _core._extract_devin_cli_usage(session_id)
        result.setdefault("engine", "devin")
        return _with_token_optimizer_quality(result, session_id)
    desktop_meta = _core._load_desktop_app_metadata().get(session_id) or {}
    if not _core.PROJECTS_ROOT.is_dir():
        return _with_token_optimizer_quality({**empty, "model": desktop_meta.get("model") or ""}, session_id)
    jsonl = None
    for pd in _core.PROJECTS_ROOT.iterdir():
        if not pd.is_dir():
            continue
        cand = pd / f"{session_id}.jsonl"
        if cand.is_file():
            jsonl = cand
            break
    if not jsonl:
        return _with_token_optimizer_quality({**empty, "model": desktop_meta.get("model") or ""}, session_id)

    latest = 0
    peak = 0
    total_in = 0
    total_cw = 0
    total_cr = 0
    total_out = 0
    model = desktop_meta.get("model") or ""
    live_effort = ""
    diagnostic_latest = 0
    diagnostic_peak = 0
    live_context = None
    max_observed_window = 0
    # Claude Code re-records the same Anthropic API response (same
    # `message.id`) under a fresh event uuid every time a session is
    # resumed or forked from a parent turn. Tracking which message.ids
    # have already contributed to the totals keeps cost from inflating
    # by the resume count — see issue #60.
    seen_message_ids = set()
    # `/compact` (manual or auto) emits a `{type: system, subtype: compact_boundary}`
    # event. Pre-compact assistant turns no longer contribute to the live
    # context window, so reset both latest and peak whenever we cross a
    # boundary — the displayed numbers reflect only the post-most-recent-
    # compact segment, which matches what the user sees in the TUI. Recent
    # Claude Code builds also include `postTokens`; use that as the live value
    # until the next assistant turn writes a normal `usage` block.
    compact_count = 0
    # Per-turn tail for the status-rail column graph: one entry per billed
    # assistant message (same message.id dedupe as the totals above), newest
    # last. Raw counts only — the client applies the same cache-read discount
    # it uses for the per-turn chip, so a bar equals the chip's number.
    turn_series = collections.deque(maxlen=USAGE_TURN_SERIES_MAX)
    try:
        with open(jsonl, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "system" and ev.get("subtype") == "compact_boundary":
                    meta = ev.get("compactMetadata")
                    if not isinstance(meta, dict):
                        meta = {}
                    pre_tokens = _core._codex_int(meta.get("preTokens"))
                    post_tokens = _core._codex_int(meta.get("postTokens"))
                    max_observed_window = max(max_observed_window, pre_tokens, post_tokens)
                    latest = post_tokens
                    peak = post_tokens
                    diagnostic_latest = post_tokens
                    diagnostic_peak = post_tokens
                    live_context = None
                    compact_count += 1
                    continue
                if ev.get("type") == "system" and ev.get("subtype") == "local_command":
                    parsed_context = _local_command_context_usage(ev)
                    if parsed_context:
                        live_context = parsed_context
                        max_observed_window = max(
                            max_observed_window,
                            parsed_context["tokens"],
                            parsed_context["limit"],
                        )
                        if parsed_context.get("model"):
                            model = parsed_context["model"]
                    continue
                if ev.get("type") != "assistant":
                    continue
                if ev.get("isSidechain"):
                    continue
                msg = _core._safe_parse_message(ev.get("message", {}))
                if msg.get("model"):
                    model = msg.get("model")
                # Claude Code stamps the turn's effort on the assistant record,
                # a sibling of `message` rather than a field inside it. That is
                # the ground truth: it reflects a `/effort` typed into the TUI
                # just as much as CCC's own `--effort`, where the override file
                # only knows what CCC set.
                if ev.get("effort"):
                    live_effort = str(ev.get("effort")).strip()
                diag_window = _diagnostic_context_tokens(
                    msg.get("diagnostics") or ev.get("diagnostics")
                )
                if diag_window:
                    diagnostic_latest = diag_window
                    diagnostic_peak = max(diagnostic_peak, diag_window)
                    max_observed_window = max(max_observed_window, diag_window)
                u = msg.get("usage") or {}
                if not isinstance(u, dict):
                    continue
                ti = u.get("input_tokens") or 0
                tcw = u.get("cache_creation_input_tokens") or 0
                tcr = u.get("cache_read_input_tokens") or 0
                tout = u.get("output_tokens") or 0
                window = ti + tcw + tcr
                # Window/peak observe every event — re-seeing the same
                # usage is harmless for a max() — but totals must skip
                # message.ids we've already billed.
                if window:
                    latest = window
                    if window > peak:
                        peak = window
                    max_observed_window = max(max_observed_window, window)
                mid = msg.get("id") if isinstance(msg.get("id"), str) else None
                if mid:
                    if mid in seen_message_ids:
                        continue
                    seen_message_ids.add(mid)
                if isinstance(ti, int):
                    total_in += ti
                if isinstance(tcw, int):
                    total_cw += tcw
                if isinstance(tcr, int):
                    total_cr += tcr
                if isinstance(tout, int):
                    total_out += tout
                if window or tout:
                    turn_series.append({
                        "ts": ev.get("timestamp") or "",
                        "tokens_in": window,
                        "tokens_cached": tcr if isinstance(tcr, int) else 0,
                        "tokens_out": tout if isinstance(tout, int) else 0,
                    })
    except OSError:
        return _with_token_optimizer_quality({**empty, "model": model}, session_id)

    if not latest and diagnostic_latest:
        latest = diagnostic_latest
    if not peak and diagnostic_peak:
        peak = diagnostic_peak

    # Best-effort context limit. Claude Code's 1M-context variant uses a
    # `[1m]` suffix in some surfaces, but the JSONL strips it ("claude-
    # opus-4-7" either way), so the model name alone is unreliable.
    # Fallback signal: if any observed turn or compact pre/post count used
    # > 200k tokens, the session must be on the 1M variant (otherwise the API
    # would have errored). Keep that session-level signal even though the
    # displayed live peak resets at the most recent compact boundary.
    if "[1m]" in model.lower() or max_observed_window > 200_000:
        limit = 1_000_000
    else:
        limit = 200_000

    rate_in, rate_cw, rate_cr, rate_out = _rates_for_model(model)
    cost_in = total_in * rate_in / 1_000_000
    cost_cw = total_cw * rate_cw / 1_000_000
    cost_cr = total_cr * rate_cr / 1_000_000
    cost_out = total_out * rate_out / 1_000_000
    cost_total = cost_in + cost_cw + cost_cr + cost_out

    return _with_token_optimizer_quality({
        # Top-level effort, matching _extract_codex_usage's shape so a client
        # reads one key for every engine. Transcript-first for the same reason
        # the model pill is live-first: the assistant record states what the
        # turn actually ran at, while the override only knows what CCC asked
        # for and goes stale the moment the user types `/effort` in the TUI.
        "reasoning_effort": live_effort or (override or {}).get("reasoning_effort") or "",
        "latest_input_tokens": latest,
        "peak_input_tokens": peak,
        "total_output_tokens": total_out,
        "total_input_tokens": total_in,
        "total_cache_creation_tokens": total_cw,
        "total_cache_read_tokens": total_cr,
        "model": model,
        "context_limit": limit,
        "compact_count": compact_count,
        "live_context_tokens": live_context["tokens"] if live_context else 0,
        "live_context_limit": live_context["limit"] if live_context else 0,
        "live_context_percent": live_context["percent"] if live_context else 0,
        "live_context_timestamp": live_context["timestamp"] if live_context else "",
        "live_context_source": "/context" if live_context else "",
        "engine": "claude",
        "override": override,
        "turn_series": list(turn_series),
        "cost_usd": round(cost_total, 4),
        "cost_breakdown_usd": {
            "input": round(cost_in, 4),
            "cache_creation": round(cost_cw, 4),
            "cache_read": round(cost_cr, 4),
            "output": round(cost_out, 4),
        },
    }, session_id)
