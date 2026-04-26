#!/usr/bin/env python3
"""PreToolUse hook — writes an in-flight marker so the UI can show
"running X for 4s now" while a long tool (Bash, WebFetch, Read on a
large file) is still executing. PostToolUse clears it.

Pairs with post-tool-use.py to give the dashboard a true
currently-running signal, not just a most-recently-completed one.
"""

import json
import os
import sys
import time

LIVE_STATE_DIR = os.path.expanduser("~/.claude/command-center/live-state")


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
            file_ref = cmd[:80] if cmd else ""

        marker = {
            "session_id": session_id,
            "tool": tool_name,
            "file": file_ref,
            "started_at": time.time(),
        }

        path = os.path.join(LIVE_STATE_DIR, f"{session_id}_in_flight.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(marker, f)
        os.replace(tmp, path)

    except Exception:
        pass


if __name__ == "__main__":
    main()
