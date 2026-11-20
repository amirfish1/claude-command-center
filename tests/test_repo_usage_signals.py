from concurrent.futures import Future, ThreadPoolExecutor
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
