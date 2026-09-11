"""Drain-paused banner: the leaked worker-restart drain of 2026-08-06 parked
31 messages for 4 hours with zero visible signal -- the only readouts lived
inside Settings > Maintenance. The banner makes a paused control-plane drain
unmissable from any view, so these assertions pin the wiring: the element
exists page-globally, the 20s control-plane poll feeds it, and both the
"dispatching" and "not dispatching" renderer paths drive it."""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestDrainPausedBanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

    def test_banner_element_is_page_global(self):
        self.assertIn('id="drainPausedBanner"', self.html)
        self.assertIn('id="drainPausedBannerText"', self.html)
        self.assertIn('id="drainPausedResumeBtn"', self.html)
        self.assertIn('id="drainPausedDetailsBtn"', self.html)

    def test_renderer_updates_banner_on_every_status_path(self):
        fn = self.js[self.js.index("function renderControlPlaneStatus"):]
        fn = fn[:fn.index("async function refreshControlPlaneStatus")]
        # null/unavailable, capabilities-missing, and the healthy tail must
        # all drive the banner -- a path that skips it would leave a stale
        # "paused" banner up after the drain clears.
        self.assertGreaterEqual(fn.count("renderDrainPausedBanner()"), 3)

    def test_banner_stacks_below_injection_health_banner(self):
        self.assertIn(
            "#injectionHealthBanner:not([hidden]) ~ .drain-paused-banner",
            self.css,
        )


if __name__ == "__main__":
    unittest.main()
