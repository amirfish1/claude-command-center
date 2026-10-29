# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Keep an existing usage DB current: a periodic incremental ``ingest``.

Ingest used to run only by hand, so the notification digest and savings
lines read a DB frozen at the last manual run (CCC-267). The dashboard now
re-runs the incremental ingest every ``CCC_USAGE_INGEST_INTERVAL_S`` (default
1 hour) in a niced child process, so the parse never holds the server's GIL.

Only a DB that already exists is refreshed: installs that never ran
``throughput ingest`` get no new 25 MB file. ``CCC_USAGE_INGEST_DISABLED=1``
turns the loop off. A non-blocking flock beside the DB keeps two servers on
one HOME from ingesting at once.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time

from .cli import default_db_path

DEFAULT_INTERVAL_S = 3600
RUN_TIMEOUT_S = 1800


def _interval_s() -> int:
    try:
        return max(300, int(os.environ.get("CCC_USAGE_INGEST_INTERVAL_S") or DEFAULT_INTERVAL_S))
    except ValueError:
        return DEFAULT_INTERVAL_S


def run_once(db_path=None) -> str:
    """One incremental ingest. Returns 'no_db', 'busy', 'ok' or 'failed'."""
    path = db_path or default_db_path()
    if not os.path.exists(path):
        return "no_db"
    try:
        lock = open(path + ".ingest.lock", "a")
    except OSError:
        return "failed"
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "busy"
        cmd = [sys.executable, "-m", "ccc_server.usage_db", "--db", path, "ingest", "--json"]
        if hasattr(os, "nice"):
            cmd = ["nice", "-n", "10", *cmd]
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            proc = subprocess.run(cmd, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=RUN_TIMEOUT_S, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return "failed"
        # Exit 1 means some source file failed to parse; the rest still landed.
        return "ok" if proc.returncode in (0, 1) else "failed"
    finally:
        lock.close()


def ingest_loop(initial_delay_s=300):
    """Daemon thread target: ingest after a startup delay, then every interval."""
    try:
        time.sleep(initial_delay_s)
    except Exception:
        return
    while True:
        try:
            run_once()
        except Exception:
            pass
        try:
            time.sleep(_interval_s())
        except Exception:
            return
