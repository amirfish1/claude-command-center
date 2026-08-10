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
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _notify import notify
except ImportError:
    def notify(*_a, **_k):
        pass

LIVE_STATE_DIR = os.path.expanduser("~/.claude/command-center/live-state")
PORT_FILE = os.path.expanduser("~/.claude/command-center/port.txt")


def request_auto_title(session_id):
    """Ask CCC to AI-title this session if it still has no title of its own.

    Claude Code only titles sessions from the interactive TUI, so anything CCC
    spawns (stream-json entrypoint) never gets one and the sidebar falls back to
    the first sentence of the prompt. The server does every check and the actual
    summarization on a background thread; this call just pokes it and must stay
    fast — the hook blocks the end of the turn.

    Opt out with CCC_AUTO_TITLE=0 (read server-side, so one place governs both).
    """
    try:
        with open(PORT_FILE) as f:
            base = f.read().strip()
        if not base:
            return
        req = urllib.request.Request(
            f"{base}/api/conversations/{session_id}/auto-title",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.5).read()
    except Exception:
        pass  # server down, no port file, anything — titling is best-effort


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

        # Clear any stale needs-approval marker left over from the just-ended
        # turn. Extended thinking (and other non-tool Notification events) write
        # this marker but are never cleared by post-tool-use.py. Without this,
        # the next inject sees _notification_blocks_inject → True and queues
        # the message even though the session is now idle.
        needs_approval_path = os.path.join(LIVE_STATE_DIR, f"{session_id}_needs_approval.json")
        try:
            os.unlink(needs_approval_path)
        except FileNotFoundError:
            pass

        # Subtitle = short session id so the user can match the banner to a
        # card in the kanban without us having to open the JSONL to fetch
        # the prompt. Trade-off: less context, but stays fast.
        notify(
            title="Claude Command Center",
            message="Ready for your input",
            subtitle=session_id[:8],
        )

        request_auto_title(session_id)

    except Exception:
        pass


if __name__ == "__main__":
    main()
