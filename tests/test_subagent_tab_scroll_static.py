"""Contracts for preserving transcript position across subagent tab switches."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "static" / "app.js"


class TestSubagentTabScrollStatic(unittest.TestCase):
    def test_tab_switch_remembers_and_restores_each_lane_scroll(self):
        source = APP_JS.read_text(encoding="utf-8")
        state_start = source.index("function _convPaneTabState(view)")
        state_end = source.index("\n  function _convPaneTabStrip", state_start)
        state_fn = source[state_start:state_end]
        activate_start = source.index("function _convPaneActivateTab(view, tabKey)")
        activate_end = source.index(
            "\n  function _convPaneEnsureSubagentTab", activate_start
        )
        activate_fn = source[activate_start:activate_end]

        self.assertIn("scrollByTab: Object.create(null)", state_fn)
        self.assertIn("_convPaneRememberTabScroll(view, state)", activate_fn)
        self.assertIn("_convPaneRestoreTabScroll(view, state, tabKey)", activate_fn)
        self.assertLess(
            activate_fn.index("_convPaneRememberTabScroll(view, state)"),
            activate_fn.index("state.active = tabKey"),
        )
        self.assertLess(
            activate_fn.index("_convPaneApplyActiveTab(view)"),
            activate_fn.index("_convPaneRestoreTabScroll(view, state, tabKey)"),
        )

    def test_tail_pinned_master_restores_to_true_end_after_layout(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function _convPaneRestoreTabScroll(", source)
        helper_start = source.index("function _convPaneRestoreTabScroll(")
        helper_end = source.index("\n  function _convPaneActivateTab", helper_start)
        helper = source[helper_start:helper_end]

        self.assertIn("requestAnimationFrame(() => requestAnimationFrame(() =>", helper)
        self.assertIn("saved.atBottom", helper)
        self.assertIn("scrollConversationToEnd(view)", helper)
        self.assertIn("Math.min(saved.top, max)", helper)
        self.assertIn("restoreVersion !== state.scrollRestoreVersion", helper)

    def test_inactive_task_close_never_restores_the_active_master(self):
        source = APP_JS.read_text(encoding="utf-8")
        close_start = source.index("function _convPaneCloseSubagentTab(")
        close_end = source.index("\n  function _convPaneMarkTaskCompleted", close_start)
        close_fn = source[close_start:close_end]

        self.assertIn("const wasActive = state.active === closedTabKey", close_fn)
        self.assertIn("if (wasActive) {", close_fn)
        restore = "_convPaneRestoreTabScroll(view, state, 'master')"
        restore_guard_start = close_fn.rindex("if (wasActive) {")
        restore_guard_end = close_fn.index("\n    }", restore_guard_start)
        self.assertIn(restore, close_fn[restore_guard_start:restore_guard_end])

    def test_pending_restore_is_not_resaved_during_rapid_switches(self):
        source = APP_JS.read_text(encoding="utf-8")
        remember_start = source.index("function _convPaneRememberTabScroll(")
        remember_end = source.index("\n  function _convPaneRestoreTabScroll", remember_start)
        remember = source[remember_start:remember_end]

        self.assertIn("state.pendingScrollRestore", remember)
        self.assertIn("pending.tabKey === state.active", remember)


if __name__ == "__main__":
    unittest.main()
