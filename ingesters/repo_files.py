"""Scan TODO.md and PARKING_LOT.md files in watched repos.

Pure stdlib. Called from morning.py's compose layer.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path


_TODO_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+?)\s*$")
_HEADING_RE = re.compile(r"^#+\s+(.+?)\s*$")
_PARK_TITLE_RE = re.compile(r"^## (.+?)\s*$")
_PARK_DATE_RE = re.compile(r"^\*\*Parked:\*\*\s*(\d{4}-\d{2}-\d{2})")
_PARK_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$")


def _days_since_mtime(mtime):
    return max(0, int((time.time() - mtime) / 86400))


def _days_since_iso(iso_date):
    try:
        d = datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - d).days)
    except (ValueError, TypeError):
        return 30


def scan_todo_md(repo_path, repo_label=None):
    """Return tactical items from `TODO.md`. Checked boxes excluded."""
    repo_path = Path(repo_path)
    todo = repo_path / "TODO.md"
    if not todo.is_file():
        return []
    label = repo_label or repo_path.name
    try:
        text = todo.read_text()
    except OSError:
        return []
    mtime_days = _days_since_mtime(todo.stat().st_mtime)
    items = []
    current_section = None
    for line in text.splitlines():
        h = _HEADING_RE.match(line)
        if h:
            current_section = h.group(1).strip()
            continue
        m = _TODO_CHECKBOX_RE.match(line)
        if not m:
            continue
        if m.group(1) in ("x", "X"):
            continue
        items.append({
            "priority": "P1",
            "text": m.group(2).strip(),
            "source": f"{label}/TODO.md",
            "section": current_section,
            "age_days": mtime_days,
            "goal_slug": None,
        })
    return items


def scan_parking_lot(repo_path, repo_label=None):
    """Return tactical items from `PARKING_LOT.md`, one per `## Title` section.

    Entries marked with a status containing "Shipped" or "Done" are skipped.
    """
    repo_path = Path(repo_path)
    parking = repo_path / "PARKING_LOT.md"
    if not parking.is_file():
        return []
    label = repo_label or repo_path.name
    try:
        text = parking.read_text()
    except OSError:
        return []

    items = []
    current_title = None
    current_parked = None
    current_status = None

    def _flush():
        if current_title is None:
            return
        # Filter out anything already resolved.
        if current_status and any(
            tok in current_status.lower()
            for tok in ("shipped", "done", "complete", "resolved", "landed")
        ):
            return
        items.append({
            "priority": "P2",
            "text": current_title,
            "source": f"{label}/PARKING_LOT.md",
            "parked": current_parked,
            "age_days": _days_since_iso(current_parked) if current_parked else 30,
            "goal_slug": None,
        })

    for line in text.splitlines():
        t = _PARK_TITLE_RE.match(line)
        if t:
            _flush()
            current_title = t.group(1).strip()
            current_parked = None
            current_status = None
            continue
        d = _PARK_DATE_RE.match(line)
        if d and current_title:
            current_parked = d.group(1)
            continue
        s = _PARK_STATUS_RE.match(line)
        if s and current_title:
            current_status = s.group(1)
            continue
    _flush()
    return items
