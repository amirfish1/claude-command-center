#!/usr/bin/env python3
"""Stop hook — marks session as waiting for input."""

import json
import os
import sys
import time

LIVE_STATE_DIR = os.path.expanduser("~/.claude/log-viewer/live-state")


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)

        session_id = data.get("session_id", "")
        if not session_id:
            return

        os.makedirs(LIVE_STATE_DIR, exist_ok=True)

        writes_flag = os.path.join(LIVE_STATE_DIR, f"{session_id}_writes")
        has_writes = os.path.exists(writes_flag)

        state = {
            "session_id": session_id,
            "status": "waiting",
            "has_writes": has_writes,
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
