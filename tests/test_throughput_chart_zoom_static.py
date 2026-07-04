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


def test_aggregate_chart_waits_for_weekly_context_and_labels_previous_week():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "let weeklyDataLoaded = false" in throughput_html
    assert "waiting for weekly context" in throughput_html
    assert "shouldDeferAggregateChart(" in throughput_html
    assert 'id="previous-week-legend"' in throughput_html
    assert "Last week" in throughput_html
    assert "previousWeekLegend" in throughput_html
    assert "projectedLabelAnchor" in throughput_html
