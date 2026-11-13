# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Claude Code transcript retention (`cleanupPeriodDays`).

Claude Code deletes transcripts under ~/.claude/projects that are older than
`cleanupPeriodDays` (default 30) from its settings.json. Everything CCC builds
on those transcripts -- History search, the session index, is_shipped -- then
silently loses anything older than a month.

This module reads the current value and, only on explicit user consent,
raises it. It never lowers an existing value, and it treats an unreadable or
malformed settings file as "don't touch, report".

Codex has no equivalent: its `[history]` config (`persistence`, `max_bytes`)
only governs the prompt-history file ~/.codex/history.jsonl, and session
rollouts under ~/.codex/sessions are never pruned by age.

Stdlib-only, no imports from server.py, so it is unit-testable in isolation.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time

CLAUDE_DEFAULT_RETENTION_DAYS = 30
SUGGESTED_RETENTION_DAYS = 3650
_KEY = "cleanupPeriodDays"


def claude_settings_path() -> str:
    """User-level Claude Code settings.json (honours CLAUDE_CONFIG_DIR)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    return os.path.join(base, "settings.json")


def _coerce_days(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _load(path: str):
    """Return (data, error). A missing file is ({}, None)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        return None, f"unreadable: {e}"
    if not raw.strip():
        return {}, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"malformed JSON: {e}"
    if not isinstance(data, dict):
        return None, "settings.json is not a JSON object"
    return data, None


def read_claude_retention(settings_path: str | None = None) -> dict:
    """Current retention as seen by Claude Code.

    `effective_days` is what Claude Code will actually apply: the configured
    value, or the 30-day default when the key is absent. It is None when the
    file cannot be parsed (we cannot tell what Claude Code will do).
    """
    path = settings_path or claude_settings_path()
    data, err = _load(path)
    if err:
        return {"path": path, "configured_days": None, "effective_days": None,
                "default_days": CLAUDE_DEFAULT_RETENTION_DAYS, "error": err}
    configured = _coerce_days(data.get(_KEY)) if _KEY in data else None
    effective = configured if configured is not None else CLAUDE_DEFAULT_RETENTION_DAYS
    return {"path": path, "configured_days": configured,
            "effective_days": effective,
            "default_days": CLAUDE_DEFAULT_RETENTION_DAYS, "error": None}


def ensure_claude_retention(days: int = SUGGESTED_RETENTION_DAYS,
                            settings_path: str | None = None) -> dict:
    """Raise `cleanupPeriodDays` to at least `days`. Call only on consent.

    Never lowers an existing value. Writes the real file behind a symlink
    (the link itself is left in place), keeps a timestamped backup beside it,
    and replaces atomically via temp file + rename. Only the one key changes;
    every other key is preserved.
    """
    days = _coerce_days(days)
    if days is None or days <= 0:
        return {"ok": False, "changed": False, "error": "invalid days"}
    link_path = settings_path or claude_settings_path()
    path = os.path.realpath(link_path)
    data, err = _load(path)
    if err:
        return {"ok": False, "changed": False, "path": path, "error": err}
    previous = _coerce_days(data.get(_KEY)) if _KEY in data else None
    if previous is not None and previous >= days:
        return {"ok": True, "changed": False, "path": path,
                "previous_days": previous, "current_days": previous,
                "backup": None, "error": None}

    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        return {"ok": False, "changed": False, "path": path, "error": str(e)}

    backup = None
    mode = 0o600
    if os.path.exists(path):
        try:
            mode = os.stat(path).st_mode & 0o777
            backup = f"{path}.ccc-backup-{time.strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(path, backup)
        except OSError as e:
            return {"ok": False, "changed": False, "path": path,
                    "error": f"backup failed: {e}"}

    data[_KEY] = days
    fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"ok": False, "changed": False, "path": path, "error": str(e)}
    return {"ok": True, "changed": True, "path": path,
            "previous_days": previous, "current_days": days,
            "backup": backup, "error": None}
