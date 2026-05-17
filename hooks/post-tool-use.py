#!/usr/bin/env python3
"""PostToolUse hook — writes sidecar state after every tool invocation."""

import json
import os
import re
import sys
import time

LIVE_STATE_DIR = os.path.expanduser("~/.claude/command-center/live-state")
SECRET_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{16,}|gsk_[a-z0-9_-]{16,}|xox[abprs]-[a-z0-9-]{16,})\b"
)


def prompt_fragment(text, max_len=240):
    if not isinstance(text, str):
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = SECRET_RE.sub("[redacted]", text)
    if len(text) > max_len:
        return text[: max_len - 3].rstrip() + "..."
    return text


def shell_preview_parts(command):
    parts = []
    buf = []
    quote = ""
    escaped = False
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            segment = "".join(buf).strip()
            if segment:
                parts.append(("segment", segment))
            parts.append(("op", command[i:i + 2]))
            buf = []
            i += 2
            continue
        if ch == ";":
            segment = "".join(buf).strip()
            if segment:
                parts.append(("segment", segment))
            parts.append(("op", ";"))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segment = "".join(buf).strip()
    if segment:
        parts.append(("segment", segment))
    return parts


def shell_wrapper_segment(segment):
    text = re.sub(r"\s+", " ", segment or "").strip()
    text = re.sub(r"(?:^|\s+)2>\s*/dev/null\b", "", text).strip()
    if text in ("true", ":"):
        return True
    if re.fullmatch(r"(?:(?:command|builtin)\s+)?(?:setopt|unsetopt)\s+[-A-Za-z0-9_\s]+", text):
        return True
    if re.fullmatch(r"(?:(?:command|builtin)\s+)?shopt\s+-[su]\s+[-A-Za-z0-9_\s]+", text):
        return True
    return False


def shell_command_preview(command, max_len=1000):
    if not isinstance(command, str):
        return ""
    raw = SECRET_RE.sub("[redacted]", re.sub(r"\s+", " ", command).strip())
    if not raw:
        return ""
    kept = []
    for kind, value in shell_preview_parts(raw):
        if kind == "segment":
            if shell_wrapper_segment(value):
                continue
            if kept and kept[-1][0] == "op":
                kept.append((kind, value))
            elif not kept or kept[-1][0] != "segment":
                kept.append((kind, value))
            else:
                kept.append(("op", ";"))
                kept.append((kind, value))
            continue
        if kept and kept[-1][0] == "segment":
            kept.append((kind, value))
    while kept and kept[-1][0] == "op":
        kept.pop()
    cleaned = " ".join(value for _, value in kept).strip() or raw
    return prompt_fragment(cleaned, max_len)


def ask_user_question_payload(tool_input):
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list) or not questions or not isinstance(questions[0], dict):
        return {}
    q = questions[0]
    header = prompt_fragment(q.get("header"), 80)
    question = prompt_fragment(q.get("question"), 160)
    options = []
    for opt in q.get("options") or []:
        label = opt.get("label") if isinstance(opt, dict) else opt
        label = prompt_fragment(label, 80)
        if label:
            options.append(label)
    parts = []
    if header:
        parts.append(header + ":")
    if question:
        parts.append(question)
    if options:
        shown = options[:3]
        parts.append("Options: " + "; ".join(shown) + ("; ..." if len(options) > len(shown) else ""))
    summary = prompt_fragment(" ".join(parts), 240)
    if not summary:
        return {}
    return {"header": header, "question": question, "options": options, "summary": summary}


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)

        session_id = data.get("session_id", "")
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}

        if not session_id:
            return

        os.makedirs(LIVE_STATE_DIR, exist_ok=True)

        # Any tool invocation marks the session as "writing" for kanban
        # classification — the column rule is "live + has_writes → Working".
        writes_flag = os.path.join(LIVE_STATE_DIR, f"{session_id}_writes")
        if tool_name:
            with open(writes_flag, "w") as f:
                f.write("1")

        # Clear the in-flight marker pre-tool-use.py wrote when the tool
        # started — the tool has now returned.
        in_flight = os.path.join(LIVE_STATE_DIR, f"{session_id}_in_flight.json")
        try:
            os.unlink(in_flight)
        except OSError:
            pass

        # Clear the needs-approval marker notification.py wrote — the user
        # answered the permission prompt (approve/deny) and the tool ran,
        # so the badge should drop off the kanban card.
        needs_approval = os.path.join(LIVE_STATE_DIR, f"{session_id}_needs_approval.json")
        try:
            os.unlink(needs_approval)
        except OSError:
            pass

        has_writes = os.path.exists(writes_flag)

        # Extract a meaningful file/command reference
        file_ref = tool_input.get("file_path") or ""
        if not file_ref:
            cmd = tool_input.get("command") or ""
            file_ref = shell_command_preview(cmd) if tool_name == "Bash" else prompt_fragment(cmd)
        question_payload = ask_user_question_payload(tool_input) if tool_name == "AskUserQuestion" else {}
        if question_payload:
            file_ref = question_payload["summary"]

        state = {
            "session_id": session_id,
            "tool": tool_name,
            "file": file_ref,
            "has_writes": has_writes,
            "status": "active",
            "timestamp": time.time(),
        }
        state.update(question_payload)

        state_path = os.path.join(LIVE_STATE_DIR, f"{session_id}.json")
        tmp_path = state_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f)
        os.replace(tmp_path, state_path)

    except Exception:
        pass


if __name__ == "__main__":
    main()
