"""Regression guards for new-session object picker behavior."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class NewSessionObjectPickerTest(unittest.TestCase):
    def test_picker_opens_unfiltered_and_prioritizes_selected_repo(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function newSessionObjectRepoMatchIds()", app_js)
        self.assertIn("repoMatched: repoMatchIds.has(o.id)", app_js)
        self.assertIn("a.repoMatched === b.repoMatched", app_js)
        self.assertIn("renderNewSessionObjectMenu('');\n      input.select();", app_js)


if __name__ == "__main__":
    unittest.main()
