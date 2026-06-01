import threading
import time

import server


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
