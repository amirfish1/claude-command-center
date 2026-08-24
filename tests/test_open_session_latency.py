"""Regression contracts for keeping conversation opens ahead of background UI work."""

import concurrent.futures
import inspect
import threading
import time
from pathlib import Path

import server


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def test_background_api_reads_have_a_global_concurrency_ceiling():
    active = 0
    peak = 0
    lock = threading.Lock()

    def slow_read():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.04)
            return "ok"
        finally:
            with lock:
                active -= 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: server._run_api_request(True, slow_read),
            range(8),
        ))

    assert results == ["ok"] * 8
    assert peak <= server._BACKGROUND_API_READ_LIMIT


def test_foreground_api_read_bypasses_saturated_background_slots():
    release = threading.Event()
    occupied = threading.Barrier(3)

    def blocking_read():
        occupied.wait(timeout=1)
        release.wait(timeout=1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        blockers = [
            pool.submit(server._run_api_request, True, blocking_read)
            for _ in range(2)
        ]
        occupied.wait(timeout=1)
        started = time.perf_counter()
        assert server._run_api_request(False, lambda: "foreground") == "foreground"
        assert time.perf_counter() - started < 0.03
        release.set()
        for blocker in blockers:
            blocker.result(timeout=1)


def test_generic_browser_reads_are_not_all_queued_behind_status_pollers():
    start = APP_JS.index("function backgroundApiFetch(")
    block = APP_JS[start:APP_JS.index("window.__cccBackgroundApiFetch", start)]
    assert "X-CCC-Background" not in block

    handler = inspect.getsource(server.CommandCenterHandler.do_GET)
    assert "X-CCC-Background" in handler
    assert "_run_api_request" in handler


def test_known_status_poll_routes_are_bounded_for_already_open_dashboards():
    slow_status_routes = (
        "/api/attention",
        "/api/group-chats/active",
        "/api/repo/ship/status",
        "/api/nextjs/status",
        "/api/vercel-deploy",
        "/api/repo/worktrees",
        "/api/repo/list",
        "/api/issues/all",
        "/api/queue/list",
        "/api/queue/status",
        "/api/sessions/live-activity",
        "/api/system/services",
        "/api/throughput/daily",
        "/api/watchtower/service/status",
        "/api/history/status",
    )
    for route in slow_status_routes:
        assert server._is_background_api_read(route, False), route

    assert server._is_background_api_read("/api/conversations/session-id", True)
    assert not server._is_background_api_read("/api/conversations/session-id", False)


def test_idle_ship_status_slow_parts_are_not_rebuilt_every_five_seconds():
    assert server._REPO_SHIP_STATUS_TTL >= 15.0


def test_session_localhost_probe_starts_only_after_the_transcript_load():
    set_start = APP_JS.index("function setCurrentSession(")
    set_block = APP_JS[set_start:APP_JS.index("async function launchTerminal", set_start)]
    assert "resetLocalhostPill()" in set_block
    assert "pollLocalhost()" not in set_block

    select_start = APP_JS.index("async function selectConversation(")
    select_block = APP_JS[select_start:APP_JS.index("// ── Split panel input bar", select_start)]
    transcript_at = select_block.index("await fetchConversationEvents(paneId)")
    localhost_at = select_block.index("pollLocalhost()", transcript_at)
    assert transcript_at < localhost_at
