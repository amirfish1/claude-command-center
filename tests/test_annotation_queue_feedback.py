"""The one-click annotation queue action must acknowledge immediately."""

from pathlib import Path


def test_queue_button_shows_saving_while_annotation_persists():
    app_js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("    if (uxQueueNowBtn) {")
    body = app_js[start:app_js.index("    saveBtn.addEventListener", start)]
    assert "uxQueueNowBtn.textContent = 'Saving…';" in body
