#!/usr/bin/env python3
"""PreToolUse hook — writes an in-flight marker so the UI can show
"running X for 4s now" while a long tool (Bash, WebFetch, Read on a
large file) is still executing. PostToolUse clears it.

Pairs with post-tool-use.py to give the dashboard a true
currently-running signal, not just a most-recently-completed one.
"""

import json
import os
import re
import sys
import time

LIVE_STATE_DIR = os.path.expanduser("~/.claude/command-center/live-state")
# Question-relay coordination dir. When a CCC-spawned headless session calls
# AskUserQuestion, this hook blocks here waiting for the dashboard to drop an
# answer file (see _question_relay below). Kept in sync with
# QUESTION_RELAY_DIR in server.py.
QUESTION_RELAY_DIR = os.path.expanduser("~/.claude/command-center/questions")
# Only relay (and block) when CCC explicitly spawned this session and is
# therefore listening for the answer. Interactive terminals render the native
# AskUserQuestion picker and must NOT be intercepted; non-CCC headless scripts
# would just hang. CCC sets this env var on the sessions it spawns.
QUESTION_RELAY_ENV = "CCC_QUESTION_RELAY"
# How long the hook blocks before giving up and letting the question fall
# through to Claude Code's own (auto-decline) handling. Capped just under the
# hook `timeout` registered in settings.json so we return our own clean
# message before Claude Code force-kills the hook.
try:
    QUESTION_RELAY_TIMEOUT = max(5, int(os.environ.get("CCC_QUESTION_TIMEOUT", "1740")))
except ValueError:
    QUESTION_RELAY_TIMEOUT = 1740
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
    option_details = []
    for opt in q.get("options") or []:
        if isinstance(opt, dict):
            label = opt.get("label")
            description = prompt_fragment(opt.get("description"), 240)
        else:
            label = opt
            description = ""
        label = prompt_fragment(label, 80)
        if label:
            options.append(label)
            option_details.append({"label": label, "description": description})
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
    return {
        "header": header,
        "question": question,
        "options": options,
        "option_details": option_details,
        "summary": summary,
    }


def full_questions(tool_input):
    """Untruncated questions for the dashboard modal + answer mapping.

    Unlike ask_user_question_payload (which is a compact, truncated summary
    for the activity strip and keeps only questions[0]), this preserves every
    question and full option label/description so the modal renders faithfully
    and we can map a chosen option index back to its exact label.
    """
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list):
        return []
    out = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        options = []
        for opt in q.get("options") or []:
            if isinstance(opt, dict):
                label = opt.get("label") or ""
                description = opt.get("description") or ""
            else:
                label = str(opt or "")
                description = ""
            if label:
                options.append({"label": label, "description": description})
        out.append({
            "header": q.get("header") or "",
            "question": q.get("question") or "",
            "multiSelect": bool(q.get("multiSelect")),
            "options": options,
        })
    return out


def build_answer_reason(questions, answers):
    """Render the user's picks into the tool-result text the model receives.

    `answers` is aligned to `questions`; each item is {"index": int, "text": str}.
    A non-negative index resolves to that option's full (untruncated) label;
    otherwise the free-text answer is used verbatim. Mirrors the phrasing
    Claude Code itself writes when a question is answered in the TUI.
    """
    parts = []
    for i, q in enumerate(questions):
        ans = answers[i] if i < len(answers) and isinstance(answers[i], dict) else {}
        idx = ans.get("index", -1)
        value = ""
        opts = q.get("options") or []
        if isinstance(idx, int) and 0 <= idx < len(opts):
            value = opts[idx].get("label") or ""
        if not value:
            value = (ans.get("text") or "").strip()
        if not value:
            value = "(no answer)"
        question = q.get("question") or q.get("header") or "Question"
        parts.append('"%s" = "%s"' % (question, value))
    if not parts:
        return "User answered the question. Proceed with your best judgment."
    noun = "question" if len(parts) == 1 else "questions"
    pronoun = "this answer" if len(parts) == 1 else "these answers"
    return (
        "User answered the %s. %s. Proceed using %s; do not ask again."
        % (noun, "; ".join(parts), pronoun)
    )


def _deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))


def question_relay(session_id, tool_input):
    """Block until the CCC dashboard answers this AskUserQuestion.

    Headless `claude -p` auto-declines AskUserQuestion instantly (no TUI to
    show a picker), so the only way to let a human answer is to intercept the
    tool here, surface it to the dashboard, and feed the chosen answer back as
    a PreToolUse deny-reason — which the model reads as the tool result.

    Returns True if a decision was emitted (caller must stop), else False.
    """
    questions = full_questions(tool_input)
    if not questions:
        return False
    try:
        os.makedirs(QUESTION_RELAY_DIR, exist_ok=True)
    except OSError:
        return False
    nonce = "%s-%d-%d" % (session_id, os.getpid(), int(time.time() * 1000))
    req_path = os.path.join(QUESTION_RELAY_DIR, "%s.request.json" % session_id)
    ans_path = os.path.join(QUESTION_RELAY_DIR, "%s.answer.json" % session_id)
    request = {
        "nonce": nonce,
        "session_id": session_id,
        "pid": os.getpid(),
        "ts": time.time(),
        "questions": questions,
    }
    # Clear any stale answer from a previous question on this session before
    # we start polling, so we never consume an old pick by accident.
    _unlink_quiet(ans_path)
    tmp = req_path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(request, f)
        os.replace(tmp, req_path)
    except OSError:
        return False

    deadline = time.time() + QUESTION_RELAY_TIMEOUT
    try:
        while time.time() < deadline:
            answer = None
            try:
                with open(ans_path) as f:
                    answer = json.load(f)
            except (OSError, ValueError):
                answer = None
            if isinstance(answer, dict) and answer.get("nonce") == nonce:
                _unlink_quiet(ans_path)
                _unlink_quiet(req_path)
                _deny(build_answer_reason(questions, answer.get("answers") or []))
                return True
            time.sleep(0.25)
    finally:
        _unlink_quiet(req_path)
    _deny(
        "No answer was provided in Claude Command Center within the wait "
        "window. Treat this question as unanswered: either proceed with a "
        "sensible default and state the assumption, or ask again concisely."
    )
    return True


def _unlink_quiet(path):
    try:
        os.unlink(path)
    except OSError:
        pass


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

        file_ref = tool_input.get("file_path") or ""
        if not file_ref:
            cmd = tool_input.get("command") or ""
            file_ref = shell_command_preview(cmd) if tool_name == "Bash" else prompt_fragment(cmd)
        question_payload = ask_user_question_payload(tool_input) if tool_name == "AskUserQuestion" else {}
        if question_payload:
            file_ref = question_payload["summary"]

        marker = {
            "session_id": session_id,
            "tool": tool_name,
            "file": file_ref,
            "started_at": time.time(),
        }
        marker.update(question_payload)

        path = os.path.join(LIVE_STATE_DIR, f"{session_id}_in_flight.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(marker, f)
        os.replace(tmp, path)

        # The in-flight marker is now visible to the dashboard. If CCC spawned
        # this session, intercept AskUserQuestion and block until the user
        # answers in the UI (headless claude can't show the native picker).
        if (
            tool_name == "AskUserQuestion"
            and question_payload
            and os.environ.get(QUESTION_RELAY_ENV) == "1"
        ):
            question_relay(session_id, tool_input)

    except Exception:
        pass


if __name__ == "__main__":
    main()
