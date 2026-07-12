"""Static regression coverage for WatchTower activity-log ticket references."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestWatchtowerActivityLogStatic(unittest.TestCase):
    def test_activity_log_reference_column_fits_long_project_ticket_refs(self):
        app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn(".wl-ref-col { flex-shrink: 0; width: 124px;", app_css)
