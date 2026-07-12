"""Regression coverage for the Queue panel's top-level ticket ordering."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueWipOrder(unittest.TestCase):
    def test_in_progress_tickets_sort_ahead_of_open_tickets(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            "const _statusRank = s => (s === 'in_progress' ? 0 : s === 'open' ? 1 : 2);",
            app_js,
        )


if __name__ == "__main__":
    unittest.main()
