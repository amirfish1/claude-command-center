"""W87 static assertions: zoom ladder, daily report, main-view strip.

Same pattern as the other test_throughput_*_static.py files — cheap string
invariants that pin the wiring without booting a server.
"""
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(rel):
    return pathlib.Path(PROJECT_ROOT, rel).read_text(encoding="utf-8")


def test_server_has_window_and_daily_endpoints():
    server = _read("server.py")
    assert '"/api/throughput/window"' in server
    assert '"/api/throughput/daily"' in server
    assert "def _throughput_window_payload(" in server
    assert "def _throughput_daily_payload(" in server
    # Bounded-query guards: span/age caps and the (mtime,size) turn cache.
    assert "_THROUGHPUT_WINDOW_MAX_SPAN_SEC" in server
    assert "_THROUGHPUT_WINDOW_MAX_AGE_SEC" in server
    assert "_throughput_recent_conversations(engine, start_epoch)" in server
    # Finished days persist write-once snapshots; today coalesces via lock.
    assert "def _throughput_persist_daily_snapshot(" in server
    assert "_THROUGHPUT_DAILY_LOCK" in server
    assert '"/throughput-daily.html"' in server


def test_throughput_page_has_zoom_ladder():
    html = _read("static/throughput.html")
    # 3h bars are click-to-zoom (aggregate views), tagged for the ladder.
    assert "data-tz" in html
    assert "window.enterTputZoom" in html
    # L1 re-buckets the already-loaded hourly rows — no fetch at this level.
    assert "hourRowsInWindow" in html
    # L2 drills one hour through the bounded window endpoint.
    assert "/api/throughput/window?start=" in html
    # Escape pops one ladder level; session rows open the conversation.
    assert "e.key !== 'Escape' || !zoom" in html
    assert "ccc_popout=conversation&conv=" in html
    # The report link lives in the topbar.
    assert "/throughput-daily.html?date=yesterday" in html


def test_daily_report_page_exists_and_renders_digest():
    html = _read("static/throughput-daily.html")
    assert "/api/throughput/daily" in html
    for marker in ("prev day", "Tokens by model", "Top lanes"):
        assert marker in html, marker
    assert "ccc_popout=conversation&conv=" in html


def test_main_view_strip_is_wired():
    app_js = _read("static/app.js")
    assert "cccThroughputStrip" in app_js
    # Strip data rides the daily endpoint (server-side 180s TTL coalescing);
    # the client poll must stay slow and hidden-guarded.
    assert "/api/throughput/daily?date=today" in app_js
    assert "setInterval(refresh, 120000)" in app_js
    assert "document.hidden" in app_js
