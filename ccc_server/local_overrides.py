# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Per-install names that must not live in the public source.

Some lookups only make sense for one person's repos: a repo folder that maps to
a differently named queue, shorthand a person uses for a repo, a page title that
carries no ticket information. They are read from one optional local file,
``<state dir>/local-overrides.json``; with no file every lookup is empty and CCC
falls back to its generic behaviour. Example:

    {
      "repo_project_map": {"acme-web": "ACME"},
      "repo_aliases": {"acme-web": ["\\\\bacme\\\\b"]},
      "ticket_title_noise": ["Acme: Home"],
      "commit_scopes": {"apps/acme-web": "web"},
      "ticket_prefix_repos": {"ACME": ["acme-web"]},
      "remote_apps_dir": "/srv/agent/Apps",
      "hermes_pending_dir": "~/.hermes/whatsapp/acme_pending",
      "owner_names": ["alex"]
    }

Stdlib only. The parse is cached by (mtime, size), so a lookup on a hot path
costs one stat(). A malformed file or value is ignored, never fatal.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "data": {}}


def overrides_path() -> Path:
    base = os.environ.get("CCC_STATE_DIR") or (Path.home() / ".claude" / "command-center")
    return Path(base) / "local-overrides.json"


def _load() -> dict:
    path = overrides_path()
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return {}
    with _LOCK:
        if _CACHE["key"] == key:
            return _CACHE["data"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    with _LOCK:
        _CACHE.update(key=key, data=data)
    return data


def _str_map(name: str) -> dict[str, str]:
    raw = _load().get(name)
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(k, str) and k and isinstance(v, str) and v}


def repo_project_map() -> dict[str, str]:
    """Lower-cased repo folder name -> queue project code."""
    return {k.lower(): v for k, v in _str_map("repo_project_map").items()}


def commit_scopes() -> dict[str, str]:
    """Repo-relative path prefix -> conventional-commit scope."""
    return _str_map("commit_scopes")


def repo_aliases() -> dict[str, list[str]]:
    """Repo name -> regexes (matched on lower-cased text); invalid ones dropped."""
    raw = _load().get("repo_aliases")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for repo, patterns in raw.items():
        if not isinstance(repo, str) or not repo or not isinstance(patterns, list):
            continue
        good = []
        for pat in patterns:
            try:
                if isinstance(pat, str) and pat:
                    re.compile(pat)
                    good.append(pat)
            except re.error:
                continue
        if good:
            out[repo] = good
    return out


def ticket_title_noise() -> list[str]:
    """Substrings that mark a ticket title as a page title, not a summary."""
    raw = _load().get("ticket_title_noise")
    return [t for t in raw if isinstance(t, str) and t] if isinstance(raw, list) else []


def ticket_prefix_repos() -> dict[str, list[str]]:
    """Upper-cased ticket prefix -> the only repos its tickets belong to."""
    raw = _load().get("ticket_prefix_repos")
    if not isinstance(raw, dict):
        return {}
    return {k.upper(): [r for r in v if isinstance(r, str)]
            for k, v in raw.items() if isinstance(k, str) and k and isinstance(v, list)}


def remote_apps_dir() -> str:
    """Folder holding repo checkouts on the always-on Linux host, or ""."""
    raw = _load().get("remote_apps_dir")
    return raw.rstrip("/") if isinstance(raw, str) and raw.startswith("/") else ""


def hermes_pending_dir() -> str:
    """Folder of pending Hermes WhatsApp items, or '' for the default."""
    value = _load().get("hermes_pending_dir")
    return value.strip() if isinstance(value, str) else ""


def owner_names() -> list[str]:
    """Lower-cased names that mean "the human owner" in action text."""
    raw = _load().get("owner_names")
    if not isinstance(raw, list):
        return []
    return [n.strip().lower() for n in raw if isinstance(n, str) and n.strip()]
