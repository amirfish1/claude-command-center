#!/usr/bin/env python3
"""PreCompact hook — marks a session as mid-/compact so the dashboard can
show a "Compacting…" badge instead of a stale/misleading tool-activity pill.

Pairs with post-compact.py, which clears the marker once compaction ends.
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
        if not session_id:
            return

        os.makedirs(LIVE_STATE_DIR, exist_ok=True)

        marker = {
            "session_id": session_id,
            "status": "compacting",
            "trigger": data.get("trigger", ""),
            "started_at": time.time(),
        }

        path = os.path.join(LIVE_STATE_DIR, f"{session_id}_compacting.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(marker, f)
        os.replace(tmp, path)

    except Exception:
        pass


if __name__ == "__main__":
    main()
