#!/usr/bin/env python3
"""PostToolUse hook — writes sidecar state after every tool invocation."""

import json
import os
import sys
import time

LIVE_STATE_DIR = os.path.expanduser("~/.claude/log-viewer/live-state")
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit", "Bash"}


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

        # Set _writes flag if this is a write-capable tool
        writes_flag = os.path.join(LIVE_STATE_DIR, f"{session_id}_writes")
        if tool_name in WRITE_TOOLS:
            with open(writes_flag, "w") as f:
                f.write("1")

        has_writes = os.path.exists(writes_flag)

        # Extract a meaningful file/command reference
        file_ref = tool_input.get("file_path") or ""
        if not file_ref:
            cmd = tool_input.get("command") or ""
            file_ref = cmd[:80] if cmd else ""

        state = {
            "session_id": session_id,
            "tool": tool_name,
            "file": file_ref,
            "has_writes": has_writes,
            "status": "active",
            "timestamp": time.time(),
        }

        state_path = os.path.join(LIVE_STATE_DIR, f"{session_id}.json")
        tmp_path = state_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f)
        os.replace(tmp_path, state_path)

    except Exception:
        pass


if __name__ == "__main__":
    main()
