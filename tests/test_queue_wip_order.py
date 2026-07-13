"""Regression coverage for the Queue panel's top-level ticket ordering."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueWipOrder(unittest.TestCase):
    def test_wip_then_blocked_then_open_then_closed(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            "const _statusRank = s => (s === 'in_progress' ? 0 : s === 'blocked' ? 1 : s === 'open' ? 2 : 3);",
            app_js,
        )

    def test_newly_fetched_queue_items_get_a_temporary_highlight(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        app_css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn("const _UXQ_NEW_ITEM_GLOW_MS = 4500;", app_js)
        self.assertIn("_uxqNewItemExpires", app_js)
        self.assertIn("fq-new-item", app_js)
        self.assertIn(".fq-row.fq-new-item", app_css)
        self.assertIn("@keyframes fq-new-item-glow", app_css)


if __name__ == "__main__":
    unittest.main()
