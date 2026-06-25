import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestConversationHistoryPagingStatic(unittest.TestCase):
    def test_hover_prefetch_uses_tail_window(self):
        """Hover warming must not full-parse huge transcripts."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        prefetch_js = app_js[
            app_js.index("function _convPrefetchSchedule(id)"):
            app_js.index("function _convPrefetchCancel(id)", app_js.index("function _convPrefetchSchedule(id)"))
        ]

        self.assertIn("?tail=' + CONV_TAIL_LINES", prefetch_js)
        self.assertNotIn("?after=0", prefetch_js)

    def test_load_earlier_fetches_previous_window_not_full_transcript(self):
        """Loading earlier history should prepend one bounded window."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        load_js = app_js[
            app_js.index("async function loadEarlier()"):
            app_js.index("banner.addEventListener('click', loadEarlier);", app_js.index("async function loadEarlier()"))
        ]
        fetch_js = app_js[
            app_js.index("async function fetchConversationEvents(paneId)"):
            app_js.index("Object.defineProperty(window, '_firstUserMsgRendered'", app_js.index("async function fetchConversationEvents(paneId)"))
        ]

        self.assertIn("pane.loadBeforeLine =", load_js)
        self.assertNotIn("pane.wantFull = true; pane.lastLine = 0;", load_js)
        self.assertIn("?before=' + encodeURIComponent(_loadBefore)", fetch_js)
        self.assertIn("const _loadingEarlier = !!_loadBefore;", fetch_js)
        self.assertIn("_prependConversationEvents(data.events, fetchPaneId);", fetch_js)
        self.assertIn("pane.firstLine = data.first_line || pane.firstLine || 0;", fetch_js)


if __name__ == "__main__":
    unittest.main()
