from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import tempfile
import threading
import unittest

import pytest

import server


class TestRepoUsageSignals(unittest.TestCase):
    def test_missing_projects_root_returns_json_safe_zero_counts(self):
        original_root = server.PROJECTS_ROOT
        original_cache = dict(server._REPO_SIGNALS_CACHE)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "demo-repo"
                repo.mkdir()
                server.PROJECTS_ROOT = Path(tmp) / "missing-projects"
                server._REPO_SIGNALS_CACHE.update({"paths": (), "data": None, "ts": 0.0})

                result = server._compute_repo_usage_signals([str(repo)])

            json.dumps(result)
            windows = result[str(repo)]["signals"]
            self.assertEqual(windows["d7"]["sessions"], 0)
            self.assertEqual(windows["d30"]["sessions"], 0)
            self.assertEqual(windows["all"]["sessions"], 0)
        finally:
            server.PROJECTS_ROOT = original_root
            server._REPO_SIGNALS_CACHE.clear()
            server._REPO_SIGNALS_CACHE.update(original_cache)


def test_repo_signal_dates_keep_window_boundaries_and_skip_invalid_days(monkeypatch, tmp_path):
    class LocalDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 1, 7, 12)

    project = tmp_path / "project"
    project.mkdir()
    for name in ("one.jsonl", "two.jsonl"):
        (project / name).touch()
    day = {"messages": 1, "in_tokens": 2, "out_tokens": 3}
    by_date = {stamp: day for stamp in (
        "2026-1-1", "2025-12-31", "2025-12-9", "2025-12-8", "2026-1-8", "malformed",
    )}
    monkeypatch.setattr(server, "datetime", LocalDateTime)
    monkeypatch.setattr(server, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(server, "_candidate_conversation_dirs", lambda _: [project])
    monkeypatch.setattr(server, "_stats_get_file_agg", lambda transcript: {
        "session_id": transcript.stem, "by_date": by_date,
    })
    monkeypatch.setattr(server, "_maybe_persist_stats_cache", lambda _: False)

    assert server._build_repo_usage_signals(["/demo/repo"], 0)["/demo/repo"]["signals"] == {
        "d7": {"sessions": 2, "turns": 2, "tokens": 10},
        "d30": {"sessions": 2, "turns": 6, "tokens": 30},
        "all": {"sessions": 2, "turns": 8, "tokens": 40},
    }


@pytest.mark.parametrize("fails", [False, True])
def test_overlapping_repo_signal_reads_share_the_build_and_can_retry(monkeypatch, fails):
    waiting = threading.Event()
    started = threading.Event()
    finish = threading.Event()
    calls = []

    class ObservedFlight(Future):
        def result(self, timeout=None):
            waiting.set()
            return super().result(timeout=timeout)

    def build(repo_paths, _now):
        calls.append(repo_paths)
        if repo_paths == ["/demo/b"]:
            return {"b": {}}
        started.set()
        assert finish.wait(5)
        if fails and calls.count(repo_paths) == 1:
            raise OSError("stats read failed")
        return {"a": {}}

    monkeypatch.setattr(server, "Future", ObservedFlight)
    monkeypatch.setattr(server, "_build_repo_usage_signals", build)
    monkeypatch.setattr(server, "_REPO_SIGNALS_CACHE", {"paths": (), "data": None, "ts": 0.0})
    monkeypatch.setattr(server, "_REPO_SIGNALS_FLIGHTS", {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(server._compute_repo_usage_signals, ["/demo/a"])
        assert started.wait(5)
        follower = pool.submit(server._compute_repo_usage_signals, ["/demo/a"])
        try:
            assert waiting.wait(5)
            assert server._compute_repo_usage_signals(["/demo/b"]) == {"b": {}}
        finally:
            finish.set()
        if fails:
            for read in (owner, follower):
                with pytest.raises(OSError, match="stats read failed"):
                    read.result(timeout=5)
        else:
            assert owner.result(timeout=5) == follower.result(timeout=5) == {"a": {}}

    assert calls == [["/demo/a"], ["/demo/b"]]
    assert server._compute_repo_usage_signals(["/demo/a"]) == {"a": {}}
    assert calls.count(["/demo/a"]) == (2 if fails else 1)
    assert not server._REPO_SIGNALS_FLIGHTS


if __name__ == "__main__":
    unittest.main()
