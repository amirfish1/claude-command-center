# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Leaf module for the unified activity log and the recent-error ring.

Stdlib-only. Anything a test patches on `server` (or that still lives there)
is read through `_core`, which resolves `server` first and the registry of
adopted ccc_server modules when `server` is absent.
"""

from __future__ import annotations

import collections
import json
import tempfile
import threading
import time
from pathlib import Path

from ccc_server import core as _core
from ccc_server import test_isolation_active
from ccc_server.paths import COMMAND_CENTER_STATE_DIR


# Unified human-readable activity log — spawn/inject/kill/app-server-health
# events, one line each. Mirrors ~/.watchtower/activity.log's format
# (TIMESTAMP UTC  CATEGORY   VERB     detail) on purpose: the two logs get
# tailed side by side, so matching the layout means no re-learning a second
# format. Distinct from CODEX_TELEMETRY_FILE (JSONL, machine-oriented, one
# record per codex RPC stage) and _RESUME_LEDGER_FILE (JSONL, internal wake/
# resume bookkeeping) -- this one is for a human to skim "what did CCC do".
if test_isolation_active():
    # The test suite re-imports this module fresh per test (`sys.modules.pop
    # ("server"); import server`) without mocking this path, so every test
    # exercising an inject/spawn/kill code path wrote real lines into the
    # live dashboard's log -- polluting the file a human actually tails to
    # debug production issues. Cover pytest and direct unittest runs, and
    # redirect for the whole test process instead
    # of a per-test fixture, since that survives arbitrary re-imports.
    ACTIVITY_LOG_FILE = Path(tempfile.gettempdir()) / "ccc-test-activity.log"
else:
    ACTIVITY_LOG_FILE = COMMAND_CENTER_STATE_DIR / "logs" / "activity.log"


def _activity_log_preview(text, limit=160):
    """Collapse whitespace and truncate for a one-line log entry.

    Never raises: called from logging paths that must not break the action
    they're recording."""
    try:
        collapsed = " ".join(str(text or "").split())
    except Exception:
        return ""
    if len(collapsed) > limit:
        return collapsed[:limit].rstrip() + "…"
    return collapsed


# Set to True after the first successful mkdir of ACTIVITY_LOG_FILE's parent.
# The mkdir used to run on every _log_activity call; under memory pressure
# that syscall can stall for seconds, and liveness logging happens while
# holding _CODEX_APP_SERVER_LOCK -- stalling every thread waiting on that
# lock (see docs/HANDOFF_codex_appserver_liveness.md). Once the directory
# exists there is nothing to do, so only ever call mkdir again if a write
# actually fails with FileNotFoundError (e.g. the dir was deleted).
_ACTIVITY_LOG_DIR_READY = False


def _log_activity(category, verb, detail):
    """Append one line to the unified activity log (see ACTIVITY_LOG_FILE).

    Format matches ~/.watchtower/activity.log: `TIMESTAMP UTC  CATEGORY  VERB  detail`.
    Best-effort and silent on failure -- logging must never break the caller.

    Short single-line fields retain the legacy fixed-width layout (14/9
    chars). Other events use `TIMESTAMP UTC  @ccc-activity-v2 {JSON}` to
    preserve full category/verb names and detail without shifting columns
    or allowing embedded newlines to become extra records. Both formats
    remain readable with tail/grep and share the same API response fields.
    """
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC"
        category, verb, detail = str(category), str(verb), str(detail)
        fields = (category, verb, detail)
        if (len(category) <= 14 and len(verb) <= 9
                and category == category.strip() and verb == verb.strip()
                and all(not field or field.splitlines() == [field] for field in fields)):
            line = f"{category:<14}  {verb:<9}{detail}\n"
        else:
            payload = {"category": category, "verb": verb, "detail": detail}
            line = "@ccc-activity-v2 " + json.dumps(payload) + "\n"
        line = f"{now}  {line}"
        if not _core._ACTIVITY_LOG_DIR_READY:
            _core.ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            _core._ACTIVITY_LOG_DIR_READY = True
        try:
            with open(_core.ACTIVITY_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except FileNotFoundError:
            # Directory vanished after we cached it as existing -- recreate
            # and retry once.
            _core.ACTIVITY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(_core.ACTIVITY_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        pass


# Rolling timestamps of recent server-side errors (500s / handler exceptions).
# A bounded ring of epoch seconds; the count is computed over a trailing window
# at read time, so we never scan service.err.log on the hot path.
_recent_error_ts = collections.deque(maxlen=200)


_recent_error_lock = threading.Lock()


_RECENT_ERROR_WINDOW_S = 15 * 60  # 15 min


def _record_server_error():
    """Bump the in-process error counter. Call wherever the server handles/logs
    a real error (500 response, handler traceback). Cheap and lock-guarded."""
    with _recent_error_lock:
        _recent_error_ts.append(time.time())


def _recent_error_count(window_s=_RECENT_ERROR_WINDOW_S):
    cutoff = time.time() - window_s
    with _recent_error_lock:
        return sum(1 for t in _recent_error_ts if t >= cutoff)
