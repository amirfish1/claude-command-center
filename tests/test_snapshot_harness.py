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


if __name__ == "__main__":
    unittest.main()
