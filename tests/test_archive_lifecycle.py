"""Regression guards for asynchronous sidebar archive actions."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class ArchiveLifecycleTests(unittest.TestCase):
    def test_archive_button_shows_pending_feedback_while_request_is_in_flight(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        app_css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn("btn.classList.add('is-pending');", app_js)
        self.assertIn("btn.setAttribute('aria-busy', 'true');", app_js)
        self.assertIn("btn.classList.remove('is-pending');", app_js)
        self.assertIn(".conv-item .conv-archive-btn.is-pending", app_css)
        self.assertIn("animation: ccc-spin", app_css)


if __name__ == "__main__":
    unittest.main()
