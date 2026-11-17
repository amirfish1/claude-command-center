"""The one-click annotation queue action must acknowledge immediately."""

from tests.dashboard_source import dashboard_source
from pathlib import Path


def test_queue_button_shows_saving_while_annotation_persists():
    app_js = dashboard_source(Path(__file__).resolve().parents[1] / "static" / "app.js")
    start = app_js.index("    if (uxQueueNowBtn) {")
    body = app_js[start:app_js.index("    saveBtn.addEventListener", start)]
    assert "uxQueueNowBtn.textContent = 'Saving…';" in body
