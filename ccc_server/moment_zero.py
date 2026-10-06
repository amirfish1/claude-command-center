# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Fresh-install detection for the Moment Zero onboarding shell.

The onboarding overlay (static/onboarding/onboarding.js) auto-opens only for
people who have never run a coding agent on this machine. This module answers
that one question — "is there any agent session history here?" — by looking
for transcript files, not by parsing them, so one early-exit hit settles it.

Perf: history is sticky, so a positive verdict is cached forever. While the
verdict is still "fresh" the scan is skipped whenever a signature of the
watched roots (dir mtimes, including each project subdir so a transcript
appearing inside an existing project dir is caught) is unchanged. The walk
itself is bounded by _MAX_PROJECT_DIRS — 500+ project folders is itself proof
of prior agent use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Beyond this many project subdirs the machine has plainly seen agent use
# before, so the scan can stop early and report "has history".
_MAX_PROJECT_DIRS = 500
# Breadth-first budget for smaller trees (codex sessions are YYYY/MM/DD deep).
_MAX_TREE_ENTRIES = 400

_cache = {"home": None, "sig": None, "result": None}


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return -1


def _signature(projects_dir, codex_sessions, marker):
    """Cheap change detector for the watched roots: directory mtimes.

    Directory mtime bumps when an entry is created or removed inside it, so a
    first transcript landing in a new *or existing* project folder is seen —
    ~/.claude/projects/<slug>/ is included per subdir for exactly that reason.
    """
    sig = [_mtime(projects_dir), _mtime(codex_sessions), _mtime(marker)]
    try:
        with os.scandir(projects_dir) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        sig.append(_mtime(entry.path))
                        if len(sig) > _MAX_PROJECT_DIRS + 3:
                            break
                except OSError:
                    continue
    except OSError:
        pass
    return tuple(sig)


def _claude_projects_have_transcripts(projects_dir, counter):
    """True when any ~/.claude/projects/<slug>/ holds a .jsonl transcript.

    `counter` is a one-element list so callers can assert the walk stays
    bounded; every scandir entry inspected increments it.
    """
    try:
        entries = list(os.scandir(projects_dir))
    except OSError:
        return False
    dirs = 0
    for entry in entries:
        counter[0] += 1
        try:
            if entry.is_file(follow_symlinks=False) and entry.name.endswith(".jsonl"):
                return True
            if not entry.is_dir(follow_symlinks=True):
                continue
        except OSError:
            continue
        dirs += 1
        if dirs > _MAX_PROJECT_DIRS:
            # That many project folders is itself evidence of heavy prior use.
            return True
        try:
            with os.scandir(entry.path) as inner:
                for child in inner:
                    counter[0] += 1
                    try:
                        if child.is_file(follow_symlinks=False) and child.name.endswith(".jsonl"):
                            return True
                    except OSError:
                        continue
        except OSError:
            continue
    return False


def _tree_has_files(root, counter):
    """True when `root` contains any regular file. Breadth-first, early exit,
    hard entry budget."""
    stack = [root]
    seen = 0
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    counter[0] += 1
                    seen += 1
                    if seen > _MAX_TREE_ENTRIES:
                        return True
                    try:
                        if entry.is_file(follow_symlinks=False):
                            return True
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return False


def _onboarding_completed(state_dir):
    marker = state_dir / "onboarding.json"
    try:
        with open(marker, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return bool(data.get("completed"))
    except (OSError, ValueError):
        return False


def moment_zero_state(home=None, counter=None):
    """Return {ok, fresh_install, has_history, signals}.

    `home` defaults to Path.home() (tests pass a tmp dir). `counter` is an
    optional one-element list receiving the number of directory entries
    inspected — the perf test uses it to prove the scan stays bounded.
    """
    home = Path(home) if home is not None else Path.home()
    counter = counter if counter is not None else [0]

    # A positive verdict never needs rescanning: transcripts are not normally
    # removed, so "has history" is a one-way door.
    if (
        _cache["home"] == str(home)
        and _cache["result"] is not None
        and _cache["result"]["has_history"]
    ):
        return dict(_cache["result"])

    projects_dir = home / ".claude" / "projects"
    codex_sessions = home / ".codex" / "sessions"
    state_dir = home / ".claude" / "command-center"

    sig = _signature(projects_dir, codex_sessions, state_dir / "onboarding.json")
    if (
        _cache["home"] == str(home)
        and _cache["sig"] == sig
        and _cache["result"] is not None
    ):
        return dict(_cache["result"])

    onboarding_done = _onboarding_completed(state_dir)
    claude_history = _claude_projects_have_transcripts(projects_dir, counter)
    codex_history = False if claude_history else _tree_has_files(codex_sessions, counter)

    has_history = bool(onboarding_done or claude_history or codex_history)
    result = {
        "ok": True,
        "fresh_install": not has_history,
        "has_history": has_history,
        "signals": {
            "onboarding_completed": bool(onboarding_done),
            "claude_transcripts": bool(claude_history),
            "codex_sessions": bool(codex_history),
        },
    }
    _cache.update({"home": str(home), "sig": sig, "result": result})
    return dict(result)


def reset_cache():
    """Tests only: drop the cached verdict."""
    _cache.update({"home": None, "sig": None, "result": None})
