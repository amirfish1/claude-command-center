"""The per-transcript stats cache used to be persisted only by /api/stats, an
overlay nobody opens, so the on-disk copy went stale for weeks and every
restart cold-parsed thousands of transcripts on the first /api/repo/list.
The repo-list signal path must persist it too, rate-limited and off-thread."""
import sys
import time


def _server():
    sys.argv = ["server.py"]
    import server
    return server


def test_repo_signals_persist_the_stats_cache_at_most_once_per_interval(monkeypatch):
    server = _server()
    calls = []
    monkeypatch.setattr(server, "_save_stats_file_cache", lambda: calls.append(time.time()))
    monkeypatch.setattr(server, "_STATS_CACHE_PERSIST_LAST", [0.0])

    assert server._maybe_persist_stats_cache(now=1000.0) is True
    server._STATS_CACHE_PERSIST_THREAD.join(5)
    assert len(calls) == 1

    assert server._maybe_persist_stats_cache(now=1000.0 + server._STATS_CACHE_PERSIST_INTERVAL_S - 1) is False
    assert len(calls) == 1

    assert server._maybe_persist_stats_cache(now=1000.0 + server._STATS_CACHE_PERSIST_INTERVAL_S + 1) is True
    server._STATS_CACHE_PERSIST_THREAD.join(5)
    assert len(calls) == 2


def test_compute_repo_usage_signals_schedules_a_persist(monkeypatch, tmp_path):
    server = _server()
    scheduled = []
    monkeypatch.setattr(server, "_maybe_persist_stats_cache", lambda now=None: scheduled.append(now) or True)
    monkeypatch.setattr(server, "_REPO_SIGNALS_CACHE", {"paths": None, "data": None, "ts": 0.0})
    monkeypatch.setattr(server, "PROJECTS_ROOT", tmp_path)  # empty root: walk finds nothing

    server._compute_repo_usage_signals([str(tmp_path / "repo")])

    assert len(scheduled) == 1
