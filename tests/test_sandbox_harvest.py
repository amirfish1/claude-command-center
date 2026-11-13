# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Unit tests for ccc_server/sandbox_harvest.py (multi-machine memory S8b)."""

import time

import pytest

from ccc_server import sandbox_harvest


@pytest.fixture
def harvest_env(tmp_path, monkeypatch):
    scan_root = tmp_path / "tmp"
    harvested_root = tmp_path / "harvested"
    scan_root.mkdir()
    monkeypatch.setenv("CCC_SANDBOX_SCAN_ROOT", str(scan_root))
    monkeypatch.setenv("CCC_HARVESTED_ROOT", str(harvested_root))
    return {"scan_root": scan_root, "harvested_root": harvested_root}


def _make_sandbox(scan_root, sandbox_id, claude_sessions=(), codex_sessions=()):
    sandbox_dir = scan_root / f"ccc-local-{sandbox_id}"
    for sid, text in claude_sessions:
        p = sandbox_dir / "home" / ".claude" / "projects" / "some-repo" / f"{sid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for sid, text in codex_sessions:
        p = sandbox_dir / "home" / ".codex" / "sessions" / "2026" / "09" / "28" / f"{sid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return sandbox_dir


def test_sandbox_dirs_discovers_ccc_local_prefix_only(harvest_env):
    scan_root = harvest_env["scan_root"]
    (scan_root / "ccc-local-abc123").mkdir()
    (scan_root / "ccc-local-def456").mkdir()
    (scan_root / "some-other-dir").mkdir()
    (scan_root / "not-a-dir-ccc-local-x").touch()

    found = sandbox_harvest._sandbox_dirs()
    names = sorted(p.name for p in found)
    assert names == ["ccc-local-abc123", "ccc-local-def456"]


def test_harvest_copies_claude_and_codex_transcripts(harvest_env):
    scan_root, harvested_root = harvest_env["scan_root"], harvest_env["harvested_root"]
    _make_sandbox(
        scan_root, "sbx1",
        claude_sessions=[("sess-a", '{"type": "user", "message": {"content": "hi"}}\n')],
        codex_sessions=[("rollout-x", '{"type": "response_item"}\n')],
    )

    result = sandbox_harvest.harvest_tick()

    assert result == {"sandboxes_scanned": 1, "files_copied": 2, "pruned": 0}
    claude_copy = harvested_root / "ccc-local-sbx1" / ".claude" / "projects" / "some-repo" / "sess-a.jsonl"
    codex_copy = harvested_root / "ccc-local-sbx1" / ".codex" / "sessions" / "2026" / "09" / "28" / "rollout-x.jsonl"
    assert claude_copy.read_text() == '{"type": "user", "message": {"content": "hi"}}\n'
    assert codex_copy.exists()


def test_harvest_preserves_source_mtime_and_size(harvest_env):
    scan_root, harvested_root = harvest_env["scan_root"], harvest_env["harvested_root"]
    sandbox_dir = _make_sandbox(scan_root, "sbx1", claude_sessions=[("sess-a", "x" * 500)])
    src = sandbox_dir / "home" / ".claude" / "projects" / "some-repo" / "sess-a.jsonl"
    src_st = src.stat()

    sandbox_harvest.harvest_tick()

    dest = harvested_root / "ccc-local-sbx1" / ".claude" / "projects" / "some-repo" / "sess-a.jsonl"
    dest_st = dest.stat()
    assert dest_st.st_size == src_st.st_size
    assert dest_st.st_mtime == pytest.approx(src_st.st_mtime, abs=1.0)


def test_warm_tick_recopies_nothing_when_unchanged(harvest_env):
    scan_root = harvest_env["scan_root"]
    _make_sandbox(scan_root, "sbx1", claude_sessions=[("sess-a", '{"type": "user"}\n')])

    first = sandbox_harvest.harvest_tick()
    second = sandbox_harvest.harvest_tick()

    assert first["files_copied"] == 1
    assert second["files_copied"] == 0


def test_changed_source_file_is_recopied(harvest_env):
    scan_root, harvested_root = harvest_env["scan_root"], harvest_env["harvested_root"]
    sandbox_dir = _make_sandbox(scan_root, "sbx1", claude_sessions=[("sess-a", "short")])
    sandbox_harvest.harvest_tick()

    src = sandbox_dir / "home" / ".claude" / "projects" / "some-repo" / "sess-a.jsonl"
    time.sleep(0.01)
    src.write_text("a much longer replacement transcript body")

    second = sandbox_harvest.harvest_tick()

    assert second["files_copied"] == 1
    dest = harvested_root / "ccc-local-sbx1" / ".claude" / "projects" / "some-repo" / "sess-a.jsonl"
    assert dest.read_text() == "a much longer replacement transcript body"


def test_sandbox_with_no_transcripts_copies_nothing(harvest_env):
    scan_root = harvest_env["scan_root"]
    (scan_root / "ccc-local-empty" / "home").mkdir(parents=True)

    result = sandbox_harvest.harvest_tick()

    assert result == {"sandboxes_scanned": 1, "files_copied": 0, "pruned": 0}


def test_prune_expired_removes_old_harvested_copies_only(harvest_env):
    harvested_root = harvest_env["harvested_root"]
    old_dir = harvested_root / "ccc-local-old"
    fresh_dir = harvested_root / "ccc-local-fresh"
    old_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)
    (old_dir / sandbox_harvest._META_FILENAME).write_text(str(time.time() - 40 * 86400))
    (fresh_dir / sandbox_harvest._META_FILENAME).write_text(str(time.time() - 1 * 86400))

    pruned = sandbox_harvest._prune_expired(harvested_root)

    assert pruned == 1
    assert not old_dir.exists()
    assert fresh_dir.exists()


def test_prune_falls_back_to_dir_mtime_when_meta_missing(harvest_env):
    harvested_root = harvest_env["harvested_root"]
    old_dir = harvested_root / "ccc-local-nometa"
    old_dir.mkdir(parents=True)
    old_ts = time.time() - 40 * 86400
    import os
    os.utime(old_dir, (old_ts, old_ts))

    pruned = sandbox_harvest._prune_expired(harvested_root)

    assert pruned == 1
    assert not old_dir.exists()


def test_harvest_tick_prunes_after_harvesting(harvest_env):
    scan_root, harvested_root = harvest_env["scan_root"], harvest_env["harvested_root"]
    stale_dir = harvested_root / "ccc-local-stale"
    stale_dir.mkdir(parents=True)
    (stale_dir / sandbox_harvest._META_FILENAME).write_text(str(time.time() - 45 * 86400))
    _make_sandbox(scan_root, "fresh", claude_sessions=[("sess-a", '{"type": "user"}\n')])

    result = sandbox_harvest.harvest_tick()

    assert result["pruned"] == 1
    assert not stale_dir.exists()
