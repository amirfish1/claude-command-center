"""Read-only, bounded annotations from an optional TokenEconomizer install.

Read its session pin rather than scanning repositories or starting its CLI.
Absence, partial writes and future schema changes must not break conversation opens.
"""
import json
import os
from pathlib import Path
import re


def _read(root, relative, limit=262144, tail=False):
    try:
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            return ""
        with target.open("rb") as stream:
            size = stream.seek(0, 2)
            if size > limit and not tail:
                return ""
            stream.seek(max(0, size - limit) if tail else 0)
            data = stream.read(limit).decode("utf-8", errors="replace")
            return data.split("\n", 1)[-1] if tail and size > limit else data
    except (OSError, RuntimeError):
        return ""


def _object(text):
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError, RecursionError):
        return {}


def session_annotation(session_id, state_root=None):
    """Return only recorded activity, never infer savings from installation."""
    session_id = str(session_id or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", session_id):
        return {"observed": False, "actions": []}
    root = Path(state_root or os.environ.get("TOKENECONOMIZER_HOME")
                or Path.home() / ".tokeneconomizer").resolve()
    reports = []
    for host in ("claude", "codex"):
        pin = _object(_read(root, Path("sessions") / host / (session_id + ".json")))
        key = pin.get("key", "")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", key):
            continue
        base = Path("repos") / key / host
        state = _object(_read(root, base / "sessions" / (session_id + ".json")))
        if state.get("session_id") != session_id:
            continue
        actions = set()
        labels = {"inject": "learned facts injected", "repomap": "repository map injected",
                  "nudge": "loop redirected", "abort": "still-stuck warning"}
        # A bounded tail avoids repeatedly reading an entire repository's history.
        # Report presence, not counts: older actions may have left this window.
        for line in _read(root, base / "events.jsonl", tail=True).splitlines():
            event = _object(line)
            if event.get("session_id") == session_id and event.get("shadow") is False:
                label = labels.get(event.get("kind"))
                if label:
                    actions.add(label)
        # Facts/maps are also retained in live session state after log rotation.
        if isinstance(state.get("injected_chars"), (int, float)) and state["injected_chars"] > 0:
            actions.add("learned facts injected")
        if isinstance(state.get("repomap_chars"), (int, float)) and state["repomap_chars"] > 0:
            actions.add("repository map injected")
        for line in _read(root, base / "ledger.jsonl", tail=True).splitlines():
            entry = _object(line)
            if entry.get("session_id") == session_id and entry.get("shadow") is False:
                if isinstance(entry.get("untool_applied"), list) and entry["untool_applied"]:
                    actions.add("tools pruned")
        reports.append({"host": host, "actions": sorted(actions)})
    return {"observed": bool(reports),
            "actions": sorted({action for report in reports for action in report["actions"]})}
