#!/usr/bin/env python3
"""Stop hook — marks session as waiting for input.

Also fires a macOS notification ("Claude is waiting for you") via the
shared _notify helper so the user sees a banner even when CCC isn't
focused. Opt-out: CCC_NOTIFY=0 in the env.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _notify import notify
except ImportError:
    def notify(*_a, **_k):
        pass

LIVE_STATE_DIR = os.path.expanduser("~/.claude/command-center/live-state")


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

        # Subtitle = short session id so the user can match the banner to a
        # card in the kanban without us having to open the JSONL to fetch
        # the prompt. Trade-off: less context, but stays fast.
        notify(
            title="Claude Command Center",
            message="Ready for your input",
            subtitle=session_id[:8],
        )

    except Exception:
        pass


if __name__ == "__main__":
    main()
