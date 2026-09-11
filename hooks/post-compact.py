#!/usr/bin/env python3
"""PostCompact hook — clears the compacting marker pre-compact.py wrote."""

import json
import os
import sys

LIVE_STATE_DIR = os.path.expanduser("~/.claude/command-center/live-state")


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)

        session_id = data.get("session_id", "")
        if not session_id:
            return

        path = os.path.join(LIVE_STATE_DIR, f"{session_id}_compacting.json")
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    except Exception:
        pass


if __name__ == "__main__":
    main()
