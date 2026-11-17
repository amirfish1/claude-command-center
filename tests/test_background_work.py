"""Warmers share a slot without gating cached HTTP responses."""
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest
import server
from ccc_server import background_work
from ccc_server.census_readiness import CensusCache


def test_serialization_is_reentrant_and_releases_after_failure():
    with pytest.raises(ValueError):
        with background_work.slot():
            with background_work.slot():
                raise ValueError("failed build")
    finished = threading.Event()
    def build():
        with background_work.slot():
            finished.set()
    thread = threading.Thread(target=build, daemon=True)
    thread.start()
    assert finished.wait(2)
    thread.join(2)


def test_different_census_scopes_do_not_build_concurrently():
    cache = CensusCache()
    entered = threading.Event()
    release = threading.Event()
    second = threading.Event()
    def build(key):
        if key == 86400:
            entered.set()
            assert release.wait(3)
        else:
            second.set()
        return {"ok": True, "sessions": []}
    try:
        cache.get(86400, build)
        assert entered.wait(2)
        cache.get(None, build)
        assert not second.wait(.05)
    finally:
        release.set()
    assert second.wait(2)


def test_forced_ticket_prewarm_and_refresh_share_one_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_WT_ITEMS_SNAPSHOT_FILE", tmp_path / "items.json")
    monkeypatch.setattr(server, "_wt_items_memo", {"at": 0, "items": None})
    entered = threading.Event()
    release = threading.Event()
    calls = []
    items = [{"ref": "TEST-1"}]
    def fetch():
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return items
    from ccc_server import wt_snapshot
    monkeypatch.setattr(wt_snapshot, "fetch", lambda queue: fetch())
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(server._wt_list_items_refresh_background, 0)
        assert entered.wait(2)
        second = pool.submit(server._wt_list_items_refresh_background, 0)
        # Prove the second forced caller has queued before publishing the memo.
        with background_work._condition:
            assert background_work._condition.wait_for(
                lambda: len(background_work._queue) == 2, timeout=2)
        release.set()
        assert first.result(3) == second.result(3) == items
    assert calls == [1]


def test_pages_and_cached_ticket_reads_bypass_busy_maintenance(monkeypatch):
    monkeypatch.setattr(server, "_wt_items_memo", {"at": 0, "items": [{"ref": "TEST-1"}]})
    monkeypatch.setattr(server, "_apps_focus_state", lambda: {})
    httpd = server.ThreadedHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        with background_work.slot():
            assert server._wt_list_items_snapshot_only() == [{"ref": "TEST-1"}]
            for route in ("/", "/static/app.css"):
                with urllib.request.urlopen(base + route, timeout=2) as response:
                    assert response.status == 200
                    assert len(response.read()) > 1000
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
