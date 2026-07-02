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
            "const _currentSessions = _ipSearchActive\n"
            "        ? (_visibleSessionConvs || []).slice()",
            app_js,
        )
        self.assertIn(
            "const _currentSessionsLabel = _ipSearchActive ? 'Search results' : 'Current sessions';",
            app_js,
        )
        self.assertIn(
            "const _currentSessionsSub = _ipSearchActive ? '' : '<span class=\"conv-objects-section-sub\">last 5h</span>';",
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

    def test_throughput_boot_renders_initial_snapshot_before_refresh(self):
        html = pathlib.Path(PROJECT_ROOT, "static", "throughput.html").read_text(encoding="utf-8")

        self.assertIn("/api/throughput/initial", html)
        self.assertIn("loadInitialAggregate(_aggDefault)", html)
        self.assertIn("refreshAggregateInBackground(_aggDefault)", html)

        load_start = html.index("async function loadInitialAggregate")
        render_idx = html.index("renderDashboard(_aggDefault, data)", load_start)
        refresh_idx = html.index("refreshAggregateInBackground(_aggDefault)", load_start)
        self.assertLess(render_idx, refresh_idx)


if __name__ == "__main__":
    unittest.main()
