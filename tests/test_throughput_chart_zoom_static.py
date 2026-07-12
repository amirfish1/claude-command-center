import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_throughput_chart_has_hover_zoom_control():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert 'id="chart-zoom-btn"' in throughput_html
    assert ".chart-container:hover .chart-zoom-btn" in throughput_html
    assert "CHART_ZOOM_HOURS = 48" in throughput_html
    assert "applyChartZoomRows(" in throughput_html
    assert "displayTimedRows" in throughput_html
    assert "focusMaxMs" in throughput_html
    assert "chartZoomLastHours = !chartZoomLastHours" in throughput_html


def test_aggregate_chart_never_draws_legacy_fallback_and_labels_previous_week():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "let weeklyDataLoaded = false" in throughput_html
    assert "Weekly quota context unavailable" in throughput_html
    assert "waiting for weekly context" not in throughput_html
    assert "shouldDeferAggregateChart(" not in throughput_html
    assert 'id="previous-week-legend"' in throughput_html
    assert "Last week" in throughput_html
    assert "previousWeekLegend" in throughput_html
    assert "projectedLabelAnchor" in throughput_html


def test_weekly_quota_overlay_uses_fixed_100_percent_axis():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function quotaScaleMax" not in throughput_html
    assert "const WEEKLY_AXIS_MAX = 100" in throughput_html
    assert "projectedEnd > WEEKLY_AXIS_MAX ? ' ↑' : ''" in throughput_html


def test_token_axis_labels_show_weekly_percent_when_available():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function formatTokenAxisLabel" in throughput_html
    assert "formatCompactTokens" in throughput_html
    assert "tokenAxisPctPerToken" in throughput_html
    assert "formatTokenAxisLabel(val, tokenAxisPctPerToken)" in throughput_html
