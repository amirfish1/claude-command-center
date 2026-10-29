# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Session -> queue link for "Create queue for this session" (CCC-1225).

The link lives on the queue's own entry in WatchTower's queue-config.json
(``session_id``), not in one browser's localStorage, so agents, a task panel
and other browsers can all find a session's queue. ``offer_workers`` marks a
session queue that was created with auto-drain off and still owes the human a
one-time "start N workers?" prompt once its first tickets are filed.

WatchTower's own setters read-modify-write the same file and keep keys they
don't know about, so these extra keys survive `wt config` edits.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
MAX_OFFER_WORKERS = 16


def valid_session_id(value) -> str:
    """The trimmed session id, or raise ValueError."""
    sid = str(value or "").strip()
    if not _SESSION_ID_RE.fullmatch(sid):
        raise ValueError("session_id must be 1-128 letters, numbers, _ or -")
    return sid


def _read(path: Path) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _match(cfg: dict, queue: str):
    want = str(queue or "").strip().upper()
    return next((k for k in cfg if str(k).strip().upper() == want), None)


def patch_queue_config(path: Path, queue: str, updates: dict) -> dict:
    """Set (or, for a None value, drop) keys on one existing queue's entry.

    Returns the entry after the write. Raises KeyError for an unknown queue:
    linking must never mint a half-configured queue.
    """
    cfg = _read(path)
    key = _match(cfg, queue)
    if key is None or not isinstance(cfg.get(key), dict):
        raise KeyError(queue)
    entry = cfg[key]
    for k, v in updates.items():
        if v is None:
            entry.pop(k, None)
        else:
            entry[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    tmp.replace(path)
    return dict(entry)


def queues_for_session(cfg: dict, session_id: str) -> list:
    """Queue names linked to ``session_id``, sorted."""
    return sorted(
        str(name).upper() for name, conf in (cfg or {}).items()
        if isinstance(conf, dict) and str(conf.get("session_id") or "") == session_id
    )


def worker_offer_updates(workers) -> dict:
    """Config updates for answering the "start N workers?" prompt.

    0 declines (just clears the offer); N >= 1 starts draining with N workers.
    """
    try:
        n = int(workers)
    except (TypeError, ValueError):
        raise ValueError("workers must be a whole number")
    if not 0 <= n <= MAX_OFFER_WORKERS:
        raise ValueError("workers must be between 0 and %d" % MAX_OFFER_WORKERS)
    updates = {"offer_workers": None}
    if n:
        updates.update({"auto_drain": True, "desired_workers": n})
    return updates

