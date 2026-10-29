#!/usr/bin/env python3
"""Stop hook — marks session as waiting for input.

Also fires a macOS notification with the turn's outcome via the
shared _notify helper so the user sees a banner even when CCC isn't
focused. Opt-out: CCC_NOTIFY=0 in the env.
"""

import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _notify import notify_session, is_throwaway_cwd
except ImportError:
    def notify_session(*_a, **_k):
        pass

    def is_throwaway_cwd(_cwd):
        return False
try:
    import _asks
except ImportError:
    _asks = None

LIVE_STATE_DIR = os.path.expanduser("~/.claude/command-center/live-state")
STATE_DIR = os.path.expanduser("~/.claude/command-center")
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


def _sidecar_title(session_id):
    """Names CCC itself holds, in the same priority the sidebar uses: a user
    rename, then the auto-title. Both are tiny files; no transcript parse."""
    try:
        with open(os.path.join(STATE_DIR, "session-names.json")) as f:
            name = (json.load(f) or {}).get(session_id)
        if isinstance(name, str) and name.strip():
            return name.strip()
    except Exception:
        pass
    try:
        with open(os.path.join(LIVE_STATE_DIR, f"{session_id}_autotitled")) as f:
            title = (json.load(f) or {}).get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    except Exception:
        pass
    return ""


def session_title(session_id, transcript_path):
    """Current session name for the banner subtitle. Prefers CCC's own names
    (rename, auto-title) because the transcript's custom-title is often just
    the launch slug (e.g. "prewarm-<repo>") that CCC re-appends every turn.
    Falls back to the transcript's /rename title, then Claude's ai-title.
    Reads only the transcript tail (hook must stay fast); "" when none."""
    name = _sidecar_title(session_id)
    if name:
        return name
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            f.seek(max(0, size - 1_000_000))
            tail = f.read().decode("utf-8", "ignore")
    except Exception:
        return ""
    custom = ai = agent = ""
    for line in tail.splitlines():
        if '"customTitle"' in line:
            try:
                custom = json.loads(line).get("customTitle") or custom
            except Exception:
                pass
        elif '"aiTitle"' in line:
            try:
                ai = json.loads(line).get("aiTitle") or ai
            except Exception:
                pass
        elif '"agentName"' in line:
            try:
                agent = json.loads(line).get("agentName") or agent
            except Exception:
                pass
    if custom and (custom == agent or custom.startswith("prewarm-")):
        custom = ""  # launch slug, not a real name
    return custom or ai


def first_prompt_snippet(transcript_path, limit=60):
    """Banner subtitle of last resort: the opening of the session's first
    prompt, so a not-yet-titled session never shows as a bare hex id. Reads
    only the transcript head (hook must stay fast); "" when none."""
    try:
        with open(transcript_path, "rb") as f:
            head = f.read(200_000).decode("utf-8", "ignore")
    except Exception:
        return ""
    for line in head.splitlines():
        if '"type":"user"' not in line and '"enqueue"' not in line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        text = obj.get("content") if obj.get("type") == "queue-operation" else (obj.get("message") or {}).get("content")
        if isinstance(text, list):
            text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
        if isinstance(text, str) and text.strip():
            text = " ".join(text.split())
            return text if len(text) <= limit else text[:limit].rstrip() + "…"
    return ""


def turn_notification_body(data, limit=240):
    """Use the final response's outcome, without another model call.

    Newer agents supply last_assistant_message directly. Older versions need
    a bounded transcript tail read; never use tool output or reasoning, and
    never cross a user message into a previous turn.
    """
    text = data.get("last_assistant_message")
    if not isinstance(text, str) or not text.strip():
        text = ""
        try:
            with open(data.get("transcript_path") or "", "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 1_000_000))
                tail = f.read().decode("utf-8", "ignore")
            for line in reversed(tail.splitlines()):
                try:
                    entry = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "user":
                    break
                if entry.get("type") != "assistant":
                    continue
                content = (entry.get("message") or {}).get("content")
                if isinstance(content, list):
                    content = "\n".join(
                        b["text"] for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                        and isinstance(b.get("text"), str))
                if isinstance(content, str) and content.strip():
                    text = content
                    break
        except (OSError, ValueError, TypeError):
            pass

    states = re.findall(r"<session-state>\s*(.*?)</session-state>", text,
                        flags=re.DOTALL | re.IGNORECASE)
    if states:
        fields = dict(re.findall(r"^(DID|NEXT_STEP_USER):[ \t]*(.*)$", states[-1],
                                 flags=re.MULTILINE))
        outcome = fields.get("DID", "").strip()
        action = fields.get("NEXT_STEP_USER", "").strip()
        if action and action.lower().rstrip(".") != "none":
            # Prioritize the required action over the outcome in a short banner.
            budget = min(100, limit - len(" Needs you: ") - len(action))
            if budget < 30:
                outcome = ""
            elif len(outcome) > budget:
                outcome = outcome[:budget - 1].rstrip() + "…"
            text = (outcome + " Needs you: " + action).strip()
        else:
            text = outcome
    else:
        # An incomplete footer is metadata too, never notification copy.
        text = re.split(r"<session-state>", text, flags=re.IGNORECASE)[0]

    text = re.sub(r"\[([^\]]+)\]\([^\n)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}(?:#{1,6}\s+|[-*]\s+)", "", text)
    text = re.sub(r"[`*_]", "", text)
    text = " ".join(text.split())
    if not text:
        return "Turn finished — open the session for details."
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


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
        if _asks is not None:
            _asks.clear(LIVE_STATE_DIR, session_id)

        # Subtitle = session name (falls back to the short id) so the user can
        # match the banner to a card in the kanban.
        # CCC's own helper runs (auto-titler etc.) live in the scratch dir and
        # never wait on a human, so a banner for them is pure noise.
        if ("command-center/scratch" not in (data.get("cwd") or "")
                and not is_throwaway_cwd(data.get("cwd"))):
            notify_session(
                "needs_input",
                (session_title(session_id, data.get("transcript_path") or "")
                 or first_prompt_snippet(data.get("transcript_path") or "")
                 or session_id[:8]),
                turn_notification_body(data),
                session_id,
            )

        request_auto_title(session_id)

    except Exception:
        pass


if __name__ == "__main__":
    main()
