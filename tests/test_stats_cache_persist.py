"""The per-transcript stats cache used to be persisted only by /api/stats, an
overlay nobody opens, so the on-disk copy went stale for weeks and every
restart cold-parsed thousands of transcripts on the first /api/repo/list.
The repo-list signal path must persist it too, rate-limited and off-thread."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from ccc_server import usage_stats


def _server():
    sys.argv = ["server.py"]
    import server
    return server


def test_stats_prefilter_keeps_escaped_roles_and_existing_turn_rules(tmp_path):
    _server()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        '{"type":"progress","timestamp":"2026-01-05T12:00:00Z"}\n'
        '{"type":"progress","timestamp":"2026-01-05T12:00:01Z","data":{"type":"user"}}\n'
        '{"type":"\\u0075ser","timestamp":"2026-01-05T12:00:02Z","sessionId":"example"}\n'
        '{"type":"assistant","timestamp":"2026-01-05T12:00:03Z","isSidechain":true}\n'
        '{"type":"assistant","timestamp":"2026-01-05T12:00:04Z","message":'
        '{"id":"response","usage":{"input_tokens":10,"output_tokens":5}}}\n'
        '{"type":"assist\\u0061nt","timestamp":"2026-01-05T12:00:05Z","message":'
        '{"id":"response","usage":{"input_tokens":10,"output_tokens":5}}}\n',
        encoding="utf-8",
    )

    agg = usage_stats._stats_aggregate_file(transcript)

    assert agg["session_id"] == "example"
    days = agg["by_date"].values()
    assert sum(day["messages"] for day in days) == 3
    assert sum(day["in_tokens"] for day in days) == 10
    assert sum(day["out_tokens"] for day in days) == 5


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


def test_overlapping_stats_saves_keep_updates_in_publication_order(monkeypatch, tmp_path):
    server = _server()
    target = tmp_path / "stats_file_cache.json"
    entries = {"one": {"mtime": 1, "size": 1, "agg": {}}}
    monkeypatch.setattr(server, "_STATS_FILE_CACHE_FILE", target)
    monkeypatch.setattr(server, "_STATS_FILE_CACHE", entries)
    monkeypatch.setattr(usage_stats, "_STATS_FILE_CACHE_DIRTY", True)
    replacing = threading.Event()
    resume = threading.Event()
    second_started = threading.Event()
    published = []
    replace = Path.replace

    def replace_snapshot(path, dest):
        if dest != target:
            return replace(path, dest)
        if not replacing.is_set():
            replacing.set()
            assert resume.wait(5)
        published.append(json.loads(path.read_text())["entries"])
        return replace(path, dest)

    def save_again():
        second_started.set()
        server._save_stats_file_cache()

    monkeypatch.setattr(Path, "replace", replace_snapshot)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(server._save_stats_file_cache)
        try:
            assert replacing.wait(5)
            with usage_stats._STATS_CACHE_LOCK:
                entries["two"] = {"mtime": 2, "size": 2, "agg": {}}
                usage_stats._STATS_FILE_CACHE_DIRTY = True
            second = pool.submit(save_again)
            assert second_started.wait(5)
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            resume.set()
        first.result(timeout=5)
        second.result(timeout=5)

    assert [list(snapshot) for snapshot in published] == [["one"], ["one", "two"]]
    assert target.read_text() == json.dumps({"schema_version": 1, "entries": entries})
    assert not usage_stats._STATS_FILE_CACHE_DIRTY


def test_stats_save_failure_leaves_the_previous_file_and_retries(monkeypatch, tmp_path):
    server = _server()
    target = tmp_path / "stats_file_cache.json"
    entries = {"one": {"mtime": 1, "size": 1, "agg": {}}}
    target.write_text('{"schema_version": 1, "entries": {}}')
    monkeypatch.setattr(server, "_STATS_FILE_CACHE_FILE", target)
    monkeypatch.setattr(server, "_STATS_FILE_CACHE", entries)
    monkeypatch.setattr(usage_stats, "_STATS_FILE_CACHE_DIRTY", True)

    def fail_publication(path, dest):
        raise OSError("cannot replace cache")

    with monkeypatch.context() as failed:
        failed.setattr(Path, "replace", fail_publication)
        server._save_stats_file_cache()
    assert json.loads(target.read_text())["entries"] == {}
    assert usage_stats._STATS_FILE_CACHE_DIRTY

    server._save_stats_file_cache()
    assert json.loads(target.read_text())["entries"] == entries
    assert not usage_stats._STATS_FILE_CACHE_DIRTY
