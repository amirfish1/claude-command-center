# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Reuse transcript-derived timelines until the exact source file changes."""

import json
import os
import threading

from .bounded_cache import ByteBudgetCache

_CACHE = ByteBudgetCache(max_bytes=8 << 20, max_entries=128)
# Fixed stripes bound synchronization state even when many sessions are opened.
_LOCKS = tuple(threading.Lock() for _ in range(16))


def _signature(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def read_timeline(path, reader, *, namespace, errors="strict"):
    """Read once per file version; return a detached JSON result on every call.

    Only transcript facts belong here. Live overrides and workspace state must
    still be resolved by their ordinary readers. Failed or changing reads are
    never published, and replacement/rewrite invalidates even preserved mtimes.
    """
    key = (namespace, os.fspath(path))
    with _LOCKS[hash(key) % len(_LOCKS)]:
        try:
            before = _signature(os.stat(path))
            cached = _CACHE.get(key)
            if cached is not None and cached[0] == before:
                return json.loads(cached[1])
            with open(path, "r", encoding="utf-8", errors=errors) as stream:
                opened = _signature(os.fstat(stream.fileno()))
                result = reader(stream)
                finished = _signature(os.fstat(stream.fileno()))
            if before == opened == finished == _signature(os.stat(path)):
                encoded = json.dumps(result, separators=(",", ":")).encode("utf-8")
                _CACHE.put(key, (before, encoded), len(encoded) + len(repr(key)) + 256)
            return result
        except OSError:
            _CACHE.pop(key, None)
            return {"events": [], "total_turns": 0}
