"""Tiny stdlib helper for firing macOS notifications from CCC hooks.

Used by stop.py (Claude finished a turn — needs input) and notification.py
(Claude is asking for permission). Stays a separate file so the hooks
themselves remain trivial.

Disabled gracefully when:
- CCC_NOTIFY=0 in the environment (opt-out)
- osascript isn't on PATH (non-macOS)
- the user's display is locked / the call fails — Popen is fire-and-forget
  so a hook never blocks on notification delivery
"""

import os
import shutil
import subprocess


def _enabled():
    return os.environ.get("CCC_NOTIFY", "1") != "0"


def _esc(s):
    """Escape characters that would break out of an AppleScript string
    literal. Keep it simple: backslash, double-quote, and trim length so
    a 5KB tool error doesn't end up in the notification banner."""
    s = s or ""
    return s.replace("\\", "\\\\").replace('"', '\\"')[:240]


def notify(title, message, subtitle=""):
    if not _enabled():
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
