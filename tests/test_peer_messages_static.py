"""Static markers for peer-message rendering in the single-file app."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "app.js").read_text()
APP_CSS = (ROOT / "static" / "app.css").read_text()


def test_app_js_renders_peer_sender_chip():
    assert "peerSenderHtml(" in APP_JS
    assert "peer-message" in APP_JS
    assert "class=\"peer-sender\"" in APP_JS


def test_app_js_skips_optimistic_echo_reconcile_for_peer_events():
    # A peer message must never consume a pending human send echo.
    assert "if (ev.peer)" in APP_JS or "ev.peer ?" in APP_JS


def test_app_js_renders_held_peer_notice():
    assert "peer_held" in APP_JS
    assert "not delivered" in APP_JS


def test_app_css_styles_peer_chip_and_held_notice():
    assert ".peer-sender" in APP_CSS
    assert ".event.system.peer-held" in APP_CSS
