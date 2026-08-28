import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


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
        self.assertIn('<span class="ver">v5.29.0</span>', landing_page)
        self.assertIn("Simple Mode for the whole fleet", landing_page)

    def test_current_release_screenshot_is_used_on_public_surfaces(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        landing_page = (PROJECT_ROOT / "docs" / "index.html").read_text(
            encoding="utf-8"
        )
        screenshot = PROJECT_ROOT / "docs" / "images" / "ccc-v5-29-orchestration.png"

        self.assertTrue(screenshot.exists())
        self.assertIn("docs/images/ccc-v5-29-orchestration.png", readme)
        self.assertIn("./images/ccc-v5-29-orchestration.png", landing_page)


if __name__ == "__main__":
    unittest.main()
