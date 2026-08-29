"""Tests for the quick model picker above the composer."""

import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import server

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX_HTML = PROJECT_ROOT / "static" / "index.html"
APP_CSS = PROJECT_ROOT / "static" / "app.css"
APP_JS = PROJECT_ROOT / "static" / "app.js"


class TestModelPickerContracts(unittest.TestCase):
    def test_dom_and_css_contracts(self):
        index_html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn('id="convModelPickerStrip"', index_html)
        self.assertIn('id="nsModelPickerPills"', index_html)

        app_css = APP_CSS.read_text(encoding="utf-8")
        self.assertIn('.conv-model-picker-strip', app_css)
        self.assertIn('.conv-pane > .conv-model-picker-strip', app_css)

        app_js = APP_JS.read_text(encoding="utf-8")
        self.assertIn('fetchModelPickerPicksFromServer', app_js)
        self.assertIn('/api/model-picker/picks', app_js)
        self.assertIn('/api/model-picker/record', app_js)
        self.assertIn('convModelPickerStrip', app_js)

    def test_record_and_get_model_picker_picks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_history = pathlib.Path(tmpdir) / "model-picker-history.json"
            with patch.object(server, "MODEL_PICKER_HISTORY_FILE", tmp_history),                  patch.object(server, "_mine_real_model_history_last_7_days", return_value=[]):
                server.record_model_picker_pick("claude", "opus-5", "high")
                server.record_model_picker_pick("claude", "opus-5", "high")
                server.record_model_picker_pick("codex", "gpt-5.6-terra", "")

                picks = server.get_model_picker_picks()
                self.assertGreaterEqual(len(picks), 2)
                top = picks[0]
                self.assertEqual(top["engine"], "claude")
                self.assertEqual(top["model"], "opus-5")
                self.assertEqual(top["effort"], "high")
                self.assertEqual(top["count"], 2)

                second = picks[1]
                self.assertEqual(second["engine"], "codex")
                self.assertEqual(second["model"], "gpt-5.6-terra")


if __name__ == "__main__":
    unittest.main()
