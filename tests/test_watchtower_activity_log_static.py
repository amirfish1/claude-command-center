"""Static regression coverage for WatchTower activity-log ticket references."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestWatchtowerActivityLogStatic(unittest.TestCase):
    def test_activity_log_reference_column_fits_long_project_ticket_refs(self):
        app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn(".wl-ref-col { flex: 0 1 auto; max-width: 124px;", app_css)

    def test_activity_log_groups_its_first_four_fields_in_one_metadata_column(self):
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn("'<span class=\"wl-meta\">'", app_js)
        self.assertIn(".wl-meta {", app_css)
