import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestConversationHistoryPagingStatic(unittest.TestCase):
    def test_boot_prefetches_saved_conversation_tails_before_loading_sidebar(self):
        """Restored transcripts should warm before the archive render blocks the main thread."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        init_start = app_js.index("  } else {\n    restoreSplitState();")
        init_js = app_js[init_start:app_js.index("  attachAllPaneDropZones();", init_start)]
        fetch_js = app_js[
            app_js.index("async function fetchConversationEvents(paneId)"):
            app_js.index("Object.defineProperty(window, '_firstUserMsgRendered'", app_js.index("async function fetchConversationEvents(paneId)"))
        ]

        self.assertLess(
            init_js.index("prefetchRestoredConversationTails();"),
            init_js.index("loadConversationList();"),
        )
        self.assertIn("_takePrefetchedConversationTail(id)", fetch_js)

    def test_archive_boot_shapes_saved_row_and_restores_before_full_render(self):
        """Metadata-safe one-row shaping should precede the expensive full archive render."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        mode_start = app_js.index("async function setArchiveMode()")
        mode_js = app_js[mode_start:app_js.index("(function wireArchiveMode()", mode_start)]

        narrow = mode_js.index("renderArchiveList(savedConversationId, { force: true, skipRestore: true });")
        restore = mode_js.index("await selectConversation(savedConversationId, savedPane.id);")
        paint = mode_js.index("await new Promise(resolve => setTimeout(resolve, 0));")
        full = mode_js.rindex("renderArchiveList(")
        self.assertLess(narrow, restore)
        self.assertLess(restore, paint)
        self.assertLess(paint, full)
        self.assertIn("!(opts && opts.skipRestore)", app_js)
        self.assertIn("savedLastView.type === 'gc'", mode_js)

    def test_restore_finds_saved_conversation_outside_the_visible_all_lane(self):
        """A saved Coding session must restore while the All sidebar shows Workers."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        restore_start = app_js.index("async function restoreLastConversation()")
        restore_js = app_js[restore_start:app_js.index("function activePaneId()", restore_start)]

        self.assertIn("conversationRowsContainId(conversationsData, pane.conversationId)", restore_js)
        self.assertIn("conversationRowsContainId(archiveData, pane.conversationId)", restore_js)

    def test_hover_prefetch_uses_tail_window(self):
        """Hover warming must not full-parse huge transcripts."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        prefetch_js = app_js[
            app_js.index("function _prefetchConversationTail(id)"):
            app_js.index("function _convPrefetchCancel(id)", app_js.index("function _prefetchConversationTail(id)"))
        ]

        self.assertIn("?tail=' + CONV_TAIL_LINES", prefetch_js)
        self.assertNotIn("?after=0", prefetch_js)
        self.assertIn("CONV_TAIL_PREFETCH_MAX", prefetch_js)
        self.assertIn("CONV_TAIL_PREFETCH_TTL_MS", prefetch_js)
        self.assertIn("_convTailPrefetches.delete", prefetch_js)

    def test_non_streamed_hermes_sessions_do_not_consume_cached_tail(self):
        """Hermes has no follow-up SSE to reconcile a stale prefetched snapshot."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        fetch_start = app_js.index("async function fetchConversationEvents(paneId)")
        fetch_js = app_js[fetch_start:app_js.index("Object.defineProperty(window, '_firstUserMsgRendered'", fetch_start)]

        self.assertIn("_prefetchSource !== 'hermes'", fetch_js)

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
