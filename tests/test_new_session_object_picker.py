"""Regression guards for new-session object picker behavior."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class NewSessionObjectPickerTest(unittest.TestCase):
    def test_picker_opens_unfiltered_and_prioritizes_selected_repo(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function newSessionObjectRepoMatchIds", app_js)
        self.assertIn("repoMatched: repoMatchIds.has(o.id)", app_js)
        self.assertIn("a.repoMatched === b.repoMatched", app_js)
        self.assertIn("renderNewSessionObjectMenu('');\n      input.select();", app_js)

        # Model picker regression checks
        self.assertIn("function recordSpawnChoice", app_js)
        self.assertIn("function getTopSpawnPicks", app_js)
        self.assertIn("function renderNsModelPickerPills", app_js)
        # The pills now mount into a static container in index.html and
        # render as orch-tier-chip buttons.
        index_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="nsModelPickerPills"', index_html)
        self.assertIn("document.getElementById('nsModelPickerPills')", app_js)
        self.assertIn("'<button type=\"button\" class=\"orch-tier-chip'", app_js)


if __name__ == "__main__":
    unittest.main()
