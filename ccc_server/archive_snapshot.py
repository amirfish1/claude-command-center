# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Publish archive snapshots safely across detached refresh processes.

Readers need no lock: replacement exposes either complete snapshot. Writers
merge under a separate, stable lock file so one worker's older in-memory
snapshot cannot erase another worker's newer entries or signature maps.
"""

from contextlib import contextmanager
import errno
import json
import math
import os
from pathlib import Path
import tempfile
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl


@contextmanager
def _writer_lock(cache_file, timeout):
    """Bound lock contention; a busy cache can be persisted on a later refresh."""
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file = cache_file.with_name(cache_file.name + ".lock")
    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b") as handle:
        if os.name == "nt" and not os.fstat(handle.fileno()).st_size:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0, timeout)
        acquired = False
        try:
            while True:
                try:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.01, remaining))
            yield acquired
        finally:
            if acquired:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _entries(snapshot):
    entries = snapshot.get("entries")
    if not isinstance(entries, dict):
        return {}
    valid = {}
    for key, entry in entries.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        if not isinstance(entry.get("conversations"), list):
            continue
        if entry.get("signature") is not None and not isinstance(entry["signature"], str):
            continue
        try:
            stamp = float(entry.get("cached_at") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(stamp):
            valid[key] = dict(entry, cached_at=stamp)
    return valid


def _merge(previous, incoming):
    if not isinstance(previous, dict) or any(
        previous.get(key) != incoming.get(key) for key in ("schema_version", "build_id")
    ):
        previous = {}
    entries = _entries(previous)
    for key, entry in _entries(incoming).items():
        if key not in entries or entry["cached_at"] > entries[key]["cached_at"]:
            entries[key] = entry
    maps = {}
    for source in (previous, incoming):
        raw_maps = source.get("signature_maps")
        if isinstance(raw_maps, dict):
            maps.update(raw_maps)
    referenced = {entry.get("signature") for entry in entries.values()}
    maps = {
        signature: mapping for signature, mapping in maps.items()
        if signature in referenced and isinstance(mapping, dict)
        and isinstance(mapping.get("files"), dict)
        and isinstance(mapping.get("extras"), dict)
    }
    return dict(incoming, entries=entries, signature_maps=maps)


def save(cache_file, snapshot, *, lock_timeout=0.5):
    """Merge and atomically publish metadata only; never read source transcripts.

    Return False on lock contention (the caller should not advance its debounce
    timer). Other I/O errors propagate for the caller's best-effort reporting.
    """
    cache_file = Path(cache_file)
    with _writer_lock(cache_file, lock_timeout) as acquired:
        if not acquired:
            return False
        try:
            with cache_file.open(encoding="utf-8") as handle:
                previous = json.load(handle)
        except (OSError, ValueError, UnicodeError):
            previous = {}
        merged = _merge(previous, snapshot)
        fd, temporary = tempfile.mkstemp(
            prefix="." + cache_file.name + "-", suffix=".tmp", dir=cache_file.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(merged, handle, separators=(",", ":"))
            os.replace(temporary, cache_file)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return True
