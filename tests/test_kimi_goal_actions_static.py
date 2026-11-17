"""Static regression coverage for Kimi goal-strip actions."""

from __future__ import annotations

from tests.dashboard_source import dashboard_source
import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestKimiGoalActions(unittest.TestCase):
    def test_kimi_goal_strip_does_not_send_claude_goal_commands(self):
        app_js = dashboard_source(PROJECT_ROOT / "static" / "app.js")

        source_start = app_js.index("function conversationGoalSourceKind(source)")
        source_end = app_js.index("function conversationGoalActionButtonsHtml", source_start)
        source_body = app_js[source_start:source_end]

        self.assertIn("s === 'kimi'", source_body)
        self.assertIn("return '';", source_body)


if __name__ == "__main__":
    unittest.main()
