from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def test_sidebar_render_has_a_progressive_row_budget():
    start = SOURCE.index("function _boundSidebarRowsForRender")
    end = SOURCE.index("function renderConversationList", start)
    source = SOURCE[start:end]

    assert "const SIDEBAR_RENDER_INITIAL_ROWS = 240" in SOURCE
    assert "const SIDEBAR_RENDER_MORE_ROWS = 240" in SOURCE
    assert "classifyKanbanColumn(row)" in source
    assert "selected.size >= limit" in source
    assert "omitted:" in source


def test_render_list_uses_bounded_rows_and_offers_more_on_demand():
    start = SOURCE.index("function renderConversationList")
    end = SOURCE.index("function renderSidebar", start)
    source = SOURCE[start:end]

    assert "_boundSidebarRowsForRender(convs, _sidebarRenderRowLimit)" in source
    assert 'data-role="sidebar-show-more"' in source
    assert "_sidebarRenderRowLimit += SIDEBAR_RENDER_MORE_ROWS" in source
    assert "renderConversationList(_sidebarFullRows)" in source


def test_archive_and_queue_timers_are_stream_failure_fallbacks_only():
    start = SOURCE.index("// Periodic archive refresh.")
    end = SOURCE.index("// Set up the In Group Chat polling", start)
    source = SOURCE[start:end]

    archive_tick = source[source.index("setInterval(_gated('archiveTimes'"):]
    assert "if (_dashboardEventStreamHealthy) return;" in archive_tick

    queue_tick = source[source.index("setInterval(_gated('uxFixesQueueMeta'"):]
    assert "if (_uxqStreamLive) return;" in queue_tick

    sessions_start = SOURCE.index("// Auto-refresh archive/session data")
    sessions_end = SOURCE.index("// The convToolbar new-session input", sessions_start)
    sessions_tick = SOURCE[sessions_start:sessions_end]
    assert "if (_dashboardEventStreamHealthy) return;" in sessions_tick
    assert "ARCHIVE_RECOVERY_INTERVAL_MS" in SOURCE
    assert "setInterval(_gated('archiveRecovery'" in SOURCE
