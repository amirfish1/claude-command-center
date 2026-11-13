# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Harvest transcripts out of ephemeral WatchTower worker sandboxes before
they're wiped (multi-machine memory, slice S8b).

Queue workers sometimes run inside an isolated HOME under
/tmp/ccc-local-<id>/home/..., and today that HOME -- and any transcript
inside it -- is lost on worker exit or reboot with no trace anywhere. That
gap is why the session that authored commit 1c5283f could not be found by
any memory design: only GitHub and the WatchTower ticket text survived.

A background loop (started from warm_start(), same shape as
ship_graph._run_origin_freshness_loop) periodically copies any transcript
files found under a live /tmp/ccc-local-* sandbox into
~/.claude/command-center/harvested/<sandbox-id>/, preserving the source's
mtime/size so session_fts's existing (mtime, size) gate indexes each file
exactly once. Harvested copies are pruned after _HARVEST_RETENTION_DAYS.

Not yet implemented: the spec's "keep indefinitely if a commit or ticket
edge points at it" retention exception -- there is no local data source for
that until WatchTower's own close-time machine/session/transcript recording
(slice S8a, a separate repo) exists to reference. See MEMORY-9.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

_HARVEST_LOCK = threading.Lock()
_HARVEST_STARTED = False
_HARVEST_TICK_INTERVAL_S = 60
_HARVEST_RETENTION_DAYS = 30.0

_META_FILENAME = ".harvested_at"


def _get_sandbox_scan_root() -> Path:
    env = os.environ.get("CCC_SANDBOX_SCAN_ROOT")
    if env:
        return Path(env)
    return Path("/tmp")


def _get_harvested_dir() -> Path:
    env = os.environ.get("CCC_HARVESTED_ROOT")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "command-center" / "harvested"


def _sandbox_dirs() -> list[Path]:
    root = _get_sandbox_scan_root()
    if not root.exists():
        return []
    try:
        return sorted(p for p in root.glob("ccc-local-*") if p.is_dir())
    except OSError:
        return []


def _sandbox_transcript_sources(sandbox_dir: Path) -> list[tuple[str, Path]]:
    """(engine, path) pairs for transcript files inside one sandbox HOME."""
    out: list[tuple[str, Path]] = []
    claude_dir = sandbox_dir / "home" / ".claude" / "projects"
    if claude_dir.exists():
        out.extend(("claude", p) for p in claude_dir.glob("*/*.jsonl"))
    codex_dir = sandbox_dir / "home" / ".codex" / "sessions"
    if codex_dir.exists():
        out.extend(("codex", p) for p in codex_dir.rglob("*.jsonl"))
    return out


def _harvest_one_sandbox(sandbox_dir: Path, harvested_root: Path) -> int:
    """Copy not-yet-harvested (or changed) transcript files out of one
    sandbox. Returns the number of files copied."""
    dest_root = harvested_root / sandbox_dir.name
    copied = 0
    for _engine, src in _sandbox_transcript_sources(sandbox_dir):
        try:
            st = src.stat()
        except OSError:
            continue
        rel = src.relative_to(sandbox_dir / "home")
        dest = dest_root / rel
        try:
            if dest.exists():
                dst_st = dest.stat()
                if dst_st.st_mtime == st.st_mtime and dst_st.st_size == st.st_size:
                    continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
        except OSError:
            continue
    if copied:
        try:
            (dest_root / _META_FILENAME).write_text(str(time.time()))
        except OSError:
            pass
    return copied


def _prune_expired(harvested_root: Path, retention_days: float = _HARVEST_RETENTION_DAYS) -> int:
    """Delete harvested sandbox copies whose harvest timestamp is older than
    retention_days."""
    if not harvested_root.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    pruned = 0
    try:
        entries = list(harvested_root.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_dir():
            continue
        meta = entry / _META_FILENAME
        try:
            harvested_at = float(meta.read_text().strip())
        except (OSError, ValueError):
            try:
                harvested_at = entry.stat().st_mtime
            except OSError:
                continue
        if harvested_at < cutoff:
            try:
                shutil.rmtree(entry)
                pruned += 1
            except OSError:
                continue
    return pruned


def harvest_tick() -> dict:
    """One pass: harvest every currently-live sandbox, prune expired
    harvested copies. Returns counts (used by tests and could back a
    doctor/health surface later)."""
    harvested_root = _get_harvested_dir()
    sandboxes = _sandbox_dirs()
    copied_total = 0
    for sandbox_dir in sandboxes:
        copied_total += _harvest_one_sandbox(sandbox_dir, harvested_root)
    pruned = _prune_expired(harvested_root)
    return {"sandboxes_scanned": len(sandboxes), "files_copied": copied_total, "pruned": pruned}


def _run_harvest_loop() -> None:
    """Background daemon modeled on ship_graph._run_origin_freshness_loop.
    Started once from warm_start(); idempotent while already running."""
    global _HARVEST_STARTED
    with _HARVEST_LOCK:
        if _HARVEST_STARTED:
            return
        _HARVEST_STARTED = True

    def _worker() -> None:
        while True:
            try:
                harvest_tick()
            except Exception:
                pass
            time.sleep(_HARVEST_TICK_INTERVAL_S)

    threading.Thread(target=_worker, daemon=True, name="ccc-sandbox-harvest").start()


def warm_start() -> None:
    """Call once at process/server start to begin the background harvest
    sweep. Cheap/no-op once already running."""
    _run_harvest_loop()
