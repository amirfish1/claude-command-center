"""Regression coverage for the optimistic Thinking-card cancel affordance."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_optimistic_thinking_card_offers_a_safe_cancel_action():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

    assert 'class="cl-cancel"' in app_js
    assert "cancelOptimisticAgentTurn" in app_js
    assert "fetch('/api/inject-esc'" in app_js
    assert ".conv-live-tool-inline .cl-cancel" in app_css
