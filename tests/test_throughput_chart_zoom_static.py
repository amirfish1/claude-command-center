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


def test_aggregate_chart_keeps_local_bars_when_weekly_quota_context_is_unavailable():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "let weeklyDataLoaded = false" in throughput_html
    assert 'id="quota-context-legend"' in throughput_html
    assert "Quota context unavailable" in throughput_html
    assert "if (quotaContextLegend) quotaContextLegend.style.display = isAggregate && sixhourly && !showWeeklyOverlay ? 'flex' : 'none';" in throughput_html
    assert "if (isAggregate && sixhourly && !weeklyChart)" not in throughput_html
    assert "buckets = [...allGroups.values()].sort((a, b) => new Date(a.hour) - new Date(b.hour));" in throughput_html
    assert "waiting for weekly context" not in throughput_html
    assert "shouldDeferAggregateChart(" not in throughput_html
    assert 'id="previous-week-legend"' not in throughput_html
    assert "prevPeriodRows" not in throughput_html
    assert "cumPrvVals" not in throughput_html
    assert "const CHART_HISTORY_DAYS = 7" in throughput_html
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


def test_chart_shows_week_of_history_and_short_capped_forecast():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "const CHART_HISTORY_DAYS = 7" in throughput_html
    assert "const CHART_FUTURE_DAYS = 2" in throughput_html
    assert "nowTs - CHART_HISTORY_DAYS * 86400000" in throughput_html
    assert "nowTs + CHART_FUTURE_DAYS * 86400000" in throughput_html
    assert "function forecastCrossingAt100" in throughput_html
    assert "const FORECAST_WIDTH_RATIO = 0.45" in throughput_html
    assert "forecast · compressed" in throughput_html
    assert "_prePeriod" in throughput_html
    assert "elapsedSlots" in throughput_html
    assert "projectedElapsedMs" in throughput_html
    assert "dt.getHours() < 6 && !isProj" in throughput_html
    assert "projectedCrossingLabel" in throughput_html
    assert "expected 100%" in throughput_html


def test_chart_draws_a_zero_based_pre_reset_quota_trace():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function buildPreviousQuotaSeries(" in throughput_html
    assert "row._prePeriod" in throughput_html
    assert "lastPreResetIndex" in throughput_html
    assert "startAtZero: true" in throughput_html
    assert "drawQuotaSeries(svg, previousSeries" in throughput_html
    assert "Previous cycle" in throughput_html


def test_codex_quota_series_uses_local_summary_hour_boundaries():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function throughputSummaryRowTimeMs(row)" in throughput_html
    assert "at: throughputSummaryRowTimeMs(row)" in throughput_html
    assert "const summaryHour = String(row.hour).trim()" in throughput_html


def test_combined_chart_draws_separate_normalized_quota_series():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function drawQuotaSeries" in throughput_html
    assert "claudeSeries" in throughput_html
    assert "codexSeries" in throughput_html
    assert "weeklyData.display_pct / claudeCurrentTokens" in throughput_html
    assert "weeklyData.codex.weekly_pct / codexCurrentTokens" in throughput_html


def test_zoomed_chart_switches_to_one_hour_columns():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function buildHourlyZoomRows(" in throughput_html
    assert "const zoomHourly = sixhourly && chartZoomLastHours && zoomState.zoomable" in throughput_html
    assert "const chartSlotMs = zoomHourly ? 3600000 : 3 * 3600000" in throughput_html
    assert "Hourly Cache-Adjusted Burn (last 48h)" in throughput_html
    assert "Cache-adj tokens / hour" in throughput_html


def test_provider_quota_context_survives_a_stale_snapshot_refresh():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function stabilizeWeeklyQuota(" in throughput_html
    assert "function isUsableWeeklyQuota(" in throughput_html
    assert "ccc-throughput-last-quota" in throughput_html
    assert "weeklyData = stabilizeWeeklyQuota(model.weekly)" in throughput_html
    assert "weeklyData = stabilizeWeeklyQuota(d && typeof d === 'object' ? d : {})" in throughput_html
