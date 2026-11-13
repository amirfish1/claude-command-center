#!/usr/bin/env python3
"""Tracker reminder: warn before a date in items.json arrives.

items.json: [{"title", "date" (YYYY-MM-DD), "note"?, "warn_days"?, "done"?}]
Prints one JSON line for the routine card. "alert" turns the card red and
sends a notification.
"""
import json
from datetime import date
from pathlib import Path

DEFAULT_WARN_DAYS = 3

try:
    items = json.loads((Path(__file__).parent / "items.json").read_text() or "[]")
except (OSError, ValueError):
    items = []
today = date.today()
due, upcoming = [], []
for it in items if isinstance(items, list) else []:
    try:
        d = date.fromisoformat(str(it["date"]))
        title = str(it["title"])
    except (KeyError, ValueError, TypeError):
        continue
    left = (d - today).days
    if left < 0 or it.get("done"):
        continue
    warn = it.get("warn_days", DEFAULT_WARN_DAYS)
    (due if left <= warn else upcoming).append((left, title, d))

when = lambda n: "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"
if due:
    due.sort()
    lines = [f"{t}: {when(n)} ({d:%a %b} {d.day})" for n, t, d in due]
    out = {"summary": "; ".join(lines), "alert": True,
           "notify": {"title": "Coming up: " + due[0][1], "body": "; ".join(lines)}}
elif upcoming:
    upcoming.sort()
    n, t, d = upcoming[0]
    out = {"summary": f"Nothing due soon. Next: {t}, {when(n)}."}
else:
    out = {"summary": "No upcoming dates yet."}
print(json.dumps(out))
