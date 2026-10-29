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

    assert "_boundSidebarRowsForRender(convs, _sidebarRenderRowLimit" in source
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


def test_render_budget_is_spent_on_the_open_tabs_lane_and_window():
    # Taking rows in server order filled the 240-row budget with worker and
    # out-of-window rows the Coding tab then hid (2 sessions shown, 3,800
    # "not rendered yet").
    start = SOURCE.index("function _boundSidebarRowsForRender")
    end = SOURCE.index("function renderConversationList", start)
    source = SOURCE[start:end]

    assert "_sidebarBudgetLaneGuess(row, byId) === tab" in source
    assert "_archiveWindowAllowsRow(row, cutoff)" in source
    assert "(b.inLane - a.inLane) || (b.inWindow - a.inWindow) || (b.ts - a.ts)" in source
    assert "entry.inLane && entry.inWindow" in source
    assert "_WT_LAUNCH_PROMPT_RE.test(" in SOURCE[SOURCE.index("function _sidebarBudgetNaturalLane"):start]


def _fn_body(name):
    start = SOURCE.index("  function " + name + "(")
    end = SOURCE.index("\n  }\n", start)
    return SOURCE[start:end]


def test_per_row_render_helpers_do_not_allocate_per_call():
    # CCC-121: these run per row (11k+ with window=all) on every sidebar
    # render. A throwaway <div> per escapeHtml call, a default `new Set()` per
    # lane guess, and arrays + spread per window timestamp were the top
    # self-time in the forced-render profile.
    assert "createElement" not in _fn_body("escapeHtml")
    assert "new Set()" not in SOURCE[SOURCE.index("  function _sidebarBudgetLaneGuess("):].split("{", 1)[0]
    ts_body = _fn_body("_archiveWindowRowTs")
    assert "...timestamps" not in ts_body and ".map(" not in ts_body
