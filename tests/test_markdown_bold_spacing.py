"""Regression coverage for transcript inline-markdown spacing."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestMarkdownBoldSpacing(unittest.TestCase):
    def test_bold_prose_boundary_uses_non_collapsing_gap(self):
        """A literal space after `**bold**` must remain visible in transcripts."""
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text()

        self.assertIn("md-bold-gap", app_js)
        self.assertIn("gap.replace(/[ \\t]/g, '&nbsp;')", app_js)


if __name__ == "__main__":
    unittest.main()
