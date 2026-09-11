"""Regression checks for the ticket-detail font-size controls."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")


def test_ticket_zoom_scales_the_card_back_into_the_viewport():
    """A 1.6x ticket font size must not enlarge the modal past its overlay."""
    assert "width: min(1020px, calc(98vw / var(--uxq-td-zoom, 1)));" in APP_CSS
    assert "max-height: calc(92vh / var(--uxq-td-zoom, 1));" in APP_CSS
