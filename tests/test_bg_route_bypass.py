"""Cheap background reads skip the shared slots (CCC-142).

After a restart cold rebuilds held both background slots for 30-72 s and
sidebar reads that take milliseconds queued behind them.
"""
import concurrent.futures
import json
import threading
import time

import pytest

import server


@pytest.fixture
def routes(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_BG_ROUTE_CPU", {})
    monkeypatch.setattr(server, "_BG_ROUTE_LOCKS", {})
    monkeypatch.setattr(server, "_BG_ROUTE_CPU_FILE", tmp_path / "bg_route_cpu.json")
    monkeypatch.setattr(server, "_BG_ROUTE_CPU_STATE", {"dirty": False, "saved_at": 0.0})
    return tmp_path / "bg_route_cpu.json"


def _fill_slots(release):
    occupied = threading.Barrier(server._BACKGROUND_API_READ_LIMIT + 1)

    def hog():
        occupied.wait(timeout=2)
        release.wait(timeout=5)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=server._BACKGROUND_API_READ_LIMIT)
    futs = [pool.submit(server._run_api_request, True, hog, "/api/background-runs-%d" % i)
            for i in range(server._BACKGROUND_API_READ_LIMIT)]
    occupied.wait(timeout=2)
    return pool, futs


def test_cheap_route_bypasses_saturated_slots(routes):
    server._BG_ROUTE_CPU["/api/repo/list"] = 0.002
    release = threading.Event()
    pool, futs = _fill_slots(release)
    try:
        t0 = time.perf_counter()
        assert server._run_api_request(True, lambda: "ok", "/api/repo/list") == "ok"
        assert time.perf_counter() - t0 < 0.1
    finally:
        release.set()
        [f.result(timeout=5) for f in futs]
        pool.shutdown()


@pytest.mark.parametrize("route", ["/api/jobs", "/api/queue/status"])
def test_unknown_or_expensive_route_waits_for_a_slot(routes, route):
    server._BG_ROUTE_CPU["/api/jobs"] = 2.0
    release = threading.Event()
    pool, futs = _fill_slots(release)
    try:
        with concurrent.futures.ThreadPoolExecutor(1) as p2:
            f = p2.submit(server._run_api_request, True, lambda: "ok", route)
            with pytest.raises(concurrent.futures.TimeoutError):
                f.result(timeout=0.15)
            release.set()
            assert f.result(timeout=5) == "ok"
    finally:
        release.set()
        [x.result(timeout=5) for x in futs]
        pool.shutdown()


def test_same_route_runs_one_at_a_time(routes):
    server._BG_ROUTE_CPU["/api/repo/list"] = 0.001
    lock = threading.Lock()
    active = peak = 0

    def read():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1

    with concurrent.futures.ThreadPoolExecutor(6) as p:
        list(p.map(lambda _: server._run_api_request(True, read, "/api/repo/list"), range(6)))
    assert peak == 1


def test_route_cost_is_learned_and_survives_a_restart(routes):
    def burn():
        t = time.thread_time()
        while time.thread_time() - t < 0.12:
            pass

    server._run_api_request(True, burn, "/api/jobs")
    server._run_api_request(True, lambda: None, "/api/repo/list")
    assert not server._bg_route_is_cheap("/api/jobs")
    assert server._bg_route_is_cheap("/api/repo/list")
    assert server._save_bg_route_cpu(force=True)
    server._BG_ROUTE_CPU.clear()
    assert server._load_bg_route_cpu() == 2
    assert server._bg_route_is_cheap("/api/repo/list") and not server._bg_route_is_cheap("/api/jobs")


def test_bad_cost_files_are_ignored(routes):
    routes.write_text("{nope")
    assert server._load_bg_route_cpu() == 0
    routes.write_text(json.dumps({"schema": 9, "routes": {"/a": 0.1}}))
    assert server._load_bg_route_cpu() == 0
    routes.write_text(json.dumps({"schema": 1, "routes": {"/a": "x", "/b": -1}}))
    assert server._load_bg_route_cpu() == 0
