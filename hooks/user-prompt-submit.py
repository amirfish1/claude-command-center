#!/usr/bin/env python3
"""UserPromptSubmit hook: blocks every prompt to a session on runaway Hold.

The runaway card's Hold (ccc_server/runaway_hold.py) writes the session id to
~/.claude/command-center/runaway-holds.json. Timer fires (ScheduleWakeup,
CronCreate, /loop), peer messages and typed prompts all pass this hook, so a
held session wakes for nothing: the prompt is dropped before any model call.
Release on the card (or a dashboard composer send) removes the hold.

Hooks must exit fast and never prompt: one stat + small read; any error lets
the prompt through.
"""

import json
import os
import sys
import time

STATE_DIR = os.path.expanduser("~/.claude/command-center")
HOLDS_FILE = os.path.join(STATE_DIR, "runaway-holds.json")
BLOCKS_LOG = os.path.join(STATE_DIR, "runaway-hold-blocks.log")


def main():
    if not os.path.exists(HOLDS_FILE):
        return
    try:
        payload = json.load(sys.stdin)
        sid = str(payload.get("session_id") or "")
        with open(HOLDS_FILE, "r", encoding="utf-8") as f:
            holds = json.load(f)
    except Exception:
        return
    if not sid or not isinstance(holds, dict) or sid not in holds:
        return
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


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
