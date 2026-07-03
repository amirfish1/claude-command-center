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
