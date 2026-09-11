import threading
import time

import server
import pytest


@pytest.fixture
def isolated_sessions_cache(monkeypatch):
    monkeypatch.setattr(server, "_SESSIONS_SINGLEFLIGHT", {})
    monkeypatch.setattr(server, "_SESSIONS_RESPONSE_CACHE", {})
    monkeypatch.setattr(server, "resolve_repo_path", str)
    monkeypatch.setenv("CCC_SESSIONS_CACHE_TTL_MS", "2000")


def test_stale_snapshot_returns_during_one_refresh(monkeypatch, isolated_sessions_cache):
    key = ("/tmp/repo", False)
    old = {"ts": time.time() - 10, "rows": [{"id": "old"}]}
    server._SESSIONS_RESPONSE_CACHE[key] = old
    started, release = threading.Event(), threading.Event()
    calls = []

    def scan(repo_path, **kwargs):
        calls.append((repo_path, kwargs["include_old"]))
        started.set()
        assert release.wait(2)
        return [{"id": "new"}]

    monkeypatch.setattr(server, "find_all_sessions", scan)
    flight = None
    try:
        rows = server._load_sessions_singleflight(key[0], progress=False)
        assert rows == [{"id": "old"}]
        assert started.wait(1)
        flight = server._SESSIONS_SINGLEFLIGHT[key]["event"]
        for _ in range(4):
            assert server._load_sessions_singleflight(key[0], progress=False) == rows
        assert calls == [(key[0], False)]
    finally:
        release.set()
        if flight is not None:
            assert flight.wait(2)
    assert flight.is_set()
    assert server._load_sessions_singleflight(key[0], progress=False) == [{"id": "new"}]


def test_refresh_failure_keeps_snapshot_and_backs_off(monkeypatch, isolated_sessions_cache):
    key = ("/tmp/repo", False)
    server._SESSIONS_RESPONSE_CACHE[key] = {"ts": time.time() - 10, "rows": []}
    calls = []

    def scan(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("scan unavailable")

    monkeypatch.setattr(server, "find_all_sessions", scan)
    assert server._load_sessions_singleflight(key[0], progress=False) == []
    with server._SESSIONS_SINGLEFLIGHT_LOCK:
        flight = server._SESSIONS_SINGLEFLIGHT.get(key)
    if flight:
        assert flight["event"].wait(2)
    for _ in range(4):
        assert server._load_sessions_singleflight(key[0], progress=False) == []
    assert calls == [1]


def test_snapshot_scope_and_cache_opt_out(monkeypatch, isolated_sessions_cache):
    server._SESSIONS_RESPONSE_CACHE[("/tmp/repo", False)] = {
        "ts": time.time() - 10, "rows": [{"id": "stale"}],
    }
    monkeypatch.setattr(server, "find_all_sessions", lambda repo, **kw: [repo, kw["include_old"]])
    assert server._load_sessions_singleflight("/tmp/other", progress=False) == ["/tmp/other", False]
    assert server._load_sessions_singleflight("/tmp/repo", include_old=True, progress=False) == ["/tmp/repo", True]
    monkeypatch.setenv("CCC_SESSIONS_CACHE_TTL_MS", "0")
    assert server._load_sessions_singleflight("/tmp/repo", progress=False) == ["/tmp/repo", False]


def test_refresh_does_not_restore_invalidated_snapshot(monkeypatch, isolated_sessions_cache):
    key = ("/tmp/repo", False)
    server._SESSIONS_RESPONSE_CACHE[key] = {
        "ts": time.time() - 10, "rows": [{"id": "old", "tags": ["original"]}],
    }
    release = threading.Event()

    def scan(*args, **kwargs):
        assert release.wait(2)
        return [{"id": "obsolete"}]

    monkeypatch.setattr(server, "find_all_sessions", scan)
    flight = None
    try:
        rows = server._load_sessions_singleflight(key[0], progress=False)
        flight = server._SESSIONS_SINGLEFLIGHT[key]["event"]
        rows[0]["tags"].append("caller mutation")
        assert server._SESSIONS_RESPONSE_CACHE[key]["rows"][0]["tags"] == ["original"]
        with server._SESSIONS_SINGLEFLIGHT_LOCK:
            server._SESSIONS_RESPONSE_CACHE.pop(key)
    finally:
        release.set()
        if flight is not None:
            assert flight.wait(2)
    assert flight.is_set()
    assert key not in server._SESSIONS_RESPONSE_CACHE


def test_sessions_singleflight_coalesces_concurrent_scans(monkeypatch, tmp_path):
    repo = str(tmp_path)
    calls = 0
    gate = threading.Barrier(4)
    lock = threading.Lock()

    def fake_find_all_sessions(repo_path, progress=None, include_old=True):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.05)
        return [{"id": "sid-1", "session_id": "sid-1", "repo_path": repo_path}]

    monkeypatch.setattr(server, "resolve_repo_path", lambda path: str(path))
    monkeypatch.setattr(server, "find_all_sessions", fake_find_all_sessions)
    monkeypatch.setattr(server, "_session_load_begin", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_session_load_complete", lambda *args, **kwargs: None)
    monkeypatch.setattr(server, "_session_load_fail", lambda *args, **kwargs: None)

    with server._SESSIONS_SINGLEFLIGHT_LOCK:
        server._SESSIONS_SINGLEFLIGHT.clear()
        server._SESSIONS_RESPONSE_CACHE.clear()

    results = []
    errors = []

    def worker():
        try:
            gate.wait(timeout=2)
            results.append(
                server._load_sessions_singleflight(
                    repo,
                    include_old=False,
                    progress=True,
                )
            )
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert not errors
    assert len(results) == 4
    assert calls == 1
    assert all(rows == results[0] for rows in results)

    with server._SESSIONS_SINGLEFLIGHT_LOCK:
        server._SESSIONS_SINGLEFLIGHT.clear()
        server._SESSIONS_RESPONSE_CACHE.clear()
