"""Regression coverage for ticket identifiers in compact worker rows."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_workers_sidebar_keeps_the_full_ticket_identifier_visible():
    """The real Workers-sidebar host must own the non-truncating ID rule."""
    css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert 'class="conv-workers-activity"' in app_js
    start = css.index(".conv-workers-activity .fq-working-id {")
    rule = css[start:css.index("}", start)]

    assert "flex: 0 0 auto" in rule
    assert "overflow: visible" in rule
    assert "text-overflow: clip" in rule
