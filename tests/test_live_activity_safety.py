"""Focused browser-side performance contracts for live activity."""

from pathlib import Path


APP_JS = Path(__file__).parents[1] / "static" / "app.js"


def test_live_activity_browser_has_one_request_owner_and_no_full_scan_fallback():
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count("fetch('/api/sessions/live-activity?") == 1
    assert "fetchJSON('/api/sessions/live-activity'" not in source
    assert "fetchJSON('/api/sessions?all=1'" not in source


def test_live_activity_owner_skips_hidden_pages():
    source = APP_JS.read_text(encoding="utf-8")
    owner = source[source.index("async function refreshLiveSessionsActivity()"):
                   source.index("const $jumpBtnConv")]
    assert "document.hidden" in owner


def test_attention_refresh_skips_hidden_pages():
    source = APP_JS.read_text(encoding="utf-8")
    owner = source[source.index("async function loadAttentionList()"):
                   source.index("function focusCardOnBoard")]
    assert "document.hidden" in owner
