"""Regression coverage for Queue startup independent of archive hydration."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueBootstrap(unittest.TestCase):
    def test_sidebar_queue_bootstraps_before_archive_hydration(self):
        """A saved Queue sidebar must populate its scope without waiting for archive data."""
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        archive_boot = app_js[
            app_js.index("(function wireArchiveMode()"):
            app_js.index("// Periodic archive refresh.", app_js.index("(function wireArchiveMode()"))
        ]

        self.assertIn("_sharedQueuePanelHost === 'sidebar'", archive_boot)
        self.assertIn("_renderQueuePanel()", archive_boot)
        self.assertLess(
            archive_boot.index("_renderQueuePanel()"),
            archive_boot.index("_firstSessionsLoaded.then(() => setArchiveMode())"),
        )


if __name__ == "__main__":
    unittest.main()
