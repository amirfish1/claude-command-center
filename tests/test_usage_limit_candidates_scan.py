"""The usage-limit watcher no longer stats every transcript each cycle (CCC-142)."""
import os
import time
from pathlib import Path

import pytest

import server  # noqa: F401
from ccc_server import usage_limit as ul


@pytest.fixture
def projects(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(ul, "_USAGE_LIMIT_CLAUDE_SCAN", {"full_at": 0.0, "root": None, "dirs": {}, "recent": set()})
    root = tmp_path / ".claude" / "projects"
    old = time.time() - 3 * 86400
    for d in range(5):
        (root / f"p{d}").mkdir(parents=True)
        for i in range(20):
            f = root / f"p{d}" / f"old-{d}-{i}.jsonl"
            f.write_text("{}\n")
            os.utime(f, (old, old))
    (root / "p0" / "live.jsonl").write_text("{}\n")
    return root


def _stats(monkeypatch):
    n = []
    real = Path.stat
    monkeypatch.setattr(Path, "stat", lambda self, *a, **k: n.append(1) or real(self, *a, **k))
    return n


def test_incremental_cycle_stats_dirs_and_recent_files_only(projects, monkeypatch):
    assert [sid for sid, _ in ul._usage_limit_claude_candidates(time.time())] == ["live"]
    n = _stats(monkeypatch)
    assert [sid for sid, _ in ul._usage_limit_claude_candidates(time.time())] == ["live"]
    assert len(n) <= 5 + 1  # one per project dir + the one recent file


def test_new_transcript_is_found_without_a_full_sweep(projects):
    ul._usage_limit_claude_candidates(time.time())
    d = projects / "p3"
    (d / "new.jsonl").write_text("{}\n")
    os.utime(d, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))  # dir mtime moves
    got = sorted(sid for sid, _ in ul._usage_limit_claude_candidates(time.time()))
    assert got == ["live", "new"]


def test_full_sweep_backstop_finds_appends_to_old_files(projects):
    ul._usage_limit_claude_candidates(time.time())
    f = projects / "p2" / "old-2-3.jsonl"
    os.utime(f, None)  # appended now; its dir mtime does not move
    assert "old-2-3" not in [sid for sid, _ in ul._usage_limit_claude_candidates(time.time())]
    ul._USAGE_LIMIT_CLAUDE_SCAN["full_at"] -= ul._USAGE_LIMIT_FULL_SWEEP_SECS + 1
    assert "old-2-3" in [sid for sid, _ in ul._usage_limit_claude_candidates(time.time())]


def test_expired_and_deleted_candidates_drop_out(projects):
    ul._usage_limit_claude_candidates(time.time())
    (projects / "p0" / "live.jsonl").unlink()
    assert ul._usage_limit_claude_candidates(time.time()) == []
