import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_weekly_banner_surfaces_codex_fable_and_timestamp():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert 'class="weekly-meter weekly-meter-claude"' in throughput_html
    assert 'class="weekly-meter weekly-meter-fable"' in throughput_html
    assert 'class="weekly-meter weekly-meter-codex"' in throughput_html
    assert 'id="weekly-fable-fill"' in throughput_html
    assert 'id="weekly-fable-pct"' in throughput_html
    assert 'id="weekly-codex-fill"' in throughput_html
    assert 'id="weekly-codex-pct"' in throughput_html
    assert "Codex session" in throughput_html
    assert "Codex plan" in throughput_html
    assert "Claude weekly limit" in throughput_html
    assert "Fable weekly limit" in throughput_html
    assert "Fable scoped" in throughput_html
    assert "fmtLastUpdated" in throughput_html
    assert "last updated" in throughput_html


def test_weekly_banner_uses_non_overlapping_two_row_layout():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert ".weekly-banner {\n    display: grid;" in throughput_html
    assert "grid-template-columns: minmax(0, 1fr) auto;" in throughput_html
    assert ".weekly-meters {\n    grid-column: 1 / -1;" in throughput_html
    assert ".weekly-meter {\n    flex: 1 1 0;" in throughput_html
    assert "min-width: 0;" in throughput_html
