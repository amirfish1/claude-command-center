"""Recover CCC-captured CLI output when a native Codex rollout is empty.

The capture is a read-only display source. Native output always takes
precedence; neither the rollout nor Codex's databases are rewritten.
"""
from __future__ import annotations

import json
import hashlib
import re
import uuid
from pathlib import Path

from ccc_server import core as _core

_NATIVE_READINESS = {}
_CAPTURE_PATHS = {}
_CAPTURE_ROWS = {}
_CAPTURE_TAILS = {}
_CAPTURE_HEADERS = {}


def _codex_capture_corpus_signature():
    """Stat-only archive invalidation for CLI runs without native files."""
    stats = []
    logs = _core._recent_codex_ccc_log_paths(repo_paths=_core._known_repo_paths(), max_logs=200)
    for log in logs:
        try:
            st = Path(log).stat()
            stats.append((str(log), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return hashlib.sha256(repr(stats).encode()).hexdigest()


def _codex_capture_header_id(log_path):
    """Read only the bounded CLI header, caching by file identity."""
    try:
        st = Path(log_path).stat()
        key = (str(log_path), st.st_ino, st.st_mtime_ns, st.st_size)
        if key in _CAPTURE_HEADERS:
            return _CAPTURE_HEADERS[key]
        sid = None
        with Path(log_path).open(encoding="utf-8", errors="replace") as source:
            for _ in range(5):
                line = source.readline(65536)
                if not line:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "thread.started":
                    sid = event.get("thread_id")
                    break
        if len(_CAPTURE_HEADERS) >= 2048:
            _CAPTURE_HEADERS.clear()
        _CAPTURE_HEADERS[key] = sid
        return sid
    except OSError:
        return None


def _codex_capture_rows(native_rows, spawn_by_sid, repo_path=None):
    """List exact-thread CLI captures omitted from Codex's native store."""
    native_ids = {row.get("id") for row in native_rows}
    repositories = [repo_path] if repo_path else _core._known_repo_paths()
    log_repos = {str(_core.repo_log_dir(repo)): repo for repo in repositories}
    out = []
    for log in _core._recent_codex_ccc_log_paths(repo_paths=repositories, max_logs=200):
        sid = _codex_capture_header_id(log)
        if not sid or sid in native_ids:
            continue
        spawn = spawn_by_sid.get(sid) or {}
        cwd = spawn.get("cwd") or log_repos.get(str(Path(log).parent)) or ""
        if not cwd:
            continue
        if len(_CAPTURE_ROWS) >= 2048:
            _CAPTURE_ROWS.clear()
        title = re.sub(r"^spawn-codex-|-[0-9]{8}T[0-9]{6}$", "", Path(log).stem).replace("-", " ")
        row = {"id": sid, "cwd": cwd, "title": spawn.get("prompt") or title,
               "first_user_message": spawn.get("prompt") or "",
               "model": spawn.get("model") or "", "_ccc_capture": str(log)}
        _CAPTURE_ROWS[sid] = row
        # A deep link may have been opened before the capture was discovered.
        with _core._engine_detect_lock:
            _core._ENGINE_DETECT_CACHE[sid] = ("codex", None)
        out.append(row)
        native_ids.add(sid)
    return out


def _codex_capture_thread_row(session_id):
    row = _CAPTURE_ROWS.get(session_id)
    if row:
        return row
    # Codex exec uses UUIDv7 IDs; Claude UUIDv4 misses must never scan logs.
    try:
        if uuid.UUID(str(session_id)).version != 7:
            return None
    except ValueError:
        return None
    # Direct links can arrive before any list rebuild after a restart.
    # Header discovery is bounded and memoized; it does not parse transcripts.
    _codex_capture_rows([], {}, None)
    return _CAPTURE_ROWS.get(session_id)


def _codex_capture_tail(session_id, log_path):
    """Memoize list metadata; full captures are only parsed after changes."""
    try:
        st = Path(log_path).stat()
    except OSError:
        return {}
    key = (str(log_path), st.st_mtime_ns, st.st_size)
    if key in _CAPTURE_TAILS:
        return _CAPTURE_TAILS[key]
    tail = {}
    for line in _core._tail_read_lines(log_path, max_bytes=131072):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        item = event.get("item") or {}
        if kind in ("turn.completed", "turn.failed"):
            tail["last_event_type"] = "result"
        elif isinstance(item, dict) and item.get("type") == "agent_message":
            tail["last_event_type"] = "assistant"
            tail["last_assistant_text"] = item.get("text") or ""
        elif isinstance(item, dict) and item.get("type") == "command_execution":
            tail["last_event_type"] = "tool_result" if kind == "item.completed" else "assistant"
            tail["pending_tool"] = None if kind == "item.completed" else "Bash"
    if len(_CAPTURE_TAILS) >= 512:
        _CAPTURE_TAILS.clear()
    _CAPTURE_TAILS[key] = tail
    return tail


def _codex_capture_fingerprint(session_id):
    """Cheap stat-only invalidation for captures discovered by recovery."""
    signature = []
    for filename in _CAPTURE_PATHS.get(session_id, ()):
        try:
            st = Path(filename).stat()
            signature.append((filename, st.st_mtime_ns, st.st_size))
        except OSError:
            signature.append((filename, 0, 0))
    return tuple(signature)


def _codex_native_recovery_metadata(native_path):
    """Cache native readiness by file identity; stop at the first response."""
    prompt = ""
    ts = ""
    meta = {}
    try:
        st = Path(native_path).stat()
        key = (str(native_path), st.st_ino, st.st_mtime_ns, st.st_size)
        if key in _NATIVE_READINESS:
            return _NATIVE_READINESS[key]
        if len(_NATIVE_READINESS) >= 512:
            _NATIVE_READINESS.clear()
        with Path(native_path).open(encoding="utf-8", errors="replace") as source:
            for line_number, raw in enumerate(source, 1):
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                if row.get("type") == "turn_context":
                    meta.update(_core._codex_turn_meta_from_event(row) or {})
                if payload.get("type") == "task_started":
                    ts = row.get("timestamp") or ts
                    meta["turn_id"] = payload.get("turn_id") or meta.get("turn_id", "")
                parsed = _core._parse_codex_event(row, line_number)
                if not parsed:
                    continue
                # A native response is authoritative, including tool-only or
                # reasoning responses. Never append a second copy from logs.
                if parsed.get("type") in ("assistant", "tool_result") or (
                    parsed.get("type") == "result" and not parsed.get("no_agent_output")
                ):
                    _NATIVE_READINESS[key] = None
                    return None
                if parsed.get("type") == "user_text":
                    prompt = parsed.get("text") or prompt
    except OSError:
        return None
    if len(_NATIVE_READINESS) >= 512:
        _NATIVE_READINESS.clear()
    metadata = (prompt, ts, meta)
    _NATIVE_READINESS[key] = metadata
    return metadata


def _codex_recover_log_conversation(session_id, native_path):
    """Render exact-thread CLI captures while native output is absent.

    Native readiness is memoized; capture output is read fresh because it can
    grow without changing the native rollout used by the normal parse cache.
    """
    metadata = _codex_native_recovery_metadata(native_path) if native_path else ("", "", {})
    if metadata is None:
        _CAPTURE_PATHS.pop(session_id, None)
        return None
    prompt, ts, native_meta = metadata
    meta = dict(native_meta)

    logs = _core._codex_logs_for_session(session_id)
    if len(_CAPTURE_PATHS) >= 512:
        _CAPTURE_PATHS.clear()
    _CAPTURE_PATHS[session_id] = tuple(str(filename) for _, filename in logs)
    if not logs:
        return None
    thread = _core._codex_thread_row(session_id) or _codex_capture_thread_row(session_id) or {}
    prompt = prompt or thread.get("first_user_message") or ""
    meta.setdefault("model", thread.get("model") or "")
    events = []
    visible = False

    def emit(event):
        event.update({"line": len(events) + 1, "ts": ts, "recovered_from_log": True})
        for key, value in meta.items():
            if value:
                event[key] = value
        events.append(event)

    if prompt:
        prompt = _core._strip_ccc_session_state_instruction(prompt)
        prompt = _core._strip_mode3_instruction(prompt)
        emit({"type": "user_text", "text": prompt, "images": []})

    for log_index, (_, log_path) in enumerate(logs):
        # Revalidate even though discovery correlates by ID: a changed or
        # misidentified capture must never reveal another thread's output.
        if _core._extract_codex_thread_id_from_log(log_path) != session_id:
            continue
        tools = set()
        completed = set()
        last_text = ""
        try:
            with Path(log_path).open(encoding="utf-8", errors="replace") as capture:
                for raw in capture:
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    kind = row.get("type")
                    item = row.get("item")
                    if kind in ("item.started", "item.completed") and isinstance(item, dict):
                        item_id = str(item.get("id") or "")
                        event_id = f"codex-capture-{log_index}-{item_id}"
                        item_type = item.get("type")
                        if kind == "item.completed" and item_id in completed:
                            continue
                        if kind == "item.completed":
                            completed.add(item_id)
                        if item_type in ("agent_message", "reasoning") and kind == "item.completed":
                            text = item.get("text") or ""
                            if not isinstance(text, str) or not text.strip():
                                continue
                            block_kind = "text" if item_type == "agent_message" else "thinking"
                            parsed = (_core._parse_codex_exec_log_event(row, len(events) + 1)
                                      if item_type == "agent_message" else None)
                            emit(parsed or {"type": "assistant", "message_id": event_id,
                                            "blocks": [{"kind": block_kind, "text": text}]})
                            visible = True
                            if item_type == "agent_message":
                                last_text = text
                        elif item_type in ("command_execution", "mcp_tool_call", "file_change", "web_search"):
                            if item_id not in tools:
                                tools.add(item_id)
                                command = item.get("command") or ""
                                name = {"command_execution": "Bash", "file_change": "apply_patch",
                                        "web_search": "web_search"}.get(item_type, item.get("tool") or "tool")
                                command = _core._redacted_shell_command_text(str(command), max_len=12000) if command else ""
                                detail = command or item.get("query") or item.get("tool") or name
                                block = {"kind": "tool_use", "id": event_id, "name": name,
                                         "detail": _core._prompt_fragment(str(detail), 200)}
                                if command:
                                    block["command"] = command
                                emit({"type": "assistant", "message_id": event_id, "blocks": [block]})
                                visible = True
                            if kind == "item.completed":
                                output = item.get("aggregated_output")
                                if output is None:
                                    output = item.get("result") or ""
                                if not isinstance(output, str):
                                    output = json.dumps(output, ensure_ascii=False)
                                emit({"type": "tool_result", "tool_use_id": event_id,
                                      "text": output[:800], "is_error": item.get("status") == "failed"})
                    elif kind in ("turn.completed", "turn.failed"):
                        usage = row.get("usage")
                        event = _core._parse_codex_exec_log_event(row, len(events) + 1) or {"type": "result", "duration_ms": "?"}
                        if isinstance(usage, dict):
                            event["token_usage"] = dict(usage)
                            _core._attach_codex_token_usage(events, usage)
                        if not last_text:
                            event["no_agent_output"] = True
                        if kind == "turn.failed":
                            error = row.get("error") or {}
                            event["turn_failed_error"] = error.get("message", "Codex run failed") if isinstance(error, dict) else str(error)
                        emit(event)
        except OSError:
            continue
    if not visible:
        return None
    return {"events": events, "last_line": len(events)}
