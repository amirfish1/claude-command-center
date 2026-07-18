"""Regression checks for the standalone Puppeteer snapshot harness."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SnapshotHarnessTests(unittest.TestCase):
    def test_snapshot_always_closes_browser_and_bounds_navigation(self):
        source = (ROOT / "snapshot.js").read_text()

        self.assertIn("try {", source)
        self.assertIn("finally {", source)
        self.assertIn("await browser.close()", source)
        self.assertIn("Promise.race", source)
        self.assertIn("snapshot exceeded", source)
        self.assertIn("page.goto(url, { waitUntil: 'load', timeout: timeoutMs })", source)

    def test_snapshot_allows_a_minute_for_a_busy_local_dashboard(self):
        source = (ROOT / "snapshot.js").read_text()

        self.assertIn("const DEFAULT_TIMEOUT_MS = 60_000;", source)

    def test_puppeteer_launchers_share_cached_headless_shell_selection(self):
        snapshot_source = (ROOT / "snapshot.js").read_text()
        config_source = (ROOT / ".puppeteerrc.cjs").read_text()

        helper_import = "require('./puppeteer-browser-config.js')"
        self.assertIn(helper_import, snapshot_source)
        self.assertIn(helper_import, config_source)


if __name__ == "__main__":
    unittest.main()
