import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _current_version():
    """(full 'X.Y.Z', 'X.Y') of the current release, from pyproject.toml."""
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', text, re.M)
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}", f"{m.group(1)}.{m.group(2)}"


class WhatsNewNoticeStaticTests(unittest.TestCase):
    def test_version_boot_shows_nonblocking_notice_instead_of_modal(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        boot_start = app_js.index("// Fetch version and optionally show")
        boot_end = app_js.index("// ── Manual server restart", boot_start)
        boot = app_js[boot_start:boot_end]

        self.assertIn("whatsNewShowNotice();", boot)
        self.assertNotIn("whatsNewOpenModal();", boot)

    def test_sidebar_menu_opens_whats_new_explicitly(self):
        index_html = (
            PROJECT_ROOT / "static" / "index.html"
        ).read_text(encoding="utf-8")
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="cccWhatsNewLink"', index_html)
        self.assertIn(
            "$cccWhatsNewLink.addEventListener('click', (e) => {",
            app_js,
        )
        self.assertIn("whatsNewOpenModal();", app_js)

    def test_whats_new_lives_in_the_overflow_menu_not_the_brand_row(self):
        index_html = (
            PROJECT_ROOT / "static" / "index.html"
        ).read_text(encoding="utf-8")
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        title_start = index_html.index('<div class="sh-title-row">')
        title_end = index_html.index('<div class="sh-service-badges">', title_start)
        title_row = index_html[title_start:title_end]

        self.assertIn('id="cccVersionLabel"', title_row)
        self.assertNotIn('id="cccWhatsNewLink"', title_row)
        self.assertIn('id="cccWhatsNewLink"', index_html)
        self.assertNotIn("_moveToHome('cccWhatsNewLink'", app_js)

    def test_current_release_story_leads_the_dashboard_and_landing_page(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        landing_page = (PROJECT_ROOT / "docs" / "index.html").read_text(
            encoding="utf-8"
        )

        features_start = app_js.index("const WHATS_NEW_FEATURES = [")
        features_end = app_js.index("let whatsNewActiveId", features_start)
        features = app_js[features_start:features_end]

        self.assertIn("simple-mode-for-the-whole-fleet", features)
        self.assertIn("orchestration-that-shows-its-work", features)
        self.assertIn("every-model-in-one-place", features)
        self.assertIn("find-recent-work-fast", features)
        self.assertNotIn("mobile-responsiveness-pass", features)
        full, mm = _current_version()
        self.assertIn(f'<span class="ver">v{full}</span>', landing_page)
        self.assertIn(f'<span class="version">v{mm}</span>', landing_page)
        self.assertIn(f'<span class="badge">v{mm}</span>', landing_page)
        dmg_version = ET.parse(PROJECT_ROOT / "docs" / "appcast.xml").findtext(
            "./channel/item/{http://www.andymatuschak.org/xml-namespaces/sparkle}version"
        )
        self.assertIn(f"macOS app v{dmg_version} &middot; source and Homebrew v{full}", landing_page)

    def test_current_release_screenshot_is_used_on_public_surfaces(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        landing_page = (PROJECT_ROOT / "docs" / "index.html").read_text(
            encoding="utf-8"
        )
        # A screenshot describes its capture, which can predate the release.
        match = re.search(r'src="\./(images/ccc-v[0-9-]+-hero\.png)"', landing_page)
        self.assertIsNotNone(match)
        rel = match.group(1)
        self.assertTrue((PROJECT_ROOT / "docs" / rel).exists())
        self.assertIn(f"docs/{rel}", readme)
        captured = re.search(r"ccc-v(\d+)-(\d+)-hero", rel)
        self.assertIn(f"CCC v{captured.group(1)}.{captured.group(2)} showing", landing_page)


if __name__ == "__main__":
    unittest.main()
