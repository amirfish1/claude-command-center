"""Regression coverage for queue availability during archive hydration."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueBootHydration(unittest.TestCase):
    def test_archive_mode_starts_queue_hydration_before_waiting_for_archive(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = app_js.index("  async function setArchiveMode()")
        end = app_js.index("\n  (function wireArchiveMode()", start)
        archive_mode = app_js[start:end]

        self.assertIn("_renderQueuePanel();", archive_mode)
        self.assertLess(
            archive_mode.index("_renderQueuePanel();"),
            archive_mode.index("await refreshArchiveData();"),
        )


if __name__ == "__main__":
    unittest.main()
