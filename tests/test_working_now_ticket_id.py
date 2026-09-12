"""Regression coverage for ticket identifiers in compact worker rows."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_mobile_working_row_keeps_the_full_ticket_identifier_visible():
    """A ticket reference is the durable identity; the title may truncate first."""
    css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")
    start = css.index("#queuePanel.fq-mobile .fq-working-id {")
    rule = css[start:css.index("}", start)]

    assert "flex: 0 0 auto" in rule
    assert "overflow: visible" in rule
    assert "text-overflow: clip" in rule
