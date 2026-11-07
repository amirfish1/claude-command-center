# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""One reminder the user asked for: tomorrow morning, say what their agents did.

After the first day nothing brings a person back to the board: it sits on a
localhost URL they have no reason to reopen. This is the smallest honest answer.
The user opts in (`ccc remind`, or the button at the end of the first task), and
the next morning they get one local notification with a true line about their
own sessions. Then it is gone. It never re-arms itself, so it is a reminder they
asked for and not a pop-up (ccc_server/popups.py covers the unrequested kind).

State is one small JSON file that exists only while a reminder is armed, so the
once-a-minute tick costs one stat call. The transcript scan runs once, when the
reminder fires. Nothing leaves this machine.

Stdlib-only; no side effects at import.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from ccc_server import notify as _notify
from ccc_server.paths import COMMAND_CENTER_STATE_DIR

STATE_PATH = Path(COMMAND_CENTER_STATE_DIR) / "return-reminder.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"

DUE_HOUR = 9
# A reminder held up by a powered-off machine would describe an old day.
STALE_AFTER_S = 3 * 86400
TAIL_BYTES = 65536
MAX_TAILS = 200


def _read():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("due_at") else None


def _due_at(now):
    """09:00 local on the calendar day after `now`."""
    tomorrow = datetime.fromtimestamp(now).date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, DUE_HOUR).timestamp()


def can_notify():
    """Whether this machine has any way to show a desktop notification."""
    if sys.platform == "darwin":
        return True
    if not shutil.which("notify-send"):
        return False
    runtime = os.environ.get("XDG_RUNTIME_DIR") or ""
    return bool(
        os.environ.get("DISPLAY")
        or os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        or (runtime and os.path.exists(os.path.join(runtime, "bus")))
    )


def runs_in_background():
    """True when a service manager keeps this server alive past the terminal."""
    if "claude-command-center" in (os.environ.get("XPC_SERVICE_NAME") or ""):
        return True
    try:
        with open("/proc/self/cgroup", "r", encoding="utf-8") as f:
            return "/ccc.service" in f.read()
    except OSError:
        return False


def status(now=None):
    state = _read()
    return {
        "ok": True,
        "armed": bool(state),
        "due_at": state.get("due_at") if state else None,
        "can_notify": can_notify(),
        "background": runs_in_background(),
    }


def arm(now=None):
    now = time.time() if now is None else float(now)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _notify._write_json_atomic(STATE_PATH, {"armed_at": now, "due_at": _due_at(now)})
    return status(now)


def cancel():
    try:
        STATE_PATH.unlink()
    except OSError:
        pass
    return status()


def _last_turn_asks(path):
    """True when the transcript's last turn is the agent asking something."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read()
    except OSError:
        return False
    for raw in reversed(tail.splitlines()):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") not in ("user", "assistant"):
            continue
        if row.get("type") == "user":
            return False
        content = (row.get("message") or {}).get("content")
        if isinstance(content, str):
            return content.rstrip().endswith("?")
        if not isinstance(content, list) or not content:
            return False
        last = content[-1]
        if not isinstance(last, dict) or last.get("type") != "text":
            return False
        return str(last.get("text") or "").rstrip().endswith("?")
    return False


def summarize(since, projects_dir=None):
    """Sessions touched since `since`: how many, in how many projects, and how
    many ended with the agent asking the user something."""
    root = Path(projects_dir or PROJECTS_DIR)
    touched = []
    try:
        projects = list(os.scandir(root))
    except OSError:
        projects = []
    for project in projects:
        if not project.is_dir():
            continue
        try:
            entries = list(os.scandir(project.path))
        except OSError:
            continue
        for entry in entries:
            if not entry.name.endswith(".jsonl"):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime >= since:
                touched.append((mtime, project.name, Path(entry.path)))
    touched.sort(key=lambda row: row[0], reverse=True)
    return {
        "sessions": len(touched),
        "projects": len({name for _, name, _ in touched}),
        "questions": sum(1 for _, _, p in touched[:MAX_TAILS] if _last_turn_asks(p)),
    }


def _board_url(base_url):
    port = urlsplit(base_url or "").port
    return f"http://localhost:{port or 8090}"


def compose(summary, base_url=None):
    url = _board_url(base_url)
    sessions = summary.get("sessions") or 0
    if not sessions:
        return f"No Claude sessions since yesterday. Your board: {url}"
    projects = summary.get("projects") or 0
    questions = summary.get("questions") or 0
    body = (f"Since yesterday: {sessions} Claude session" + ("" if sessions == 1 else "s")
            + f" in {projects} project" + ("" if projects == 1 else "s") + ".")
    if questions:
        body += f" {questions} ended on a question for you."
    return f"{body} Your board: {url}"


def _desktop_notify(title, body):
    if sys.platform == "darwin":
        return _notify._osascript_notify(title, body)
    binary = shutil.which("notify-send")
    if not binary:
        return False
    try:
        return subprocess.run(
            [binary, "--app-name=Command Center", title, body],
            timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def tick(now=None, base_url=None, publish=None, projects_dir=None):
    """Fire the armed reminder once it is due. Returns the item, or None."""
    if not STATE_PATH.exists():
        return None
    now = time.time() if now is None else float(now)
    state = _read()
    if state is None:
        cancel()
        return None
    due_at = float(state.get("due_at") or 0)
    if now < due_at:
        return None
    # Disarm first: a crash below must not turn one reminder into many.
    cancel()
    if now - due_at > STALE_AFTER_S:
        return None
    summary = summarize(float(state.get("armed_at") or due_at - 86400), projects_dir)
    item = {
        "id": "ntf_" + uuid.uuid4().hex[:16],
        "kind": "info",
        "title": "Your reminder from Command Center",
        "body": compose(summary, base_url),
        "url": "/",
        "session_id": "",
        "ts": now,
    }
    # Asked for by the user, so it does not go through the pop-up gate.
    _notify._append_log(item)
    (publish or _notify._publish)(item)
    item["delivered"] = _desktop_notify(item["title"], item["body"])
    return item
