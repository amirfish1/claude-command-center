import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_model_breakdown_shows_weekly_limit_contribution():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "function formatModelWeeklyPct" in throughput_html
    assert "const modelRows = summary.per_model || []" in throughput_html
    assert "renderModelBreakdown(modelRows, modelBreakdownContext(modelRows))" in throughput_html
    assert "claudeWeeklyPct" in throughput_html
    assert "claudeWeeklyTotalTokens" in throughput_html
    assert "codexWeeklyPct" in throughput_html
    assert '<div class="model-cell">% Weekly</div>' in throughput_html
    assert "model-weekly-cell" in throughput_html
