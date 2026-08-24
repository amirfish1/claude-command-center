"""Static regression coverage for q2's WatchTower annotation entry point."""

from __future__ import annotations

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQ2Annotate(unittest.TestCase):
    def test_q2_topbar_uses_native_ccc_annotation_controller(self):
        html = (ROOT / "static" / "q2.html").read_text(encoding="utf-8")
        self.assertIn('id="q2AnnotateBtn"', html)
        self.assertIn('src="/static/q2-annotation.js"', html)
        self.assertNotIn('src="/static/annotate-widget.js"', html)
        self.assertNotIn('q2-annotate-bridge.js', html)

    def test_native_picker_visibly_targets_and_persists_q2_context(self):
        controller = (ROOT / "static" / "q2-annotation.js").read_text(encoding="utf-8")
        self.assertIn("window.Q2Annotation", controller)
        self.assertIn("q2-ann-selection", controller)
        self.assertIn("elementFromPoint", controller)
        self.assertIn("pointerEvents = 'none'", controller)
        self.assertIn("event.key === 'Escape'", controller)
        self.assertIn("source: 'ccc'", controller)
        self.assertIn("capture_screen: true", controller)
        self.assertIn("fetch('/api/annotations'", controller)
        self.assertIn("fetch('/api/annotations/ux-fixes-queue'", controller)
        self.assertIn("annotation_id:", controller)


if __name__ == "__main__":
    unittest.main()
