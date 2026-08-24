"""Regression coverage for object buckets in Current sessions."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class CurrentSessionsObjectGroupsTest(unittest.TestCase):
    def test_object_bucket_has_a_persistent_collapse_control(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-role="current-object-group-toggle"', app_js)
        self.assertIn("ccc-current-object-group-collapsed:", app_js)
        self.assertIn("$currentObjectGroupToggle", app_js)


if __name__ == "__main__":
    unittest.main()
