import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = PROJECT_ROOT / "scripts" / "verify-whats-new-notice.js"


class WhatsNewNoticeBrowserHarnessTests(unittest.TestCase):
    def test_verifier_checks_startup_first_click_and_explicit_modal_open(self):
        self.assertTrue(VERIFIER.exists(), "What's New browser verifier is missing")
        source = VERIFIER.read_text(encoding="utf-8")

        self.assertIn("require('../require-puppeteer.js')", source)
        self.assertIn("CCC_WHATS_NEW_NOTICE_URL", source)
        self.assertIn("ccc-tour-done", source)
        self.assertIn("ccc-last-seen-version", source)
        self.assertIn("#sidebarNewGroupChatBtn", source)
        self.assertIn("window.prompt", source)
        self.assertIn("#whatsNewNoticeOpen", source)
        self.assertIn("#whatsNewBackdrop", source)
        self.assertIn("page.screenshot", source)
        self.assertIn("page.waitForFunction", source)
        self.assertNotIn("page.waitForTimeout", source)
        self.assertIn("finally", source)
        self.assertIn("await browser.close()", source)

    def test_badge_controls_meet_compact_target_size(self):
        app_css = (
            PROJECT_ROOT / "static" / "app.css"
        ).read_text(encoding="utf-8")

        self.assertIn("min-height: 24px;", app_css)
        self.assertIn("min-width: 24px;", app_css)
        self.assertIn("outline-offset: -2px;", app_css)


if __name__ == "__main__":
    unittest.main()
