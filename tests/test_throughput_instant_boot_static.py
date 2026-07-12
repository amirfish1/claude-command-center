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
    boot = _function(html, "bootThroughputPage")

    read = boot.index("readThroughputBootstrap")
    apply = boot.index("applyThroughputBootstrap")
    defer = boot.index("queueMicrotask")
    network = boot.index("loadServerBootstrapThenRefresh")

    assert read < apply < defer < network
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


def test_first_visit_uses_final_view_shell_not_legacy_graph():
    html = _html()
    shell = _function(html, "showFirstSnapshotShell", "loadServerBootstrapThenRefresh")

    assert "dashboard-content" in shell
    assert "Preparing first snapshot…" in shell
    assert "throughput-chart" in shell
    assert "showLoader" not in shell
    assert "renderChartWaitingForWeeklyContext" not in html
    assert "shouldDeferAggregateChart" not in html


def test_server_bootstrap_only_applies_complete_newer_models():
    html = _html()
    loader = _function(html, "loadServerBootstrapThenRefresh", "bootThroughputPage")

    assert "/api/throughput/initial" in loader
    assert "validateThroughputBootstrap" in loader
    assert "isNewerThroughputBootstrap" in loader
    assert "applyThroughputBootstrap" in loader
    assert "renderDashboard(_aggDefault, data)" not in loader


def test_aggregate_boot_does_not_reselect_default_after_sessions_load():
    html = _html()
    load_sessions = html[html.index("async function loadSessions"):html.index("function findSessionById")]
    boot = _function(html, "bootThroughputPage")

    assert "selectDefault = true" in load_sessions
    assert "selectDefault && !activeSessionId" in load_sessions
    assert "loadSessions({ selectDefault: false })" in boot
