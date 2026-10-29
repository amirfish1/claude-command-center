"""Jobs tab is reachable from the phone bottom nav (static wiring checks)."""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text()
JS = (ROOT / "static" / "app.js").read_text()
CSS = (ROOT / "static" / "app.css").read_text()


class JobsMobileNavTest(unittest.TestCase):
    def test_bottom_nav_has_advanced_jobs_button(self):
        self.assertRegex(
            HTML, r'data-mobile-nav="jobs"\s+data-nav-chrome="advanced"')

    def test_nav_handler_and_active_state_know_jobs(self):
        self.assertIn(
            "dest === 'coding' || dest === 'workers' || dest === 'queues' || dest === 'jobs'", JS)
        self.assertIn("tab !== 'queues' && tab !== 'jobs'", JS)
        self.assertIn("tab === 'jobs' ? 'jobs'", JS)

    def test_mobile_css_tap_targets_and_dialog(self):
        for sel in (".jobs-seg-btn", ".jobs-sort-toggle .grouping-opt", ".job-live",
                    ".jobs-add-card"):
            self.assertRegex(CSS, r"has-mobile-bottom-nav[^{}]*" + re.escape(sel))
        self.assertIn("max-height: calc(100dvh - 32px)", CSS)


if __name__ == "__main__":
    unittest.main()
