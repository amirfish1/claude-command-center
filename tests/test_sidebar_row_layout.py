import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestSidebarRowLayout(unittest.TestCase):
    def test_model_icon_is_left_marker_not_right_end_cap(self):
        """Provider icons should live in the row's left rail, not after the time."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        row_start = app_js.index("return '<div class=\"conv-item'")
        row_html = app_js[row_start:app_js.index("+   hoverMetaRowHtml", row_start)]
        main_row = row_html[row_html.index("+ '<div class=\"conv-main-row\">'"):]
        row_end = row_html[row_html.index("+ '<span class=\"conv-row-end\">'"):]

        self.assertIn("+ sessionIconHtml\n            + _nyaChevronHtml", main_row)
        self.assertNotIn("+   sessionIconHtml", row_end)

        item_css = app_css[
            app_css.index(".conv-item {"):
            app_css.index(".conv-item:hover", app_css.index(".conv-item {"))
        ]
        self.assertIn("--conv-icon-left: 10px;", item_css)
        self.assertIn("padding: 6px 12px 6px var(--conv-content-left);", item_css)

        icon_css = app_css[
            app_css.index(".conv-item .conv-session-icon {"):
            app_css.index(".conv-item .conv-title {", app_css.index(".conv-item .conv-session-icon {"))
        ]
        self.assertIn("position: absolute;", icon_css)
        self.assertIn("left: var(--conv-icon-left);", icon_css)
        self.assertIn("top: 50%;", icon_css)
        self.assertIn("transform: translateY(-50%);", icon_css)
        self.assertIn("opacity: 0.88;", icon_css)
        self.assertIn(
            ".conv-item:hover .conv-session-icon,\n"
            "  .conv-item:focus-within .conv-session-icon",
            app_css,
        )
        self.assertNotIn(".conv-item .conv-row-end > .conv-session-icon", app_css)

    def test_repeated_sidebar_rows_render_as_collapsible_groups(self):
        """Adjacent identical titles should fold into an expandable group row."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        self.assertIn("const _renderRowsWithRepeatGroups = (cards, opts = {}) =>", app_js)
        self.assertIn("const _repeatGroupTitleKey = (title) =>", app_js)
        self.assertIn("if (normalized.length > 48) return normalized.slice(0, 32);", app_js)
        self.assertIn("data-role=\"repeat-row-group\"", app_js)
        self.assertIn("data-role=\"repeat-row-group-toggle\"", app_js)
        self.assertIn("ccc-repeat-row-group-expanded:", app_js)
        self.assertIn(
            "_renderRowsWithRepeatGroups(cards, { lifecycleContext: 'active', suppressFolderChip: true })",
            app_js,
        )
        self.assertIn(
            "_renderRowsWithRepeatGroups(cards, { lifecycleContext: 'active', suppressFolderChip: false, quietTitleChrome: true })",
            app_js,
        )
        self.assertIn("_arcChunks.push(sep + _renderRowsWithRepeatGroups(", app_js)
        self.assertIn("type: 'session',\n          card: c,", app_js)
        self.assertIn("lifecycleContext: 'active',\n              suppressFolderChip: false", app_js)

        self.assertIn(".conv-repeat-group-header", app_css)
        self.assertIn(".conv-repeat-group.is-collapsed .conv-repeat-group-body", app_css)
        self.assertIn("display: none;", app_css)

    def test_repeated_sidebar_rows_offer_confirmed_bulk_archive(self):
        """A cluster header archives its exact session set only after confirmation."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn('data-role="repeat-row-group-archive"', app_js)
        self.assertIn('Archive ' + "' + cards.length + ' sessions", app_js)
        self.assertIn("confirm('Archive ' + sessionIds.length +", app_js)
        self.assertIn("ccPostJson('/api/conversations/archive-bulk', {", app_js)
        self.assertIn("session_ids: sessionIds, archived: true", app_js)
        self.assertIn("refreshArchiveData({ force: true })", app_js)

    def test_group_chat_emoji_uses_the_session_icon_rail(self):
        """A chat marker should align with model icons, not reserve a leading gutter."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        row_css = app_css[
            app_css.index("#convList .conv-ingroupchat-row {"):
            app_css.index("#convList .conv-ingroupchat-row.active", app_css.index("#convList .conv-ingroupchat-row {"))
        ]
        icon_css = app_css[
            app_css.index("#convList .conv-ingroupchat-row-icon"):
            app_css.index("/* Collapse chevron", app_css.index("#convList .conv-ingroupchat-row-icon"))
        ]
        collapse_css = app_css[
            app_css.index("#convList .conv-ingroupchat-collapse-btn {"):
            app_css.index("#convList .conv-ingroupchat-collapse-btn:hover", app_css.index("#convList .conv-ingroupchat-collapse-btn {"))
        ]

        self.assertIn("position: relative;", row_css)
        self.assertIn("padding: 5px 14px 5px 63px;", row_css)
        self.assertIn("position: absolute;", icon_css)
        self.assertIn("left: 12px;", icon_css)
        self.assertIn("position: absolute;", collapse_css)
        self.assertIn("left: 36px;", collapse_css)


if __name__ == "__main__":
    unittest.main()
