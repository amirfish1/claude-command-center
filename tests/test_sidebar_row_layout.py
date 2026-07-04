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
        self.assertIn("opacity: 0.5;", icon_css)
        self.assertIn(
            ".conv-item:hover .conv-session-icon,\n"
            "  .conv-item:focus-within .conv-session-icon",
            app_css,
        )
        self.assertNotIn(".conv-item .conv-row-end > .conv-session-icon", app_css)


if __name__ == "__main__":
    unittest.main()
