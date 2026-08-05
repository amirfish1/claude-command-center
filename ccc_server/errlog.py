# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Visibility for best-effort failures.

CCC deliberately keeps a lot of work best-effort: a read-only HOME, a
missing agent CLI, or a half-written state file must never take the server
down. The failure mode of that policy is `except Exception: pass` — the
symptom (a pin that will not stick, a title that never persists) shows up in
the UI with nothing in the log to explain it.

`log_swallowed()` keeps the best-effort behaviour and adds one line of
stderr. It is deliberately noise-averse: identical failures collapse to one
line per cooldown window, and the suppressed count rides along on the next
line, so a per-request write failing 500 times cannot flood the log.

Stdlib-only, no back-reference to server.py — safe to import from anywhere.

Env:
    CCC_QUIET_ERRORS=1   mute entirely
    CCC_DEBUG=1          append the full traceback
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
import traceback

COOLDOWN_S = 60.0

_LOCK = threading.Lock()
# (context, exception type name) -> [last_emit_epoch, suppressed_since_last_emit]
_SEEN = {}


def _quiet():
    return os.environ.get("CCC_QUIET_ERRORS") == "1"


def _debug():
    return os.environ.get("CCC_DEBUG") == "1"


def reset_state():
    """Drop the dedupe window. For tests."""
    with _LOCK:
        _SEEN.clear()


def _should_emit(key, now):
    """Rate-limit decision. Returns (emit, suppressed_count)."""
    with _LOCK:
        entry = _SEEN.get(key)
        if entry is None:
            _SEEN[key] = [now, 0]
            return True, 0
        if now - entry[0] >= COOLDOWN_S:
            suppressed = entry[1]
            _SEEN[key] = [now, 0]
            return True, suppressed
        entry[1] += 1
        return False, entry[1]


def log_swallowed(context, exc=None):
    """Report an exception that the caller is intentionally not propagating.

    `context` is a short, stable, greppable label — it doubles as the dedupe
    key, so keep request-specific values (session ids, paths) out of it
    unless the cardinality is genuinely small.
    """
    if _quiet():
        return
    if exc is None:
        exc = sys.exc_info()[1]
    kind = type(exc).__name__ if exc is not None else "unknown"
    emit, suppressed = _should_emit((str(context), kind), time.time())
    if not emit:
        return
    detail = f"{kind}: {exc}" if exc is not None else "no exception context"
    line = f"[ccc:swallowed] {context}: {detail}"
    if suppressed:
        line += f" (+{suppressed} suppressed in the last {int(COOLDOWN_S)}s)"
    print(line, file=sys.stderr, flush=True)
    if _debug() and exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)


@contextlib.contextmanager
def swallow(context, expected=Exception):
    """Run a best-effort block: swallow `expected`, but log it.

    Anything outside `expected` still propagates, so this cannot quietly
    widen a handler the way a bare `except Exception` does.
    """
    try:
        yield
    except expected as exc:  # noqa: BLE001 - the whole point of the helper
        log_swallowed(context, exc)
