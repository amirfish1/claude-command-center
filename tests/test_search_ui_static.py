import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestSearchUiStatic(unittest.TestCase):
    def test_recall_matches_are_prioritized_after_title_matches(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("function _historySourceRank(source) {", app_js)
        self.assertIn("if (source === 'recall') return 3;", app_js)
        self.assertIn("function _mergeHistoryResults(qLower, results) {", app_js)
        self.assertIn(
            "_historySourceRank(hit.source) > _historySourceRank(existing.source)",
            app_js,
        )
        self.assertIn("function _prioritizeSearchResultBands(rows, qLower) {", app_js)
        self.assertIn("else if (_isRecallHistoryMatch(c)) recall.push(c);", app_js)
        self.assertIn("return name.concat(recall).concat(rest);", app_js)
        self.assertIn(
            "return _prioritizeSessionIdMatches(_prioritizeSearchResultBands(combined, qLower), qLower);",
            app_js,
        )

    def test_objects_grouping_search_uses_ranked_results_as_top_band(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn(
            "const _currentSessionSource = _ipSearchActive\n"
            "        ? (_visibleSessionConvs || []).slice()",
            app_js,
        )
        self.assertIn(
            "const _currentSessionWindowed = _ipSearchActive\n"
            "        ? _currentSessionSource",
            app_js,
        )
        self.assertIn(
            "const _currentSessionLineage = _ipSearchActive\n"
            "        ? { rows: _currentSessionWindowed, openAsks: [] }",
            app_js,
        )
        self.assertIn(
            "const _currentSessions = _ipSearchActive\n"
            "        ? _currentSessionLineage.rows",
            app_js,
        )
        self.assertIn(
            "const _currentSessionsLabel = _ipSearchActive ? 'Search results' : 'Current sessions';",
            app_js,
        )
        self.assertIn(
            "const _currentSessionsSub = _ipSearchActive ? '' : '<span class=\"conv-objects-section-sub\">' + _currentSessionsWindowLabel + '</span>';",
            app_js,
        )

    def test_recall_results_repaint_without_waiting_for_history(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn("const recallDone = recallReq.then((recallData) => {", app_js)
        self.assertIn(
            "_mergeHistoryResults(qLower, (recallData && recallData.results) || []);",
            app_js,
        )
        self.assertIn("repaintIfCurrent();", app_js)
        self.assertIn("function _renderConversationSearchResults(query) {", app_js)
        self.assertIn(
            "renderSidebar(filterConversations(query), { force: true });",
            app_js,
        )
        self.assertIn("modified: _historyTsSeconds(hit.ts),", app_js)

    def test_archive_search_keeps_name_matches_above_recall_results(self):
        """A late Total Recall repaint must preserve the search-result bands."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        start = app_js.index("function renderArchiveList(filter, opts) {")
        end = app_js.index("async function setArchiveMode", start)
        body = app_js[start:end]

        self.assertIn(
            "_prioritizeSearchResultBands(applyConvSort(_applyOptimisticTouches(rowsForRender), { persist: true }), q)",
            body,
        )

    def test_add_queue_action_is_after_queue_health_rows_not_tickets(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        health_start = app_js.index("async function _renderQueueHealthStrip")
        health_end = app_js.index("// Repo-basename", health_start)
        health_body = app_js[health_start:health_end]
        panel_start = app_js.index("function _renderQueuePanel(options)")
        panel_end = app_js.index("// Jump the conversation pane", panel_start)
        panel_body = app_js[panel_start:panel_end]

        self.assertIn('id="filesQueueConfigure"', health_body)
        self.assertNotIn('id="filesQueueConfigure"', panel_body)

    def test_throughput_boot_renders_complete_browser_snapshot_before_network(self):
        html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

        self.assertIn("/api/throughput/initial", html)
        self.assertIn("readThroughputBootstrap", html)
        self.assertIn("applyThroughputBootstrap", html)
        self.assertNotIn("refreshAggregateInBackground", html)

        boot_start = html.index("function bootThroughputPage")
        read_idx = html.index("readActiveThroughputBootstrap", boot_start)
        apply_idx = html.index("applyThroughputBootstrap", boot_start)
        network_idx = html.index("loadServerBootstrapThenRefresh", boot_start)
        self.assertLess(read_idx, apply_idx)
        self.assertLess(apply_idx, network_idx)


if __name__ == "__main__":
    unittest.main()
