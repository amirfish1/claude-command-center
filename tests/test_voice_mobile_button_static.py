"""Tests for the mobile voice call button toggle option."""

import pathlib
import unittest

from ccc_server import realtime_voice as rv

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / "static" / name).read_text(encoding="utf-8")


class TestVoiceMobileButtonStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = read("index.html")
        cls.app = read("app.js")
        cls.css = read("app.css")

    def test_markup_and_toggles_present(self):
        # Settings Layout & View toggle
        self.assertIn('id="settingsVoiceMobileToggle"', self.index)
        self.assertIn('data-voice-mobile-toggle', self.index)
        self.assertIn('Show voice call button on mobile', self.index)

        # Simple mode settings card
        self.assertIn('id="simpleVoiceMobileRow"', self.index)
        self.assertIn('data-simple-voice-mobile="on"', self.index)
        self.assertIn('data-simple-voice-mobile="off"', self.index)

        # Voice modal checkbox
        self.assertIn('id="voiceShowMobileBtn"', self.index)

    def test_css_hides_button_when_class_applied(self):
        self.assertIn("body.hide-voice-mobile-btn .voice-mobile-btn", self.css)
        self.assertIn("display: none !important", self.css)

    def test_app_js_persists_and_applies_pref(self):
        self.assertIn("ccc-voice-mobile-btn", self.app)
        self.assertIn("function getVoiceMobileBtnPref()", self.app)
        self.assertIn("function applyVoiceMobileBtn(", self.app)
        self.assertIn("function setVoiceMobileBtnPref(", self.app)
        self.assertIn("data-voice-mobile-toggle", self.app)
        self.assertIn("data-simple-voice-mobile", self.app)

    def test_index_html_has_foit_guard(self):
        self.assertIn("localStorage.getItem('ccc-voice-mobile-btn') === '0'", self.index)
        self.assertIn("document.body.classList.add('hide-voice-mobile-btn')", self.index)

    def test_voice_config_default_and_sanitize(self):
        cfg = rv._default_config()
        self.assertIn("show_mobile_btn", cfg)
        self.assertTrue(cfg["show_mobile_btn"])

        sanitized = rv._config_sanitize({"show_mobile_btn": False})
        self.assertFalse(sanitized["show_mobile_btn"])

        sanitized_true = rv._config_sanitize({"show_mobile_btn": True})
        self.assertTrue(sanitized_true["show_mobile_btn"])


if __name__ == "__main__":
    unittest.main()
