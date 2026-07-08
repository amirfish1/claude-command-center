import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_main_chart_shows_fable_weekly_contribution():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert 'id="fable-contribution-legend"' in throughput_html
    assert "Fable % of weekly" in throughput_html
    assert "_fableRatio" in throughput_html
    assert "cumFableVals" in throughput_html
    assert "% fable" in throughput_html


def test_fable_ratio_gated_on_current_period_activity():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    # summary.per_model is a rolling last-7-days total, not billing-period-scoped,
    # so the ratio must be gated on the correctly-scoped weeklyData.fable_pct to
    # avoid showing a contribution line from stale pre-reset Fable usage.
    assert "_fableActiveThisPeriod" in throughput_html
    assert "weeklyData.fable_pct != null && weeklyData.fable_pct > 0" in throughput_html
    assert "_fableActiveThisPeriod && _totalModelTok > 0 && _fableTok > 0" in throughput_html
