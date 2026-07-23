"""Static regression coverage for the Open ask visibility preference."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestOpenAskPreference(unittest.TestCase):
    def test_settings_exposes_a_persistent_open_ask_toggle(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="settingsOpenAskToggle"', html)
        self.assertIn('data-view-openask-toggle', html)
        self.assertIn('aria-label="Show Open ask"', html)
        self.assertIn("function getOpenAskPref()", app_js)
        self.assertIn("localStorage.getItem('ccc-view-open-ask')", app_js)
        self.assertIn("const openAskOn = getOpenAskPref() !== 'hide';", app_js)
        self.assertIn("const openAskToggle = e.target.closest('[data-view-openask-toggle]');", app_js)
        self.assertIn("localStorage.setItem('ccc-view-open-ask', next);", app_js)
        self.assertIn("localStorage.removeItem('ccc-view-open-ask');", app_js)


    def test_hidden_open_ask_preference_skips_the_sidebar_section(self):
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const _openAskHtml = getOpenAskPref() === 'hide' ? ''", app_js)

    def test_original_ask_panel_can_be_dismissed_on_mobile(self):
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn("sticky.hidden = true;", app_js)
        self.assertIn("firstUser.classList.remove('is-pinned-in-sticky');", app_js)
        self.assertIn("body.status-pos-right .conv-sticky-header[hidden]", app_css)
        self.assertIn(".conv-sticky-header[hidden] { display: none !important; }", app_css)


if __name__ == "__main__":
    unittest.main()
