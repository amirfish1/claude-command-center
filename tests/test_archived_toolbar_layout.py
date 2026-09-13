"""Regression guard for the archived-session control bar."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestArchivedToolbarLayout(unittest.TestCase):
    def test_archived_controls_use_the_full_toolbar_without_horizontal_scrolling(self):
        css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = css.index("#convList .conv-archived-tools-right {")
        end = css.index("#convList .conv-archived-tools-right > * {", start)
        toolbar_css = css[start:end]

        self.assertIn("justify-content: space-between;", toolbar_css)
        self.assertIn("flex-wrap: wrap;", toolbar_css)
        self.assertNotIn("overflow-x: auto;", toolbar_css)
        self.assertIn("const _arcToolsLeft = _arcExpandAllToggle", app_js)
