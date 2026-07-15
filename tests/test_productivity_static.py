from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_productivity_page_exposes_ranges_and_data_sections():
    html = (ROOT / "static" / "productivity.html").read_text()
    for token in (
        'data-weeks="6"',
        'data-weeks="8"',
        'data-weeks="12"',
        'data-weeks="16"',
        'id="summaryCards"',
        'id="weeklyTrend"',
        'id="projectTable"',
        'id="dailyTable"',
        'id="coveragePanel"',
    ):
        assert token in html
    assert "/api/productivity" in html


def test_productivity_page_handles_building_stale_and_failure_states():
    html = (ROOT / "static" / "productivity.html").read_text()
    assert "response.status === 202" in html
    assert "refresh.stale" in html
    assert "renderError" in html
    assert "escapeHtml" in html
    assert "indexDeliveries" in html
    assert "coverage.warning_count" in html
    assert "coverage-warning-details" in html
    assert "renderProjects(data.projects || [], deliveryIndex.byProject)" in html
    assert "renderDaily(data.daily || [], deliveryIndex.byDate)" in html
    assert "localStorage.setItem(PRODUCTIVITY_RANGE_KEY" in html
    assert "status=1" in html
    assert "pollRefreshStatus" in html
    assert "Math.min" in html


def test_productivity_page_distinguishes_unavailable_sources_from_zero():
    html = (ROOT / "static" / "productivity.html").read_text()
    assert "coverage.watchtower" in html
    assert "WatchTower unavailable" in html
    assert "presence.sampler_available" in html
    assert "Presence sampling unavailable" in html


def test_existing_surfaces_link_to_productivity():
    throughput = (ROOT / "static" / "throughput.html").read_text()
    app_js = (ROOT / "static" / "app.js").read_text()
    assert "/productivity.html" in throughput
    assert "/productivity.html" in app_js
    assert "cccProductivityPill" in app_js
