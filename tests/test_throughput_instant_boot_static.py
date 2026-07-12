import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = ROOT / "static" / "throughput.html"


def _html():
    return HTML.read_text(encoding="utf-8")


def _function(text, name, next_name=None):
    start = text.index(f"function {name}")
    if next_name:
        return text[start:text.index(f"function {next_name}", start)]
    return text[start:]


def test_cached_boot_precedes_network_work():
    html = _html()
    boot = _function(html, "bootThroughputPage", "updateThroughputEngineUi")

    read = boot.index("readThroughputBootstrap")
    apply = boot.index("applyThroughputBootstrap")
    layout = boot.index("toggleSidebar")
    defer = boot.index("queueMicrotask")
    network = boot.index("loadServerBootstrapThenRefresh")

    assert layout < read < apply < defer < network
    assert "bootThroughputPage();" in html


def test_complete_bootstrap_applies_context_before_one_dashboard_render():
    html = _html()
    apply = _function(html, "applyThroughputBootstrap", "showFirstSnapshotShell")

    weekly = apply.index("weeklyData = model.weekly")
    resets = apply.index("resetEvents = model.reset_events")
    render = apply.index("renderDashboard(")

    assert weekly < render
    assert resets < render
    assert apply.count("renderDashboard(") == 1
    assert "weeklyDataLoaded = true" in apply
    assert "performance.mark('throughput-bootstrap-rendered')" in apply


def test_browser_bootstrap_is_versioned_scoped_and_never_age_expires():
    html = _html()

    assert "const THROUGHPUT_BOOTSTRAP_SCHEMA = 1" in html
    assert "function throughputBootstrapKey(sessionId, engine)" in html
    assert "function validateThroughputBootstrap(model, sessionId, engine)" in html
    assert "model.schema !== THROUGHPUT_BOOTSTRAP_SCHEMA" in html
    assert "model.session_id !== sessionId" in html
    assert "model.engine !== engine" in html
    assert "localStorage.removeItem(key)" in html
    assert "maxAge" not in _function(
        html, "validateThroughputBootstrap", "readThroughputBootstrap"
    )


def test_browser_snapshot_compacts_hourly_rows_to_native_chart_resolution():
    html = _html()
    writer = _function(html, "writeThroughputBootstrap", "isNewerThroughputBootstrap")

    assert "function compactThroughputHourlyRows(rows)" in html
    assert "3 * 60 * 60 * 1000" in html
    assert "stored.throughput.summary.hourly = compactThroughputHourlyRows" in writer


def test_first_visit_uses_final_view_shell_not_legacy_graph():
    html = _html()
    shell = _function(html, "showFirstSnapshotShell", "loadServerBootstrapThenRefresh")

    assert "dashboard-content" in shell
    assert "Preparing first snapshot…" in shell
    assert "throughput-chart" in shell
    assert "showLoader" not in shell
    assert "renderChartWaitingForWeeklyContext" not in html
    assert "shouldDeferAggregateChart" not in html


def test_first_snapshot_shell_shows_live_bounded_work_counts():
    html = _html()
    progress = _function(html, "renderFirstSnapshotProgress", "loadServerBootstrapThenRefresh")

    assert "last 14 days" in progress
    assert "sessions_discovered" in progress
    assert "sessions_read" in progress
    assert "cache_hits" in progress
    assert "parsed" in progress
    assert "Finding recent conversations" in progress
    assert "Reading conversations" in progress
    assert "Calculating weekly context" in progress
    assert "renderFirstSnapshotProgress(refreshStatus)" in html


def test_server_bootstrap_only_applies_complete_newer_models():
    html = _html()
    loader = _function(html, "loadServerBootstrapThenRefresh", "bootThroughputPage")

    assert "/api/throughput/initial" in loader
    assert "validateThroughputBootstrap" in loader
    assert "isNewerThroughputBootstrap" in loader
    assert "applyThroughputBootstrap" in loader
    assert "finally" in loader
    assert "startRefreshStatusPolling(session)" in loader
    assert "renderDashboard(_aggDefault, data)" not in loader


def test_aggregate_boot_defers_sidebar_archive_until_refresh_finishes():
    html = _html()
    load_sessions = html[html.index("async function loadSessions"):html.index("function findSessionById")]
    boot = _function(html, "bootThroughputPage", "updateThroughputEngineUi")
    complete = _function(html, "loadCompletedThroughputBootstrap", "startRefreshStatusPolling")

    assert "selectDefault = true" in load_sessions
    assert "selectDefault && !activeSessionId" in load_sessions
    assert "loadSessions({ selectDefault: false })" not in boot
    assert "loadSessions({ selectDefault: false })" in complete


def test_refresh_panel_is_accessible_and_surfaces_all_progress_fields():
    html = _html()

    assert 'id="throughput-refresh-panel"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="refresh-primary"' in html
    assert 'id="refresh-secondary"' in html

    render = _function(html, "renderRefreshPanel", "stopRefreshStatusPolling")
    assert "last_refreshed_at" in render
    assert "expected_ms" in render
    assert "sessions_read" in render
    assert "sessions_discovered" in render
    assert "cache_hits" in render
    assert "parsed" in render


def test_refresh_progress_uses_live_timer_and_lightweight_polling():
    html = _html()
    polling = _function(html, "startRefreshStatusPolling", "refreshThroughput")

    assert "/api/throughput/refresh/start" in polling
    assert "/api/throughput/refresh/status" in polling
    assert "setInterval(renderElapsed, 100)" in polling
    assert "setInterval(poll, 500)" in polling
    assert "loadCompletedThroughputBootstrap" in polling


def test_refresh_completion_publishes_complete_model_atomically():
    html = _html()
    complete = _function(
        html,
        "loadCompletedThroughputBootstrap",
        "startRefreshStatusPolling",
    )

    assert "/api/throughput/initial" in complete
    assert "validateThroughputBootstrap" in complete
    assert "writeThroughputBootstrap(model)" in complete
    assert "applyThroughputBootstrap(model, 'refresh')" in complete
    assert "fetchWeekRankings()" in complete
    assert "loadWeeklyHistory()" in complete
    assert "renderDashboard(" not in complete


def test_cached_paint_does_not_start_competing_aggregate_scans():
    html = _html()
    apply = _function(html, "applyThroughputBootstrap", "showFirstSnapshotShell")
    history = _function(html, "loadWeeklyHistory", "drawWeeklyHistory")

    assert "fetchWeekRankings()" not in apply
    assert "loadWeeklyHistory()" not in apply
    assert "/api/throughput/history?cache_only=1" in history


def test_manual_refresh_is_nonblocking_and_uses_same_single_flight_job():
    html = _html()

    assert 'onclick="refreshThroughput()"' in html
    refresh = _function(html, "refreshThroughput", "fetchWeekRankings")
    assert "startRefreshStatusPolling" in refresh
    assert "showLoader" not in refresh
    assert "loadSessions" not in refresh


def test_weekly_usage_banner_has_compact_two_line_summary():
    html = _html()
    render = _function(html, "renderWeeklyUsage", "loadWeeklyUsage")

    assert 'id="weekly-sync-line"' in html
    assert 'id="weekly-reset-line"' in html
    assert '>Claude</div>' in html
    assert '>Fable</div>' in html
    assert '>Codex</div>' in html
    assert "weekly-sub').title" in render
    assert "weekly-sync-line').textContent" in render
    assert "weekly-reset-line').textContent" in render
    assert "innerHTML = bits.join('<br>')" not in render
    assert "banner.style.display = 'grid'" in render
    assert "fableMeter.style.display = 'grid'" in render
    assert "codexMeter.style.display = 'grid'" in render
