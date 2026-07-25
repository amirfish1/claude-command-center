"""Regression checks for full-width web-UI transcript entries."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestWebUiConversationWidth(unittest.TestCase):
    def test_transcript_entries_use_the_available_pane_width(self):
        css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
        selector = ".conv-pane.is-webui-session .conversations-view > .kimi-turn,"
        start = css.index(selector)
        block = css[start:css.index("}", start) + 1]

        self.assertIn("width: 100%", block)
        self.assertNotIn("max-width: 760px", block)
        self.assertNotIn("margin-left: auto", block)
        self.assertNotIn("margin-right: auto", block)
