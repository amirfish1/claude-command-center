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

    def test_notice_can_open_or_dismiss_whats_new_explicitly(self):
        index_html = (
            PROJECT_ROOT / "static" / "index.html"
        ).read_text(encoding="utf-8")
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="whatsNewNotice"', index_html)
        self.assertIn('id="whatsNewNoticeOpen"', index_html)
        self.assertIn('id="whatsNewNoticeDismiss"', index_html)
        self.assertIn("$whatsNewNoticeOpen.addEventListener('click', whatsNewOpenModal);", app_js)
        self.assertIn(
            "$whatsNewNoticeDismiss.addEventListener('click', whatsNewDismissNotice);",
            app_js,
        )
        dismiss_start = app_js.index("function whatsNewDismissNotice()")
        dismiss_end = app_js.index("\n  function ", dismiss_start + 1)
        dismiss = app_js[dismiss_start:dismiss_end]
        self.assertIn("ccc-last-seen-version", dismiss)
        self.assertIn("classList.remove('visible')", dismiss)
        self.assertNotIn("Don't show on startup", index_html)

    def test_notice_badge_lives_beside_version_not_in_crowded_alert_strip(self):
        index_html = (
            PROJECT_ROOT / "static" / "index.html"
        ).read_text(encoding="utf-8")
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        title_start = index_html.index('<div class="sh-title-row">')
        title_end = index_html.index('<div class="sh-service-badges">', title_start)
        title_row = index_html[title_start:title_end]

        self.assertIn('id="cccVersionLabel"', title_row)
        self.assertIn('id="whatsNewNotice"', title_row)
        self.assertNotIn("_moveToHome('whatsNewNotice'", app_js)


if __name__ == "__main__":
    unittest.main()
