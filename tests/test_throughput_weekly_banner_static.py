import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_weekly_banner_surfaces_codex_fable_and_timestamp():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert 'class="weekly-meter weekly-meter-claude"' in throughput_html
    assert 'class="weekly-meter weekly-meter-fable"' in throughput_html
    assert 'class="weekly-meter weekly-meter-codex"' in throughput_html
    assert 'class="weekly-meter weekly-meter-kimi"' in throughput_html
    assert 'id="weekly-fable-fill"' in throughput_html
    assert 'id="weekly-fable-pct"' in throughput_html
    assert 'id="weekly-codex-fill"' in throughput_html
    assert 'id="weekly-codex-pct"' in throughput_html
    assert 'id="weekly-kimi-fill"' in throughput_html
    assert 'id="weekly-kimi-pct"' in throughput_html
    assert 'id="kimi-next-reset"' in throughput_html
    assert "Kimi session" in throughput_html
    assert "Kimi plan" in throughput_html
    assert "Codex session" in throughput_html
    assert "Codex plan" in throughput_html
    assert "Claude weekly limit" in throughput_html
    assert "Fable weekly limit" in throughput_html
    assert "Fable scoped" in throughput_html
    assert "fmtLastUpdated" in throughput_html
    assert "last updated" in throughput_html


def test_weekly_banner_uses_non_overlapping_compact_layout():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert ".weekly-banner {\n    display: grid;" in throughput_html
    assert "grid-template-columns: minmax(460px, 1fr) minmax(240px, 340px) auto;" in throughput_html
    assert 'id="weekly-sync-line"' in throughput_html
    assert 'id="weekly-reset-line"' in throughput_html
    assert ".weekly-sub-line { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }" in throughput_html
    assert ".weekly-meter {\n    flex: 1 1 0;" in throughput_html
    assert "min-width: 0;" in throughput_html


def test_codex_aggregate_uses_weekly_period_overlay():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function weeklyChartContext(summary)" in throughput_html
    assert "codex.weekly_resets_at" in throughput_html
    assert "codex.weekly_pct" in throughput_html
    assert "const showWeeklyOverlay = !!weeklyChart" in throughput_html
    assert "const resetAt = new Date(weeklyChart.resetAt)" in throughput_html
    assert "const codexPpt" in throughput_html
    assert "weeklyData.codex.weekly_pct / codexCurrentTokens" in throughput_html


def test_aggregate_chart_has_no_legacy_all_hours_fallback():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "Fallback: all available 3h data" not in throughput_html
    assert "const firstH = hourly.length" not in throughput_html


def test_weekly_axis_is_fixed_at_100_with_overflow_and_midnight_labels():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "const WEEKLY_AXIS_MAX = 100" in throughput_html
    assert "Math.min(Math.max(p, 0), WEEKLY_AXIS_MAX)" in throughput_html
    assert "projectedEnd > WEEKLY_AXIS_MAX ? ' ↑' : ''" in throughput_html
    assert "midnightLabel.textContent = '00:00'" in throughput_html
    assert "!isCalendarDayStart(i)" in throughput_html
    assert "!isCalendarDayStart(i - 1)" in throughput_html
    assert "!isCalendarDayStart(i + 1)" in throughput_html
    assert "if (j > 0)" in throughput_html


def test_model_weekly_contributions_normalize_to_authoritative_total():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "claudeWeeklyPct" in throughput_html
    assert "claudeWeeklyTotalTokens" in throughput_html
    assert "(tokens / total) * pct" in throughput_html
    assert "tokens * ppt" not in throughput_html
