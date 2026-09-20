# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Leaf module for state directories, file constants, and binary candidate paths.

Stdlib-only. No imports from server or ccc_server.core.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil

# Core state directory and derived paths
COMMAND_CENTER_STATE_DIR = Path.home() / ".claude" / "command-center"
COMMAND_CENTER_PASTED_IMAGES_DIR = COMMAND_CENTER_STATE_DIR / "pasted-images"
COMMAND_CENTER_ATTACHMENTS_DIR = COMMAND_CENTER_STATE_DIR / "attachments"
PYTHON_STACK_DUMP_LOG = COMMAND_CENTER_STATE_DIR / "logs" / "python-stacks.log"

LOG_VIEWER_STATE_DIR = COMMAND_CENTER_STATE_DIR
PINNED_CONVERSATIONS_FILE = COMMAND_CENTER_STATE_DIR / "pinned-conversations.json"  # [session_id,...]
SPAWN_DEFAULTS_FILE = COMMAND_CENTER_STATE_DIR / "spawn-defaults.json"
SPAWNED_PIDS_FILE = COMMAND_CENTER_STATE_DIR / "spawned-pids.json"
USAGE_LIMIT_RESUME_FILE = COMMAND_CENTER_STATE_DIR / "usage_limit_resumes.json"

_CODEX_THREAD_REGISTRY_ENV = (
    os.environ.get("CCC_CODEX_THREAD_REGISTRY")
    or os.environ.get("WATCHTOWER_CODEX_THREAD_REGISTRY")
)
CODEX_THREAD_REGISTRY_FILE = (
    Path(os.path.expanduser(_CODEX_THREAD_REGISTRY_ENV))
    if _CODEX_THREAD_REGISTRY_ENV else
    COMMAND_CENTER_STATE_DIR / "codex-thread-registry.json"
)
SESSION_OVERRIDES_FILE = COMMAND_CENTER_STATE_DIR / "session-overrides.json"

CODEX_GOALS_DB_CANDIDATES = (
    Path.home() / ".codex" / "goals_1.sqlite",
    Path.home() / ".codex" / "sqlite" / "goals_1.sqlite",
)
CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
CODEX_APP_SERVER_STATE_FILE = COMMAND_CENTER_STATE_DIR / "codex-app-server-state.json"
CODEX_TELEMETRY_FILE = COMMAND_CENTER_STATE_DIR / "codex-telemetry.jsonl"
_SPAWN_TIMELINE_FILE = COMMAND_CENTER_STATE_DIR / "spawn-timeline.json"


def _which(cmd):
    """Return the absolute path of `cmd` on PATH, or None. shutil-free so the
    file stays stdlib-only without importing shutil at module top."""
    return shutil.which(cmd)


def _iter_common_cli_candidates(cmd):
    """Yield common user-install locations that launchd often omits from PATH."""
    home = Path.home()
    fixed = [
        Path("/opt/homebrew/bin") / cmd,
        Path("/usr/local/bin") / cmd,
        home / ".local" / "bin" / cmd,
        home / ".npm-global" / "bin" / cmd,
        home / "Library" / "pnpm" / cmd,
        home / ".volta" / "bin" / cmd,
        home / ".bun" / "bin" / cmd,
        home / ".asdf" / "shims" / cmd,
        home / ".nodenv" / "shims" / cmd,
        home / ".local" / "share" / "mise" / "shims" / cmd,
    ]
    seen = set()
    for p in fixed:
        s = str(p)
        if s not in seen:
            seen.add(s)
            yield p
    glob_roots = [
        (home / ".nvm" / "versions" / "node", f"*/bin/{cmd}"),
        (home / ".fnm" / "node-versions", f"*/installation/bin/{cmd}"),
    ]
    for root, pattern in glob_roots:
        if not root.is_dir():
            continue
        try:
            paths = sorted(root.glob(pattern), reverse=True)
        except OSError:
            continue
        for p in paths:
            s = str(p)
            if s not in seen:
                seen.add(s)
                yield p
    try:
        hidden_bin_paths = sorted(home.glob(f".*/bin/{cmd}"), reverse=True)
    except OSError:
        hidden_bin_paths = []
    for p in hidden_bin_paths:
        s = str(p)
        if s not in seen:
            seen.add(s)
            yield p


def _path_is_within(child, parent):
    try:
        child_p = Path(child).expanduser().resolve(strict=False)
        parent_p = Path(parent).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    return child_p == parent_p or parent_p in child_p.parents


# `sysctl`/`lsof` silently fail (FileNotFoundError -> ""). On Linux the same
# tools live elsewhere (or not at all), so resolve per platform: prefer the
# shipped macOS path when it exists, else a PATH lookup, else the bare name.
# Missing tools degrade to "" in _sys_run rather than crashing. sysctl and
# vm_stat are macOS-only; on Linux _sys_memory/_sys_cpu use /proc + os instead.
def _resolve_sys_tool(macos_path, name):
    if os.path.exists(macos_path):
        return macos_path
    return shutil.which(name) or name


_SYS_PS = _resolve_sys_tool("/bin/ps", "ps")
_SYS_LSOF = _resolve_sys_tool("/usr/sbin/lsof", "lsof")
_SYS_SYSCTL = _resolve_sys_tool("/usr/sbin/sysctl", "sysctl")
_SYS_VM_STAT = _resolve_sys_tool("/usr/bin/vm_stat", "vm_stat")
_SYS_OSASCRIPT = _resolve_sys_tool("/usr/bin/osascript", "osascript")
