"""Released-but-alive WatchTower workers must not read as still-working.

WatchTower keeps a released worker's process alive on purpose (the
conversation stays resumable), but it is no longer staffed or doing
anything. Before this fix every "live workers" surface in the dashboard
(Q2 board, RHS queue status strip, LIVE badges, the WatchTower server card's
"Busy: N" sentence, the TRIGGERED WORKERS sidebar) rendered it exactly like
an actively-working worker. These are now filtered out at the two server
choke points those views read from; the queue diagnostics report (a
deliberately separate, debug-only path) is unaffected and keeps surfacing
them via its own `released` field.
"""
import importlib
import json
import pathlib
import sys
import threading
import urllib.request

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _workers():
    return [
        {"worker_id": "ccc-active", "queue": "CCC", "session_id": "s-active",
         "alive": True},
        {"worker_id": "ccc-released", "queue": "CCC", "session_id": "s-released",
         "alive": True, "released_at": "2026-08-11T15:46:37Z"},
    ]


@pytest.fixture
def server():
    return importlib.import_module("server")


@pytest.fixture
def queue_events():
    return importlib.import_module("ccc_server.queue_events")


def test_health_payload_excludes_released_and_reports_count(
    server, queue_events, monkeypatch
):
    monkeypatch.setattr(server, "_wt_read_workers", _workers)
    monkeypatch.setattr(server._q, "list_items", lambda **kw: [])

    payload = queue_events._build_ux_fixes_health_payload_uncached()

    worker_ids = [w["worker_id"] for w in payload["wt_workers"]]
    assert worker_ids == ["ccc-active"]
    assert payload["wt_workers_released_count"] == 1


def test_queue_rollup_passes_through_released_count_without_counting_it_live(
    server, monkeypatch
):
    monkeypatch.setattr(
        server, "build_ux_fixes_health_payload",
        lambda: {"queues": [], "wt_workers": [_workers()[0]],
                  "wt_workers_released_count": 1},
    )

    rollup = server._watchtower_queue_rollup()

    assert rollup["workers_live"] == 1
    assert rollup["workers_released"] == 1


def test_system_services_watchtower_entry_surfaces_released_count(
    server, monkeypatch
):
    monkeypatch.setattr(
        server, "_watchtower_service_status",
        lambda include_queues=False: {
            "running": True, "state": "online", "workers_live": 1,
            "workers_released": 3,
        },
    )

    entry = server._system_services_watchtower_entry()

    assert entry["workers_live"] == 1
    assert entry["released_workers_count"] == 3


@pytest.fixture
def live_server(server, monkeypatch):
    """Real HTTP server on the actual CommandCenterHandler, so the
    /api/wt/workers test exercises the real routing/query-parsing code
    instead of a re-implementation of it."""
    monkeypatch.setattr(server, "_wt_read_workers", _workers)
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_wt_workers_endpoint_excludes_released_by_default(live_server):
    with urllib.request.urlopen(live_server + "/api/wt/workers") as response:
        result = json.load(response)

    assert [w["worker_id"] for w in result["workers"]] == ["ccc-active"]
    assert result["total"] == 1
    assert result["released_count"] == 1


def test_wt_workers_endpoint_includes_released_on_opt_in(live_server):
    with urllib.request.urlopen(
        live_server + "/api/wt/workers?include_released=1"
    ) as response:
        result = json.load(response)

    assert sorted(w["worker_id"] for w in result["workers"]) == [
        "ccc-active", "ccc-released",
    ]
    assert result["total"] == 2
    assert result["released_count"] == 1


def test_diagnostic_snapshot_is_unaffected_and_still_flags_released(
    server, monkeypatch
):
    """The debug-only diagnostics path reads _wt_read_workers() directly
    (not through the filtered health payload) and must keep seeing released
    workers -- that is the one place they should stay fully visible."""
    monkeypatch.setattr(server, "_wt_read_config", lambda: {"CCC": {
        "auto_drain": True, "desired_workers": 1, "claim_types": ["bug"],
    }})
    monkeypatch.setattr(server, "_wt_read_workers", _workers)
    monkeypatch.setattr(server._q, "list_items", lambda: [])
    monkeypatch.setattr(server, "_wt_diagnostic_events", lambda *a, **k: [])

    snapshot = server._build_queue_diagnostic_snapshot("CCC", now=1785758400)

    released_flags = {row["id"]: row["released"] for row in snapshot["workers"]}
    assert True in released_flags.values()


def test_sys_row_wt_carries_the_released_debug_toggle():
    """Structural guard for the debug-only affordance in the WatchTower row
    of the System status panel (#sysRowWt): a collapsed toggle + list, not
    wired into any other count on the page."""
    index_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    sys_row = index_html.split('id="sysRowWt"', 1)[1].split('<!-- The KeepAlive fact', 1)[0]
    assert 'id="cccWtReleasedToggle"' in sys_row
    assert 'id="cccWtReleasedCount"' in sys_row
    assert 'id="cccWtReleasedList"' in sys_row
    assert 'hidden' in sys_row.split('id="cccWtReleasedToggle"', 1)[1].split('>', 1)[0]
    assert '<script src="/static/q2-worker-idle.js"></script>' in index_html


def test_app_js_wires_the_released_toggle_to_the_opt_in_endpoint():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "_wtReleasedRender" in app_js
    assert "cccWtReleasedToggle" in app_js
    assert "/api/wt/workers?include_released=1" in app_js
    assert "svc.released_workers_count" in app_js or "released_workers_count" in app_js
