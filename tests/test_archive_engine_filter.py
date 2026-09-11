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


def test_archive_engine_filter_keeps_top_level_lanes_available():
    app_js = _source(APP_JS)

    assert "const _allTabCodingConvs = _allTabConvs.filter" in app_js
    assert "const _allTabWorkerConvs = _allTabConvs.filter" in app_js
    assert "const _allTabHermesMessageConvs = _allTabConvs.filter" in app_js
    assert "const _topLevelLaneOverride" in app_js
    assert "const _allTabMainConvs = _allTabView === 'workers'" in app_js


def test_archive_engine_filter_has_compact_accessible_icon_styles():
    app_css = _source(APP_CSS)

    assert ".conv-archived-engine-filter" in app_css
    assert ".conv-archived-engine-btn" in app_css
    assert ".conv-archived-engine-btn .conv-session-svg" in app_css
    assert ".conv-archived-engine-btn.is-active" in app_css


def test_archive_engine_filter_options_open_right_from_trigger_in_narrow_rail():
    app_css = _source(APP_CSS)
    options_start = app_css.index(".conv-archived-engine-options {")
    options_end = app_css.index(".conv-archived-engine-filter.is-expanded", options_start)
    options_css = app_css[options_start:options_end]

    # The All-view toolbar can place this control near the rail's left edge.
    # Anchoring the popup's right edge there puts its first engine choices
    # outside the viewport; anchor its left edge to the trigger instead.
    assert "left: 0;" in options_css
    assert "right: 0;" not in options_css
