"""Static coverage for compact All-view goal indicators."""

from __future__ import annotations

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestAllGoalIcon(unittest.TestCase):
    def test_all_view_uses_a_goal_icon_with_the_full_goal_in_its_tooltip(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const goalIconOnly = !!opts.goalIconOnly;", app_js)
        self.assertIn('class="conv-goal-icon-only', app_js)
        self.assertIn("+ escapeAttr(_gTip) + '", app_js)
        self.assertIn("goalIconOnly ? goalIconHtml : ''", app_js)

    def test_all_view_passes_compact_goal_option_to_its_rows(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        all_start = app_js.index("const _allTabConvs =")
        all_end = app_js.index("const _allTabTotalCount =", all_start)
        all_body = app_js[all_start:all_end]

        self.assertIn("goalIconOnly: true", all_body)
