from pathlib import Path
import unittest


APP_JS = Path("static/app.js")


class ModelAdvisorSchedulingStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_JS.read_text(encoding="utf-8")

    def test_footer_uses_saved_state_without_any_advisor_request(self):
        footer_start = self.source.index("const advPill = document.createElement('div');")
        footer_end = self.source.index(
            "// Productivity is deliberately", footer_start
        )
        footer_block = self.source[footer_start:footer_end]

        self.assertNotIn("/api/model-advisor", footer_block)
        self.assertNotIn("setInterval(_bg, 45000)", self.source)
        self.assertNotIn("checks every 45 seconds", self.source)

    def test_modal_forces_one_fresh_report_then_polls_cache(self):
        self.assertIn("_pollModelAdvisor('force')", self.source)
        self.assertIn(
            "'/api/model-advisor?fresh=' + encodeURIComponent(fresh)",
            self.source,
        )
        self.assertIn("_maTimer = setInterval(_pollModelAdvisor, 5000)", self.source)

    def test_qualifying_session_changes_are_debounced_and_rate_limited(self):
        self.assertIn("const _ADVISOR_DEBOUNCE_MS = 30000", self.source)
        self.assertIn("const _ADVISOR_REFRESH_MIN_MS = 300000", self.source)
        self.assertIn("_observeAdvisorSessionChanges(fresh)", self.source)
        self.assertIn("_requestScheduledAdvisorRefresh", self.source)

    def test_initial_nonempty_snapshot_schedules_one_refresh(self):
        self.assertIn(
            "if (Object.keys(next).length) _scheduleAdvisorRefresh();",
            self.source,
        )
        self.assertIn("_advisorSessionSnapshot = next;", self.source)


if __name__ == "__main__":
    unittest.main()
