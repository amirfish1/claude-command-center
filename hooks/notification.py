#!/usr/bin/env python3
"""Notification hook — writes a needs-approval marker when Claude Code is
asking the user for permission (or otherwise needs attention).

The previous Needs-Approval signal in the dashboard was heuristic: a
combination of `pending_tool` + assistant-as-last-event + age threshold,
which routinely confused "tool fired but not yet returned" with "Claude
is blocked on a permission prompt". Claude Code emits a distinct
`Notification` hook event when it specifically wants the user to look,
so wiring that up gives a precise, signal-driven badge instead.

Pairs with post-tool-use.py, which clears the marker once a tool
returns (the user has either approved or the prompt is no longer
relevant). Stdlib only, fails silently like the other hooks.
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

        message = data.get("message", "")
        marker = {
            "session_id": session_id,
            "message": message,
            "type": data.get("type", ""),
            "started_at": time.time(),
        }

        path = os.path.join(LIVE_STATE_DIR, f"{session_id}_needs_approval.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(marker, f)
        os.replace(tmp, path)

        # macOS banner so the user knows Claude is blocked on a permission
        # prompt even when CCC isn't focused. Falls through silently on
        # non-macOS systems or when CCC_NOTIFY=0.
        notify(
            title="Claude needs your approval",
            message=message or "Permission requested",
            subtitle=session_id[:8],
        )

    except Exception:
        pass


if __name__ == "__main__":
    main()
