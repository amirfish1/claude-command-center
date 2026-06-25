import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestSearchUiStatic(unittest.TestCase):
    def test_recall_matches_are_prioritized_after_title_matches(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
