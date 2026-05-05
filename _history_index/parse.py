"""Parse Claude Code JSONL session logs into searchable records."""
from __future__ import annotations

import json
from typing import Any, Iterator, Optional

SEARCHABLE_TYPES = {"user", "assistant", "summary", "custom-title", "last-prompt"}

MAX_BLOCK_CHARS = 4000


def _truncate(s: str, n: int = MAX_BLOCK_CHARS) -> str:
    return s if len(s) <= n else s[:n] + "…"


def extract_text(record: dict) -> Optional[str]:
    """Pull searchable text out of one JSONL record. Returns None if nothing useful."""
    rtype = record.get("type")
    if rtype not in SEARCHABLE_TYPES:
        return None

    if rtype == "summary":
        return record.get("summary") or record.get("content")
    if rtype == "custom-title":
        return record.get("title") or record.get("content")
    if rtype == "last-prompt":
        return record.get("prompt") or record.get("content")

    msg = record.get("message") or {}
    content = msg.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = block.get("text")
            if t:
                parts.append(t)
        elif btype == "thinking":
            t = block.get("thinking")
            if t:
                parts.append(_truncate(t))
        elif btype == "tool_use":
            name = block.get("name", "")
            inp = block.get("input")
            try:
                inp_s = json.dumps(inp, ensure_ascii=False) if inp else ""
            except Exception:
                inp_s = str(inp)
            parts.append(f"[tool_use:{name}] {_truncate(inp_s)}")
        elif btype == "tool_result":
            tc = block.get("content")
            if isinstance(tc, list):
                for sub in tc:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        parts.append(_truncate(sub.get("text", "")))
            elif isinstance(tc, str):
                parts.append(_truncate(tc))
        elif btype == "image":
            parts.append("[image]")
    return "\n".join(parts) if parts else None


def parse_line(
    line: str, source_file: str, source_line: int, project_dir: str
) -> Optional[dict]:
    """Turn one JSONL line into a record dict, or None if not worth indexing."""
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except Exception:
        return None
    text = extract_text(rec)
    if not text:
        return None

    msg = rec.get("message") or {}
    return {
        "uuid": rec.get("uuid"),
        "session_id": rec.get("sessionId"),
        "parent_uuid": rec.get("parentUuid"),
        "type": rec.get("type"),
        "role": msg.get("role") or rec.get("type"),
        "cwd": rec.get("cwd"),
        "project_dir": project_dir,
        "git_branch": rec.get("gitBranch"),
        "timestamp": rec.get("timestamp"),
        "version": rec.get("version"),
        "slug": rec.get("slug"),
        "model": msg.get("model"),
        "source_file": source_file,
        "source_line": source_line,
        "content": text,
    }


def iter_records(path: str, project_dir: str, start_line: int = 0) -> Iterator[dict]:
    """Yield parsed records from a JSONL file starting at line offset start_line."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i < start_line:
                continue
            rec = parse_line(line, path, i, project_dir)
            if rec is not None:
                yield rec


# ---------------------------------------------------------------------------
# Codex (~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-<iso>-<uuid>.jsonl) parser.
# Codex's transcript schema is different from Claude Code's — every record
# is {timestamp, type, payload}, where type ∈ {session_meta, turn_context,
# response_item, event_msg, compacted}. Only response_item carries searchable
# message content; session_meta + turn_context carry cwd which we thread
# through to the records we yield.
# ---------------------------------------------------------------------------

_CODEX_TEXT_BLOCKS = {"input_text", "output_text", "text"}
_CODEX_INDEXABLE_ROLES = {"user", "assistant", "developer", "system"}


def extract_codex_text(record: dict) -> Optional[str]:
    """Pull searchable text from a Codex `response_item` record. Returns None
    for non-message items (function_call, function_call_output, reasoning, etc.)
    so we don't bloat the index with tool-loop noise.
    """
    if record.get("type") != "response_item":
        return None
    payload = record.get("payload") or {}
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return None
    if payload.get("role") not in _CODEX_INDEXABLE_ROLES:
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in _CODEX_TEXT_BLOCKS:
            t = block.get("text")
            if t:
                parts.append(_truncate(t))
    return "\n".join(parts) if parts else None


def iter_records_codex(
    path: str, session_id: str, start_line: int = 0
) -> Iterator[dict]:
    """Yield message records from a Codex JSONL transcript, in claude-index's
    canonical message-dict shape so the rest of the pipeline doesn't care
    which agent runtime produced the file.

    We synthesize a deterministic uuid `codex:<session_id>:<line:06d>` since
    Codex records have no per-message uuid. Line offset is stable as long
    as we only ever append (which Codex does), so the same line always
    yields the same uuid across runs — UNIQUE constraint in messages then
    makes ingest idempotent.

    cwd tracking: session_meta and turn_context update `current_cwd`, which
    is then attached to every following response_item until the next
    transition. We process those state-update records BEFORE checking
    start_line so that incremental re-ingest doesn't lose cwd context
    that was set early in the file.
    """
    current_cwd: Optional[str] = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            line_s = line.strip()
            if not line_s:
                continue
            try:
                rec = json.loads(line_s)
            except Exception:
                continue
            rtype = rec.get("type")
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}

            # State updates apply regardless of start_line, so resumed ingest
            # still carries the correct cwd onto records past start_line.
            if rtype == "session_meta":
                current_cwd = payload.get("cwd") or current_cwd
                if not session_id and payload.get("id"):
                    session_id = payload["id"]
                continue
            if rtype == "turn_context":
                current_cwd = payload.get("cwd") or current_cwd
                continue

            if i < start_line:
                continue
            if rtype != "response_item":
                continue

            text = extract_codex_text(rec)
            if not text:
                continue

            role = payload.get("role")
            yield {
                "uuid": f"codex:{session_id}:{i:06d}",
                "session_id": session_id,
                "parent_uuid": None,
                "type": role or "unknown",
                "role": role,
                "cwd": current_cwd,
                "project_dir": "_codex",
                "git_branch": None,
                "timestamp": rec.get("timestamp"),
                "version": None,
                "slug": None,
                "model": payload.get("model"),
                "source_file": path,
                "source_line": i,
                "content": text,
            }
