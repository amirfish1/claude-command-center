#!/usr/bin/env python3
"""UserPromptSubmit hook: enforce Hold and refresh reply-chip guidance.

The runaway card's Hold (ccc_server/runaway_hold.py) writes the session id to
~/.claude/command-center/runaway-holds.json. Timer fires (ScheduleWakeup,
CronCreate, /loop), peer messages and typed prompts all pass this hook, so a
held session wakes for nothing: the prompt is dropped before any model call.
Release on the card (or a dashboard composer send) removes the hold.

Allowed prompts also receive a short reply-chip reminder. Hooks are read from
disk on each prompt, so this reaches already-running sessions without waking
them or restarting their agent process. Any error lets the prompt through.
"""

import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/command-center")
HOLDS_FILE = os.path.join(STATE_DIR, "runaway-holds.json")
BLOCKS_LOG = os.path.join(STATE_DIR, "runaway-hold-blocks.log")


REPLY_CHIP_CONTEXT = """CCC reply-chip reminder: In your final <session-state> footer, supply
NEXT_ACTION as a single-line JSON array of {"label":"...","reply":"..."} for
2-4 explicit answer choices, including categorical questions such as which
notification type the user sees. Use one object for a genuinely required,
scoped go-ahead. Labels: 1-4 words, letters/spaces and optional commas between words,
at most 24 characters.
Replies: at most 400 characters; preserve the exact question tag and scope.
NEXT_STEP_USER must describe the actual pending question. Use NEXT_ACTION: null
when no reply choice or approval is needed. Do not invent choices, ask again
for authorized work, or create buttons claiming offline tasks were completed.
Keep your existing completion-assessment and footer requirements.
"""


def main():
    try:
        payload = json.load(sys.stdin)
        sid = str(payload.get("session_id") or "")
    except (ValueError, TypeError, AttributeError):
        return
    if not sid:
        return
    holds = {}
    try:
        with open(HOLDS_FILE, "r", encoding="utf-8") as f:
            holds = json.load(f)
    except (OSError, ValueError):
        pass
    if isinstance(holds, dict) and sid in holds:
        try:
            with open(BLOCKS_LOG, "a", encoding="utf-8") as f:
                f.write("%d %s\n" % (int(time.time()), sid))
        except OSError:
            pass
        print(json.dumps({
            "decision": "block",
            "reason": "This session is on Hold in Claude Command Center (runaway card). "
                      "Release it there, or send from the CCC composer, to let it run.",
        }))
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": REPLY_CHIP_CONTEXT,
    }}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
