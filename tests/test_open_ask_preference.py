"""Static regression coverage for removing the Open asks UI."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestOpenAskRemoval(unittest.TestCase):
    def test_open_asks_are_not_rendered_or_configurable(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('id="settingsOpenAskToggle"', html)
        self.assertNotIn('data-view-openask-toggle', html)
        self.assertNotIn('aria-label="Show Open ask"', html)
        self.assertNotIn("function getOpenAskPref()", app_js)
        self.assertNotIn("ccc-view-open-ask", app_js)
        self.assertNotIn("kind: 'openask', label: 'Open asks'", app_js)
        self.assertNotIn("const _openAskHtml", app_js)

    def test_original_ask_panel_can_be_dismissed_on_mobile(self):
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn("sticky.hidden = true;", app_js)
        self.assertIn("firstUser.classList.remove('is-pinned-in-sticky');", app_js)
        self.assertIn("body.status-pos-right .conv-sticky-header[hidden]", app_css)
        self.assertIn(".conv-sticky-header[hidden] { display: none !important; }", app_css)


if __name__ == "__main__":
    unittest.main()
