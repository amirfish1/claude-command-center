# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Optional 'brain' plugin hook (MEMORY-1).

CCC ships no second-brain logic of its own. This is a generic, opt-in,
no-op-by-default hook so a private plugin living outside this repo (e.g.
~/Apps/second-brain) can plug into two places without CCC knowing anything
brain-specific:

  1. `ccc brain <subcmd> ...` passthrough -- see the `ccc` CLI's cmd_brain,
     which execs the plugin's own executable directly (no server round trip).
  2. A short "session-start index" text blob prepended to a spawned
     session's prompt, opt-in per repo via the config file below.

Config file: ~/.claude/command-center/brain-plugin.json
  {"plugin_path": "~/Apps/second-brain", "enabled_repos": ["/abs/repo/path"]}

Contract the plugin must satisfy (nothing here enforces it beyond the
subprocess call itself): an executable at <plugin_path>/bin/brain that
treats `session-start-index --repo <path>` as a request to print a short
text blob to stdout, and any other argv as its own CLI.

Zero cost when the config file is absent or a repo isn't listed in
enabled_repos: every entry point below returns after at most one cached
stat() of the config file, never touching a subprocess. Disable outright
with CCC_DISABLE_BRAIN_HOOK=1.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

BRAIN_CONFIG_FILE = Path(os.path.expanduser("~/.claude/command-center/brain-plugin.json"))
SESSION_START_TIMEOUT_S = 1.5
SESSION_START_MAX_CHARS = 2000

_CONFIG_CACHE = {"sig": None, "config": None}


def _brain_hook_disabled():
    return os.environ.get("CCC_DISABLE_BRAIN_HOOK", "").strip().lower() in ("1", "true", "yes")


def _normalize_path(p):
    return str(Path(os.path.expanduser(str(p))))


def _load_config():
    """(mtime, size)-cached read of brain-plugin.json. None if the file is
    absent, unreadable, or not a JSON object."""
    path = BRAIN_CONFIG_FILE
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        _CONFIG_CACHE["sig"] = None
        _CONFIG_CACHE["config"] = None
        return None
    if _CONFIG_CACHE["sig"] != sig:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = None
        _CONFIG_CACHE["sig"] = sig
        _CONFIG_CACHE["config"] = data if isinstance(data, dict) else None
    return _CONFIG_CACHE["config"]


def brain_plugin_executable():
    """Absolute path to the plugin's bin/brain executable, or None when
    unconfigured, not installed, or disabled. Cheap: one cached config read
    plus one is_file() stat -- no subprocess."""
    if _brain_hook_disabled():
        return None
    config = _load_config()
    if not config:
        return None
    plugin_path = config.get("plugin_path")
    if not plugin_path:
        return None
    exe = Path(_normalize_path(plugin_path)) / "bin" / "brain"
    if not exe.is_file() or not os.access(exe, os.X_OK):
        return None
    return exe


def is_enabled_for_repo(repo_path):
    """True only when the plugin is installed AND repo_path is explicitly
    listed in enabled_repos -- the per-repo on/off setting."""
    if not repo_path:
        return False
    if brain_plugin_executable() is None:
        return False
    config = _load_config() or {}
    enabled = config.get("enabled_repos")
    if not isinstance(enabled, list):
        return False
    target = _normalize_path(repo_path)
    return any(_normalize_path(r) == target for r in enabled if r)


def _run_capped(exe, args, timeout_s):
    """Run exe with args on a worker thread, abandoned (never joined again)
    past timeout_s rather than blocking the caller -- same pattern as
    shipped_check._is_shipped_capped."""
    box = {}

    def _run():
        try:
            box["out"] = subprocess.run(
                [str(exe), *args], capture_output=True, text=True, timeout=timeout_s,
            ).stdout
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive() or "error" in box:
        return None
    return box.get("out")


def session_start_index(repo_path, timeout_s=SESSION_START_TIMEOUT_S):
    """The plugin's short session-start text blob for repo_path, or None
    (not installed / not enabled for this repo / timed out / empty).
    Never raises and never blocks past timeout_s."""
    if not is_enabled_for_repo(repo_path):
        return None
    exe = brain_plugin_executable()
    if exe is None:
        return None
    out = _run_capped(exe, ["session-start-index", "--repo", str(repo_path)], timeout_s)
    text = (out or "").strip()
    if not text:
        return None
    return text[:SESSION_START_MAX_CHARS]


def session_start_prefix(repo_path, timeout_s=SESSION_START_TIMEOUT_S):
    """session_start_index() wrapped for prepending to a spawned prompt --
    empty string (never None) when there's nothing to inject."""
    text = session_start_index(repo_path, timeout_s)
    return f"{text.rstrip()}\n\n" if text else ""
