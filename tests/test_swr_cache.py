"""Cold SWR keys are computed once for concurrent callers (CCC-142)."""
import threading
import time

import pytest

from ccc_server import swr_cache


@pytest.fixture(autouse=True)
def _clean():
    swr_cache.clear()
    yield
    swr_cache.clear()


def _concurrent(n, fn):
    gate = threading.Barrier(n)
    out = []

    def run():
        gate.wait()
        out.append(fn())

    ts = [threading.Thread(target=run) for _ in range(n)]
    [t.start() for t in ts]
    [t.join(5) for t in ts]
    return out


def test_concurrent_cold_callers_share_one_compute():
    calls = []

    def compute():
        calls.append(1)
        time.sleep(0.1)
        return ({"ok": True, "n": len(calls)}, 200)

    out = _concurrent(6, lambda: swr_cache.cached(("savings", "all"), compute, fresh_s=30))
    assert len(calls) == 1
    assert out == [({"ok": True, "n": 1}, 200)] * 6


def test_failed_cold_compute_is_not_shared():
    calls = []

    def compute():
        calls.append(1)
        return ({"ok": False}, 500)

    _concurrent(3, lambda: swr_cache.cached("k", compute, fresh_s=30))
    assert len(calls) == 3


def test_bypass_still_recomputes():
    calls = []
    compute = lambda: calls.append(1) or {"v": len(calls)}  # noqa: E731
    swr_cache.cached("k", compute, fresh_s=30)
    assert swr_cache.cached("k", compute, fresh_s=30, bypass=True) == {"v": 2}
    assert swr_cache.cached("k", compute, fresh_s=30) == {"v": 2}


def test_different_keys_do_not_block_each_other():
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(2)
        return {"slow": True}

    t = threading.Thread(target=lambda: swr_cache.cached("a", slow, fresh_s=30))
    t.start()
    assert started.wait(2)
    t0 = time.perf_counter()
    assert swr_cache.cached("b", lambda: {"fast": True}, fresh_s=30) == {"fast": True}
    assert time.perf_counter() - t0 < 0.1
    release.set()
    t.join(2)
