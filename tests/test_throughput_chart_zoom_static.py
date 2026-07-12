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


def test_token_axis_labels_do_not_mix_interval_tokens_with_cumulative_percent():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function formatTokenAxisLabel" in throughput_html
    assert "formatCompactTokens" in throughput_html
    assert "tokenAxisPctPerToken" not in throughput_html
    assert "formatTokenAxisLabel(val, null)" in throughput_html


def test_chart_keeps_two_prior_days_and_compresses_forecast_to_100():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "const PREVIOUS_CYCLE_DAYS = 2" in throughput_html
    assert "function forecastCrossingAt100" in throughput_html
    assert "const FORECAST_WIDTH_RATIO = 0.45" in throughput_html
    assert "forecast · compressed" in throughput_html
    assert "_prePeriod" in throughput_html
    assert "elapsedThreeHourSlots" in throughput_html
    assert "projectedElapsedMs" in throughput_html
    assert "dt.getHours() < 6 && !isProj" in throughput_html
    assert "projectedCrossingLabel" in throughput_html
    assert "expected 100%" in throughput_html


def test_combined_chart_draws_separate_normalized_quota_series():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function drawQuotaSeries" in throughput_html
    assert "claudeSeries" in throughput_html
    assert "codexSeries" in throughput_html
    assert "weeklyData.display_pct / claudeCurrentTokens" in throughput_html
    assert "weeklyData.codex.weekly_pct / codexCurrentTokens" in throughput_html
