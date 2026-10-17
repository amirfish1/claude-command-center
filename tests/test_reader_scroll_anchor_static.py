"""The conversation reader must keep its position when content above grows (iOS has no CSS scroll anchoring)."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestReaderScrollAnchorStatic(unittest.TestCase):
    def test_reader_compensates_growth_above_viewport(self):
        js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = js.index("function _observeConvEventSizes(view, mutations)")
        body = js[start:js.index("function _topVisibleAnchor", start)]
        self.assertIn("new ResizeObserver", body)
        self.assertIn("view.scrollTop += shift", body)
        self.assertIn("_pinnedToBottom", body)
        self.assertIn("_observeConvEventSizes(view, mutations);", js)

    def test_native_anchoring_off_for_reader(self):
        css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn(".conversations-view { overflow-anchor: none; }", css)


if __name__ == "__main__":
    unittest.main()
