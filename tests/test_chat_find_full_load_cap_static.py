"""Cmd+F find must not load a huge session's whole transcript (it froze the tab)."""

from tests.dashboard_source import dashboard_source
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestChatFindFullLoadCap(unittest.TestCase):
    def test_full_load_is_gated_by_hidden_line_count(self):
        js = dashboard_source(ROOT / "static" / "app.js")
        start = js.index("async function chatFindLoadFullTranscript()")
        body = js[start:js.index("pane.wantFull = true;", start)]
        self.assertIn("pane.firstLine", body)
        self.assertIn("CHAT_FIND_FULL_LOAD_MAX_LINES", body)


if __name__ == "__main__":
    unittest.main()
