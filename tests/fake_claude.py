#!/usr/bin/env python3
"""Fake Claude Code CLI for CCC integration tests.

Speaks just enough of the `claude -p --input-format stream-json
--output-format stream-json` protocol that CCC's spawn / inject / ask /
resume plumbing can be exercised end to end without launching a real agent:

- emits a system init event carrying a session_id (new uuid, or the one
  passed via --resume);
- maintains a real transcript under ~/.claude/projects/<slug(cwd)>/ so the
  session is discoverable, resumable, and hand-off-able like a native one;
- answers every stdin user message with a canned assistant reply plus a
  stream-json result event (which /api/ask tails for).

Selected via CCC_CLAUDE_BIN in the two-node harness. Obviously-fake output
("FAKE-CLAUDE-REPLY") so no fixture can be mistaken for a real session.
"""

import json
import os
import re
import sys
import uuid


def _slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def main():
    args = sys.argv[1:]
    sid = None
    if "--resume" in args:
        try:
            sid = args[args.index("--resume") + 1]
        except IndexError:
            pass
    resumed = bool(sid)
    if not sid:
        sid = str(uuid.uuid4())
    cwd = os.getcwd()
    projects_root = os.path.expanduser("~/.claude/projects")
    transcript = os.path.join(projects_root, _slug(cwd), sid + ".jsonl")
    if resumed and not os.path.exists(transcript):
        # Resume from wherever the transcript actually lives (it may have
        # been imported into a different project slug by a handoff).
        try:
            for d in os.listdir(projects_root):
                cand = os.path.join(projects_root, d, sid + ".jsonl")
                if os.path.exists(cand):
                    transcript = cand
                    with open(cand, "r", encoding="utf-8") as fh:
                        for line in fh:
                            try:
                                ev = json.loads(line)
                            except ValueError:
                                continue
                            if ev.get("cwd"):
                                cwd = ev["cwd"]
                                break
                    break
        except OSError:
            pass
    os.makedirs(os.path.dirname(transcript), exist_ok=True)

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def record(obj):
        with open(transcript, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\n")

    emit({"type": "system", "subtype": "init", "session_id": sid,
          "cwd": cwd, "model": "fake-claude"})

    turns = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "user":
            continue
        content = (ev.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "".join(c.get("text", "") for c in (content or [])
                           if isinstance(c, dict))
        turns += 1
        record({"type": "user", "cwd": cwd, "sessionId": sid,
                "message": {"role": "user", "content": text}})
        if "ping" in text.lower():
            reply = f"FAKE-PONG {sid[:8]}"
        else:
            reply = f"FAKE-CLAUDE-REPLY[{turns}]: acknowledged {len(text)} chars"
        record({"type": "assistant", "cwd": cwd, "sessionId": sid,
                "message": {"role": "assistant", "model": "fake-claude",
                            "content": [{"type": "text", "text": reply}]}})
        emit({"type": "assistant", "session_id": sid,
              "message": {"role": "assistant",
                          "content": [{"type": "text", "text": reply}]}})
        emit({"type": "result", "subtype": "success", "session_id": sid,
              "result": reply, "total_cost_usd": 0.0, "duration_ms": 5,
              "num_turns": turns, "is_error": False})


if __name__ == "__main__":
    main()
