# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Hand freed heap pages back to the operating system.

CPython frees objects, but the C allocator keeps the pages. A helper that peaks
at 160 MB during a scan and then holds 28 MB of live objects stayed at 330 MB
RSS. Stdlib-only (ctypes, which drops the GIL for the call). A platform without
these calls does nothing.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import threading
import time

RELEASE_EVERY_S = 60.0
_M_ARENA_MAX = -8  # glibc mallopt: without a cap, threads get up to 8 per core
_ARENA_CAP = 2

_started = False
_libc = None
_libc_loaded = False


def _load_libc():
    global _libc, _libc_loaded
    if not _libc_loaded:
        _libc_loaded = True
        try:
            _libc = ctypes.CDLL(None)
        except OSError:
            _libc = None
    return _libc


def cap_allocator_arenas():
    """Limit glibc to a few heaps. Python work is serialized by the GIL, so
    per-thread heaps buy little here and strand free memory in each one."""
    libc = _load_libc()
    fn = getattr(libc, "mallopt", None) if libc is not None else None
    if fn is None or os.environ.get("MALLOC_ARENA_MAX"):
        return False
    return bool(fn(_M_ARENA_MAX, _ARENA_CAP))


def release_freed_memory():
    """True when the platform call ran. Linux: malloc_trim. macOS: pressure relief."""
    libc = _load_libc()
    if libc is None:
        return False
    trim = getattr(libc, "malloc_trim", None)
    if trim is not None:
        trim(0)
        return True
    relief = getattr(libc, "malloc_zone_pressure_relief", None)
    if relief is not None and sys.platform == "darwin":
        relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        relief.restype = ctypes.c_size_t
        relief(None, 0)
        return True
    return False


def start(interval_s=RELEASE_EVERY_S):
    """Once per process. CCC_MEMORY_RELEASE=0 turns both steps off."""
    global _started
    if _started or os.environ.get("CCC_MEMORY_RELEASE", "1") == "0":
        return
    _started = True
    cap_allocator_arenas()

    def loop():
        while True:
            time.sleep(interval_s)
            try:
                release_freed_memory()
            except Exception:
                pass

    threading.Thread(target=loop, name="ccc-memory-release", daemon=True).start()
