"""Cmd+F must re-collect matches after the transcript re-renders.

Ranges are live: when a re-render removes a matched text node the browser
moves the range onto the parent and collapses it. The counter kept advancing
while nothing highlighted or scrolled.
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestChatFindStaleRanges(unittest.TestCase):
    def setUp(self):
        self.js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def test_staleness_checks_range_content_not_just_connection(self):
        start = self.js.index("function chatFindRangesStale(root)")
        body = self.js[start:self.js.index("\n    }", start)]
        self.assertIn("r.collapsed", body)
        self.assertIn("r.toString()", body)

    def test_step_rebuilds_stale_ranges(self):
        start = self.js.index("async function chatFindStep(opts)")
        body = self.js[start:self.js.index("refreshChatFind = chatFindStep", start)]
        self.assertIn("chatFindRangesStale(root)", body)

    def test_open_find_bar_watches_for_rerenders(self):
        self.assertIn("_chatFindObserver = new MutationObserver", self.js)
        start = self.js.index("function closeChatFindModal()")
        body = self.js[start:self.js.index("\n    }", start)]
        self.assertIn("chatFindUnwatch()", body)


if __name__ == "__main__":
    unittest.main()
