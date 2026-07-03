import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_throughput_chart_has_hover_zoom_control():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert 'id="chart-zoom-btn"' in throughput_html
    assert ".chart-container:hover .chart-zoom-btn" in throughput_html
    assert "CHART_ZOOM_HOURS = 48" in throughput_html
    assert "applyChartZoomRows(" in throughput_html
    assert "chartZoomLastHours = !chartZoomLastHours" in throughput_html
