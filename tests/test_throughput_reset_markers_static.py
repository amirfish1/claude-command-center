import pathlib


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_throughput_reset_markers_are_clickable_and_editable():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert 'id="reset-event-modal"' in throughput_html
    assert "openResetEventModal(event)" in throughput_html
    assert "saveResetEventEdit()" in throughput_html
    assert "deleteResetEvent()" in throughput_html
    assert "recordLimitReset()" in throughput_html
    assert "reset-marker-hit" in throughput_html


def test_weekly_chart_filters_reset_markers_and_humanizes_codex_windows():
    throughput_html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

    assert "shouldRenderResetMarker(event, resetMarkerWindow)" in throughput_html
    assert "const resetMarkerWindow = activeThroughputEngine === 'codex'" in throughput_html
    assert "? 'codex'" in throughput_html
    assert "String(event.window || '').startsWith('codex_')" in throughput_html
    assert "let markerLabelCount = 0" in throughput_html
    assert "markerLabelCount % 3" in throughput_html
    assert "Codex 5-hour session" in throughput_html
    assert "Codex weekly" in throughput_html
    assert ".reset-event-field input:disabled" in throughput_html
    assert "-webkit-text-fill-color: var(--text-muted)" in throughput_html
