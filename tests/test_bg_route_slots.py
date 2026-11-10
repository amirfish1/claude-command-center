"""Regression tests for client-marked X-CCC-Background GET route normalization and slot bypass."""
import concurrent.futures
import threading
import time
import urllib.parse

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
    futs = [
        pool.submit(server._run_api_request, True, hog, f"/api/background-runs-{i}")
        for i in range(server._BACKGROUND_API_READ_LIMIT)
    ]
    occupied.wait(timeout=2)
    return pool, futs


class _DummyHandler:
    do_GET = server.CommandCenterHandler.do_GET

    def __init__(self, path, headers=None, callback=None):
        self.path = path
        self.headers = headers or {}
        self._callback = callback or (lambda: "ok")

    def _check_proxy_secret(self):
        return True

    def _phone_pin_blocked(self, _path):
        return False

    def _do_GET(self):
        return self._callback()


def test_client_marked_bg_route_outside_read_paths_learns_cost_and_bypasses_slots(routes):
    custom_path = "/api/conversations/session-abc123/files?limit=10"
    parsed_path = urllib.parse.urlparse(custom_path).path.rstrip("/")
    assert parsed_path not in server._BACKGROUND_API_READ_PATHS

    norm_route = server._normalize_bg_route(parsed_path)
    assert norm_route == "/api/conversations/:id/files"
    assert not server._bg_route_is_cheap(norm_route)

    # First client-marked background GET runs, records CPU cost, and becomes cheap.
    h1 = _DummyHandler(custom_path, headers={"X-CCC-Background": "1"})
    assert h1.do_GET() == "ok"
    assert norm_route in server._BG_ROUTE_CPU
    assert server._bg_route_is_cheap(norm_route)

    # A second client-marked background GET with a different session ID normalizes
    # to the same route and bypasses saturated background read slots.
    release = threading.Event()
    pool, futs = _fill_slots(release)
    try:
        t0 = time.perf_counter()
        h2 = _DummyHandler(
            "/api/conversations/session-xyz789/files",
            headers={"X-CCC-Background": "1"},
        )
        assert h2.do_GET() == "ok"
        assert time.perf_counter() - t0 < 0.1
    finally:
        release.set()
        [f.result(timeout=5) for f in futs]
        pool.shutdown()


def test_polled_background_paths_are_registered_and_normalized():
    expected = (
        "/api/wt/activity-log",
        "/api/wt/workers",
        "/api/session-status",
        "/api/decision-inbox/requests",
        "/api/takeover/observe",
        "/api/question-tags",
        "/api/watchtower/alerts",
    )
    for path in expected:
        assert path in server._BACKGROUND_API_READ_PATHS
        assert server._is_background_api_read(path, False)
        assert server._normalize_bg_route(path) == path
    assert server._normalize_bg_route(None) is None
    assert server._normalize_bg_route("") is None
