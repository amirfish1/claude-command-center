# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Shared read-only helpers for the engine-ingestion modules.

The per-engine adapters (opencode, kilo, grok, hermes, copilot_cli, …) all read
another tool's live SQLite store the same way: open the file normally, flip it
to query-only so the handle can never write to a foreign DB, and hand back rows
as `sqlite3.Row`. A read-only-URI open (`?mode=ro`) can silently miss
un-checkpointed WAL frames, so query-only on a normal handle is the standard
multi-reader path. Stdlib-only, like the rest of the package.
"""

from __future__ import annotations

import sqlite3


def path_if_exists(p):
    """Return `p` if it points at an existing path, else None.

    Swallows the OSError a stat can raise (permissions, dead symlink, …) so
    callers get a uniform "no store here" signal instead of a crash.
    """
    try:
        return p if p.exists() else None
    except OSError:
        return None


def connect_readonly(db, *, timeout=0.5):
    """Open `db` query-only with a `sqlite3.Row` factory, or None.

    `db` may be a path or None (the resolver returned "no store"); a falsy value
    or any `sqlite3.Error` yields None so callers can treat "no DB" and "DB
    unreadable" identically.
    """
    if not db:
        return None
    try:
        con = sqlite3.connect(str(db), timeout=timeout)
        con.execute("PRAGMA query_only=1")
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None
