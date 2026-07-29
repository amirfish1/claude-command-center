"""Static regression coverage for q2's WatchTower annotation entry point."""

from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQ2Annotate(unittest.TestCase):
    def test_q2_topbar_uses_the_ccc_annotation_widget(self):
        html = (ROOT / "static" / "q2.html").read_text(encoding="utf-8")
        self.assertIn('id="q2AnnotateBtn"', html)
        self.assertIn("window.WT_ANNOTATE = { queue: 'CCC'", html)
        self.assertIn('src="/static/annotate-widget.js"', html)
        self.assertIn('q2-annotate-bridge.js', html)
        bridge = (ROOT / "static" / "q2-annotate-bridge.js").read_text(encoding="utf-8")
        self.assertIn("widgetButton.style.display = 'none';", bridge)

    def test_widget_builds_selectors_by_traversing_to_a_stable_ancestor(self):
        widget = (ROOT / "static" / "annotate-widget.js").read_text(encoding="utf-8")
        self.assertIn("while (current && current !== document.body)", widget)
        self.assertIn("segments.unshift", widget)


if __name__ == "__main__":
    unittest.main()
