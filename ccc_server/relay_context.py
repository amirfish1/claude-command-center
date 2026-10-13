"""Full-context readers for the cloud relay (attention items and session.detail).

The session rows only carry a short `pending_file` preview (what the UI shows)
and a scraped one-line prose question. An external agent deciding whether to
act needs the complete pending tool input and the complete final assistant
message. This module reads both straight from the transcript tail, on demand,
only for candidate sessions (those already flagged waiting), and caches the
parse by (mtime, size) so a push loop re-reading the same transcript is free.

Stdlib only. No redaction here: callers pass the text through
cloud_relay.redact_cap before it leaves the machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading

# Tail windows tried in order until the wanted event is found. Tool-heavy
# transcripts push the last text turn far from EOF, so one fixed 64 KiB window
# returns nothing (the empty-`turns` bug); widen instead of giving up.
TAIL_WINDOWS = (64 * 1024, 512 * 1024, 4 * 1024 * 1024)

_CACHE_MAX = 128
_cache: dict = {}
_cache_lock = threading.Lock()


def _stat_key(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _cached(kind, path, compute):
    key = _stat_key(path)
    if key is None:
        return None
    ck = (kind, path)
    with _cache_lock:
        hit = _cache.get(ck)
        if hit and hit[0] == key:
            return hit[1]
    val = compute()
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)), None)
        _cache[ck] = (key, val)
    return val


def read_tail_lines(path, tail_bytes):
    """Last ~tail_bytes of a JSONL file as parsed dicts (partial first line
    dropped). Returns (events, whole_file_read)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            whole = size <= tail_bytes
            if not whole:
                f.seek(size - tail_bytes)
                f.readline()
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return [], True
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out, whole


def _message(obj):
    """(role, content) of a transcript event across the Claude shape and the
    Codex `response_item` wrapper."""
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else None
    if payload is not None and obj.get("type") == "response_item" \
            and payload.get("type") == "message":
        return payload.get("role"), payload.get("content")
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
    role = obj.get("role")
    if role is None and msg is not None:
        role = msg.get("role")
    if role is None:
        t = str(obj.get("type") or "").lower()
        if t in ("user", "assistant", "system"):
            role = t
    content = msg.get("content") if msg is not None else None
    if content is None:
        content = obj.get("content") or obj.get("text")
    return role, content


def content_to_text(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") in ("tool_use", "tool_result", "thinking",
                                         "redacted_thinking"):
                    continue
                if block.get("text"):
                    parts.append(str(block["text"]))
        return "\n".join(parts)
    return str(content)


def extract_turn(obj):
    """(role, text) for a user/assistant/system text turn, else (None, '')."""
    role, content = _message(obj)
    if role not in ("user", "assistant", "system"):
        return None, ""
    return role, content_to_text(content)


def read_turns(path, max_turns, tail_bytes=TAIL_WINDOWS[0]):
    """Last `max_turns` raw (role, text) turns. Starts at `tail_bytes` and
    widens until enough turns exist or the whole file was read."""
    windows = [tail_bytes] + [w for w in TAIL_WINDOWS if w > tail_bytes]
    turns = []
    for w in windows:
        events, whole = read_tail_lines(path, w)
        turns = []
        for obj in events:
            role, text = extract_turn(obj)
            if role is None or not text:
                continue
            turns.append((role, text))
        if len(turns) >= max_turns or whole:
            break
    return turns[-max_turns:]


# ---------------------------------------------------------------------------
# Pending tool input
# ---------------------------------------------------------------------------

def _pending_tool(path):
    for w in TAIL_WINDOWS:
        events, whole = read_tail_lines(path, w)
        pending: dict = {}
        saw_assistant = False
        for obj in events:
            role, content = _message(obj)
            if not isinstance(content, list):
                if role == "user":
                    pending.clear()
                continue
            if role == "assistant":
                saw_assistant = True
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        pending[b.get("id") or len(pending)] = (
                            b.get("name"), b.get("input"))
            elif role == "user":
                has_result = False
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        pending.pop(b.get("tool_use_id"), None)
                        has_result = True
                if not has_result:
                    pending.clear()
        if pending:
            name, tin = list(pending.values())[-1]
            return {"tool": str(name or ""), "input": tin}
        if saw_assistant or whole:
            return None
    return None


def last_pending_tool(path):
    """{"tool", "input"} of the newest tool_use without a result, or None."""
    return _cached("tool", path, lambda: _pending_tool(path))


def _short(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "..."


def format_action(tool, tin, limit=2000):
    """Complete, human-readable action for a pending tool call. Bash gives the
    full command; Edit/Write give the file path plus a short change summary;
    anything else a compact JSON of its input. Bounded by `limit`."""
    tool = str(tool or "").strip()
    if not isinstance(tin, dict):
        return tool
    path = tin.get("file_path") or tin.get("path") or tin.get("notebook_path")
    if tool == "Bash":
        cmd = str(tin.get("command") or "")
        desc = str(tin.get("description") or "").strip()
        text = f"Bash: {cmd}"
        if desc:
            text += f"\n# {desc}"
    elif tool in ("Edit", "MultiEdit") and path:
        edits = tin.get("edits") if tool == "MultiEdit" else [tin]
        parts = []
        for e in (edits or [])[:5]:
            if not isinstance(e, dict):
                continue
            old, new = str(e.get("old_string") or ""), str(e.get("new_string") or "")
            parts.append(f"-{len(old.splitlines())}/+{len(new.splitlines())} lines: "
                         f"{_short(old, 120)!r} -> {_short(new, 120)!r}")
        text = f"{tool} {path}" + ("\n" + "\n".join(parts) if parts else "")
    elif tool == "Write" and path:
        body = str(tin.get("content") or "")
        text = (f"Write {path} ({len(body.splitlines())} lines, "
                f"{len(body)} chars)\n{_short(body, 300)}")
    elif tool == "NotebookEdit" and path:
        text = f"NotebookEdit {path} ({tin.get('edit_mode') or 'replace'})"
    else:
        try:
            body = json.dumps(tin, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            body = str(tin)
        text = f"{tool}: {body}" if tool else body
    return _short(text, limit)


def pending_action(path, limit=2000):
    """Full action text for the session's pending tool call, or ''."""
    if not path:
        return ""
    p = last_pending_tool(path)
    if not p:
        return ""
    return format_action(p["tool"], p["input"], limit)


# Hard cap on the full tool input an approval carries (chars of canonical JSON).
# Over the cap the text is cut AND `truncated` is set: never silently dropped.
INPUT_CAP = 16 * 1024


def canonical_input(tool, tin):
    """Canonical JSON of a tool call: the one text the approval hash is pinned
    over. The CCC PermissionRequest hook builds the same string from its own
    stdin, so keep both in lockstep (hooks/permission-request.py)."""
    try:
        return json.dumps({"tool": str(tool or ""), "input": tin},
                          sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"tool": str(tool or ""), "input": str(tin)},
                          sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)


def input_sha256(canonical):
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pending_input(path, cap=INPUT_CAP):
    """The session's pending tool call in full: {"tool", "text", "truncated",
    "sha256"} or None. `sha256` covers the WHOLE canonical input even when
    `text` was cut at `cap`, so a truncated item can never be mistaken for a
    complete one."""
    if not path:
        return None
    p = last_pending_tool(path)
    if not p or not isinstance(p.get("input"), dict):
        return None
    full = canonical_input(p["tool"], p["input"])
    return {"tool": p["tool"], "text": full[:cap],
            "truncated": len(full) > cap, "sha256": input_sha256(full)}


# ---------------------------------------------------------------------------
# Final assistant message
# ---------------------------------------------------------------------------

def _last_assistant_text(path):
    for w in TAIL_WINDOWS:
        events, whole = read_tail_lines(path, w)
        text = ""
        reset = False
        for obj in events:
            role, content = _message(obj)
            if role == "assistant":
                t = content_to_text(content).strip()
                if t:
                    text = t
            elif role == "user" and not (
                    isinstance(content, list) and any(
                        isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in content)):
                text = ""  # a real human message resets: nothing asked since
                reset = True
        if text or whole or reset:
            return text
    return ""


def last_assistant_text(path):
    """Full text of the session's last assistant message, or ''."""
    if not path:
        return ""
    return _cached("text", path, lambda: _last_assistant_text(path)) or ""
