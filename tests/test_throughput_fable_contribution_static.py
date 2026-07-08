import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_main_chart_shows_fable_weekly_contribution():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert 'id="fable-contribution-legend"' in throughput_html
    assert "Fable % of weekly" in throughput_html
    assert "_fableRatio" in throughput_html
    assert "cumFableVals" in throughput_html
    assert "% fable" in throughput_html
