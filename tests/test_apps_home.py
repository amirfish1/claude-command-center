"""Apps home page: built-in rail entry, served page and assets."""
import re
import unittest
from pathlib import Path

import server

STATIC = Path(server.__file__).parent / "static"


class AppsHomeTests(unittest.TestCase):
    def test_registered_first_in_rail(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(server, "_apps_config_path", lambda: Path(d) / "none.json"), \
                mock.patch.object(server, "USER_APPS_DIR", Path(d) / "apps"):
            apps = server._resolve_apps()
        self.assertEqual(apps[0]["id"], "apps")
        self.assertTrue(apps[0]["builtin"])
        self.assertEqual(apps[0]["nav"], "/apps")

    def test_route_present(self):
        src = Path(server.__file__).read_text()
        self.assertIn('path in ("/apps", "/apps.html")', src)

    def test_page_assets_exist_and_linked(self):
        html = (STATIC / "apps-home.html").read_text()
        for f in ("apps-home.css", "apps-home.js"):
            self.assertTrue((STATIC / f).is_file())
            self.assertIn("/static/" + f, html)

    def test_page_uses_documented_hooks(self):
        js = (STATIC / "apps-home.js").read_text()
        for needle in ("/api/apps/routines", "/preview.png", "/applications#add",
                       "app-concierge.js", "pgrip"):
            self.assertIn(needle, js + (STATIC / "apps-home.html").read_text())

    def test_both_themes_styled(self):
        css = (STATIC / "apps-home.css").read_text()
        self.assertIn('[data-theme="light"]', css)
        self.assertIsNone(re.search(r"/Users/|/home/", css + (STATIC / "apps-home.js").read_text()))


if __name__ == "__main__":
    unittest.main()
