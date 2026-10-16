"""Tiny stdlib helper for firing macOS notifications from CCC hooks.

Used by stop.py (Claude finished a turn — needs input) and notification.py
(Claude is asking for permission). Stays a separate file so the hooks
themselves remain trivial.

Disabled gracefully when:
- CCC_NOTIFY=0 in the environment (opt-out)
- osascript isn't on PATH (non-macOS)
- the user's display is locked / the call fails — Popen is fire-and-forget
  so a hook never blocks on notification delivery

The Stop hook fires on every turn a session takes, not just when it goes
truly idle — a session doing many quick turns in a row (e.g. an automated
worker draining a queue) would otherwise bang out a banner+sound per turn.
notify() is debounced per (session, title) pair via CCC_NOTIFY_COOLDOWN_S
(default 30s) so bursts collapse to one sound.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.request

LIVE_STATE_DIR = os.path.expanduser("~/.claude/command-center/live-state")


def _enabled():
    return os.environ.get("CCC_NOTIFY", "1") != "0"


def _cooldown_s():
    try:
        return float(os.environ.get("CCC_NOTIFY_COOLDOWN_S", "30"))
    except ValueError:
        return 30.0


def _debounced(session_id, title):
    """True if a notification with this (session, title) fired too recently."""
    cooldown = _cooldown_s()
    if cooldown <= 0 or not session_id:
        return False
    try:
        os.makedirs(LIVE_STATE_DIR, exist_ok=True)
        key = "".join(c if c.isalnum() else "_" for c in f"{session_id}_{title}")
        marker = os.path.join(LIVE_STATE_DIR, f"_notify_{key}.ts")
        now = time.time()
        if os.path.exists(marker) and (now - os.path.getmtime(marker)) < cooldown:
            return True
        with open(marker, "w") as f:
            f.write(str(now))
    except OSError:
        return False
    return False


_TEMP_CWD_PREFIXES = ("/private/var/folders/", "/var/folders/", "/tmp/", "/private/tmp/")


def is_throwaway_cwd(cwd):
    """True for sessions started in an OS temp dir. Eval harnesses and other
    scripts run hundreds of headless `claude -p` runs there; none wait on a
    human, so a banner per run is noise (one flood hit ~100 banners/minute)."""
    cwd = (cwd or "").rstrip("/") + "/"
    return cwd.startswith(_TEMP_CWD_PREFIXES)


def _esc(s):
    """Escape characters that would break out of an AppleScript string
    literal. Keep it simple: backslash, double-quote, and trim length so
    a 5KB tool error doesn't end up in the notification banner."""
    s = s or ""
    return s.replace("\\", "\\\\").replace('"', '\\"')[:240]


def notify(title, message, subtitle="", session_id=""):
    if not _enabled():
        return
    if _debounced(session_id or subtitle, title):
        return
    osascript = shutil.which("osascript")
    if not osascript:
        return
    script_parts = [f'display notification "{_esc(message)}"',
                    f'with title "{_esc(title)}"']
    if subtitle:
        script_parts.append(f'subtitle "{_esc(subtitle)}"')
    script = " ".join(script_parts)
    try:
        subprocess.Popen(
            [osascript, "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


PORT_FILE = os.path.expanduser("~/.claude/command-center/port.txt")


def notify_session(kind, title, message, session_id):
    """Route a session banner through the CCC server (POST /api/notify).

    The server shows it in the open dashboard/app, where clicking it lands on
    the session (/?session=<id>), and falls back to an osascript banner only
    when no dashboard is listening. Server down or unreachable: the old direct
    osascript banner, so a turn end is never silent."""
    if not _enabled() or not session_id:
        return
    try:
        with open(PORT_FILE) as f:
            base = f.read().strip()
        if not base:
            raise OSError("no port")
        req = urllib.request.Request(
            f"{base}/api/notify",
            data=json.dumps({
                "kind": kind,
                "title": title,
                "body": message,
                "url": f"/?session={session_id}",
                "session_id": session_id,
                "os": "auto",
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.5).read()
    except Exception:
        notify("Claude Command Center", message, subtitle=title, session_id=session_id)
