import threading
import time

import server


def _reset_cache():
    server._invalidate_known_repo_paths()


def test_concurrent_callers_rebuild_the_known_repo_paths_once(monkeypatch):
    """Every request funnels through resolve_repo_path -> _known_repo_paths.

    When the memo expires, concurrent request threads must not each walk
    $HOME, the custom/recent repo files and ~/.claude/projects: one rebuild,
    everyone else gets its result.
    """
    _reset_cache()
    calls = []
    gate = threading.Barrier(6)

    def slow_walk():
        calls.append(threading.get_ident())
        time.sleep(0.05)
        return ["/repo/a", "/repo/b"]

    monkeypatch.setattr(server, "_known_repo_paths_uncached", slow_walk)
    results = []

    def worker():
        gate.wait()
        results.append(server._known_repo_paths())

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert results == [["/repo/a", "/repo/b"]] * 6
    _reset_cache()


def test_expired_known_repo_paths_serve_the_last_list_while_one_thread_rebuilds(monkeypatch):
    """A request must never block behind another thread's rebuild."""
    _reset_cache()
    monkeypatch.setattr(server, "_known_repo_paths_uncached", lambda: ["/repo/old"])
    assert server._known_repo_paths() == ["/repo/old"]
    # Expire the memo without dropping the last value.
    server._KNOWN_REPO_PATHS_CACHE["at"] = 0.0

    started = threading.Event()
    release = threading.Event()

    def blocking_walk():
        started.set()
        release.wait(2)
        return ["/repo/new"]

    monkeypatch.setattr(server, "_known_repo_paths_uncached", blocking_walk)
    rebuilder = threading.Thread(target=server._known_repo_paths)
    rebuilder.start()
    assert started.wait(2)

    t0 = time.perf_counter()
    stale = server._known_repo_paths()
    elapsed = time.perf_counter() - t0
    release.set()
    rebuilder.join(2)

    assert stale == ["/repo/old"]
    assert elapsed < 0.5
    assert server._known_repo_paths() == ["/repo/new"]
    _reset_cache()


def test_invalidate_forces_the_next_call_to_walk_again(monkeypatch):
    _reset_cache()
    calls = []

    def walk():
        calls.append(1)
        return ["/repo/a"]

    monkeypatch.setattr(server, "_known_repo_paths_uncached", walk)
    server._known_repo_paths()
    server._known_repo_paths()
    assert len(calls) == 1
    server._invalidate_known_repo_paths()
    server._known_repo_paths()
    assert len(calls) == 2
    _reset_cache()
