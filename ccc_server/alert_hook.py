# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Outbound alert hook: reach the owner off-dashboard when something died.

Dashboard toasts only help while a dashboard is open. For the few events that
mean "a session stopped and nothing will restart it" (a dead session, an
automatic wake refused by the wake guard) CCC can also run a command of your
choosing with the message on stdin, e.g. a chat/SMS CLI:

    CCC_ALERT_COMMAND='my-notifier --to me -f -'

Off unless CCC_ALERT_COMMAND is set. The command is split with shlex and run
without a shell. Guards:
  * one alert per incident key, ever (keys kept KEY_KEEP_S)
  * at most RATE_MAX alerts per hour; the rest are counted and the next
    alert that goes out says how many were held back
  * runs on a daemon thread with a timeout, so a slow/hung notifier never
    blocks the caller
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import threading
import time

try:  # POSIX only; best-effort lock elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

COMMAND_ENV = "CCC_ALERT_COMMAND"
RATE_MAX = 10
RATE_WINDOW_S = 3600.0
KEY_KEEP_S = 7 * 86400.0
TIMEOUT_S = 60

SENT = "sent"
OFF = "off"
DUPLICATE = "duplicate"
RATE_LIMITED = "rate_limited"


def command():
    raw = os.environ.get(COMMAND_ENV, "").strip()
    try:
        return shlex.split(raw) if raw else []
    except ValueError:
        return []


def _default_path():
    base = os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )
    return os.path.join(base, "alert-hook.json")


def _mutate(path, fn):
    path = path or _default_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = open(path + ".lock", "a+")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError):
            state = {}
        result = fn(state)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        return result
    finally:
        lock.close()


def admit(key, now=None, path=None):
    """Reserve a send slot for `key`. Returns (outcome, held_back_count)."""
    now = time.time() if now is None else float(now)
    key = str(key or "")

    def fn(state):
        keys = {k: t for k, t in (state.get("keys") or {}).items() if now - float(t) < KEY_KEEP_S}
        sent = [t for t in state.get("sent") or [] if now - float(t) < RATE_WINDOW_S]
        state["keys"], state["sent"] = keys, sent
        if key in keys:
            return DUPLICATE, 0
        keys[key] = now  # a held-back incident is still "handled": never re-sent later
        if len(sent) >= RATE_MAX:
            state["held_back"] = int(state.get("held_back") or 0) + 1
            return RATE_LIMITED, 0
        sent.append(now)
        held, state["held_back"] = int(state.get("held_back") or 0), 0
        return SENT, held

    return _mutate(path, fn)


def format_message(title, body="", url="", held_back=0):
    lines = [str(title or "").strip()]
    if body:
        lines.append(str(body).strip())
    if url:
        lines.append(str(url).strip())
    if held_back:
        lines.append(f"(+{held_back} more alert{'s' if held_back != 1 else ''} held back by the hourly cap; see the dashboard)")
    return "\n".join(l for l in lines if l)


def _run(cmd, text, log=None):
    try:
        r = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=TIMEOUT_S)
        outcome = f"rc={r.returncode} {(r.stdout or r.stderr or '').strip()[:200]}"
    except Exception as exc:  # noqa: BLE001
        outcome = f"error {exc}"[:220]
    if log:
        try:
            log(outcome)
        except Exception:
            pass
    return outcome


def send(key, title, body="", url="", *, now=None, path=None, log=None, wait=False):
    """Alert once for incident `key`. Returns the admit outcome; the command
    itself runs on a daemon thread unless `wait` (tests)."""
    cmd = command()
    if not cmd:
        return OFF
    outcome, held = admit(key, now=now, path=path)
    if outcome != SENT:
        return outcome
    text = format_message(title, body, url, held)
    if wait:
        _run(cmd, text, log)
    else:
        threading.Thread(target=_run, args=(cmd, text, log), daemon=True, name="ccc-alert-hook").start()
    return SENT


def dashboard_link(rel_url):
    """Absolute link for a phone: CCC_PUBLIC_URL (or the allowed origin) + rel."""
    base = (os.environ.get("CCC_PUBLIC_URL") or os.environ.get("CCC_ALLOWED_ORIGIN") or "").strip().rstrip("/")
    rel = str(rel_url or "")
    if not base or not rel.startswith("/"):
        return rel
    return base.split(",")[0].strip().rstrip("/") + rel
