"""Focused browser-side performance contracts for live activity."""

from tests.dashboard_source import dashboard_source
from pathlib import Path


APP_JS = Path(__file__).parents[1] / "static" / "app.js"


def test_live_activity_browser_has_one_request_owner_and_no_full_scan_fallback():
    source = dashboard_source(APP_JS)
    assert source.count("backgroundApiFetch('/api/sessions/live-activity?") == 1
    assert "fetchJSON('/api/sessions/live-activity'" not in source
    assert "fetchJSON('/api/sessions?all=1'" not in source


def test_live_activity_owner_skips_hidden_pages():
    source = dashboard_source(APP_JS)
    owner = source[source.index("async function refreshLiveSessionsActivity()"):
                   source.index("const $jumpBtnConv")]
    assert "document.hidden" in owner


def test_attention_refresh_skips_hidden_pages():
    source = dashboard_source(APP_JS)
    owner = source[source.index("async function loadAttentionList(opts)"):
                   source.index("function focusCardOnBoard")]
    assert "document.hidden" in owner


def test_attention_refresh_backs_off_and_skips_without_consumer():
    source = dashboard_source(APP_JS)
    owner = source[source.index("async function loadAttentionList(opts)"):
                   source.index("function focusCardOnBoard")]
    assert "if (!_ipNyaDetailsOn()) return;" in owner
    assert "Date.now() < _nyaNextFetchAt" in owner
    assert "Math.min(_nyaPollGapMs * 2, _NYA_POLL_MAX_MS)" in owner
