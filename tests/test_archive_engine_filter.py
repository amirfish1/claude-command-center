"""Static regression coverage for the All-tab engine icon filter."""

from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"
APP_CSS = Path(__file__).resolve().parents[1] / "static" / "app.css"


def _source(path):
    return path.read_text(encoding="utf-8")


def test_archive_toolbar_has_icon_only_claude_codex_kimi_filter():
    app_js = _source(APP_JS)

    assert 'data-role="archived-engine-filter"' in app_js
    assert 'data-archive-engine="' in app_js
    for engine in ("claude", "codex", "kimi"):
        assert f"_arcEngineButton('{engine}'," in app_js
        assert f"getEngineSvg('{engine}')" in app_js


def test_archive_engine_filter_is_exclusive_reversible_and_persistent():
    app_js = _source(APP_JS)

    assert "const ARCHIVE_ENGINE_FILTER_KEY = 'ccc-archive-engine-filter';" in app_js
    assert "function _archiveEngineFilter()" in app_js
    assert "current === value ? '' : value" in app_js
    assert "localStorage.removeItem(ARCHIVE_ENGINE_FILTER_KEY)" in app_js
    assert "localStorage.setItem(ARCHIVE_ENGINE_FILTER_KEY, next)" in app_js


def test_archive_engine_filter_uses_stable_list_delegation():
    app_js = _source(APP_JS)

    assert "$convList._archiveEngineFilterWired" in app_js
    assert "$convList.addEventListener('click', _handleArchiveEngineFilterClick)" in app_js


def test_archive_engine_filter_applies_before_all_tab_grouping():
    app_js = _source(APP_JS)

    filter_pos = app_js.index("const _allTabConvs = _allTabUnfilteredConvs.filter")
    lane_pos = app_js.index("const _allTabCodingConvs = _allTabConvs.filter")
    assert filter_pos < lane_pos
    assert "_archiveEngineAllowsRow(c, _arcEngineFilter)" in app_js
    assert "const _allTabGroupChatItems = _arcEngineFilter ? []" in app_js
    assert "No ' + escapeHtml(_arcEngineFilterLabel) + ' sessions." in app_js


def test_archive_engine_filter_keeps_coding_worker_message_lanes_selectable():
    app_js = _source(APP_JS)

    assert "_allTabUnfilteredConvs.forEach(c => {" in app_js
    assert "const _allTabUnfilteredLanes = new Set(" in app_js
    assert "_allTabUnfilteredConvs.map(c => _allTabLaneFor(c))" in app_js
    assert "_allTabUnfilteredLanes.has('workers')" in app_js
    assert "_allTabUnfilteredLanes.has('messages')" in app_js
    assert "const _allTabHasHermesSplit = !_arcEngineFilter && (" not in app_js


def test_archive_engine_filter_has_compact_accessible_icon_styles():
    app_css = _source(APP_CSS)

    assert ".conv-archived-engine-filter" in app_css
    assert ".conv-archived-engine-btn" in app_css
    assert ".conv-archived-engine-btn .conv-session-svg" in app_css
    assert ".conv-archived-engine-btn.is-active" in app_css
