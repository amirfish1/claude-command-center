"""Regression guard for the archived-session control bar."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestArchivedToolbarLayout(unittest.TestCase):
    def test_archived_controls_stay_on_a_single_scrollable_row(self):
        css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")
        start = css.index("#convList .conv-archived-tools-right {")
        end = css.index("#convList .conv-archived-tools-right > * {", start)
        toolbar_css = css[start:end]

        self.assertIn("flex-wrap: nowrap;", toolbar_css)
        self.assertIn("overflow-x: auto;", toolbar_css)

