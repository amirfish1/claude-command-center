"""Static contracts for browser-local Debug mode."""

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / "static" / name).read_text(encoding="utf-8")


class TestDebugModeStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index, cls.app, cls.css = map(read, ("index.html", "app.js", "app.css"))

    def test_preference_and_switch_are_present(self):
        self.assertIn("ccc-debug-mode", self.index)
        self.assertIn("ccc-debug-mode", self.app)
        self.assertIn("document.documentElement.classList.toggle('ccc-debug-mode'", self.app)
        self.assertIn('id="settingsDebugModeToggle"', self.index)
        self.assertIn('data-debug-mode-toggle', self.index)

    def test_mode_hides_approved_controls(self):
        for selector in (
            '#statsBtn', '#annotationFabBtn', '#annotationStartBtn', '#annotationScreenBtn', '#annotationNotesBtn',
            '#statusRailAnnotateBtn', '#statusRailActivityLogBtn',
            '#cccHealth', '#cccAdvisorPill', '#cccThroughputPill', '#cccThroughputStrip',
            '#cccFleetPill', '#cccHeroPulsePill', '[data-role="pane-annotate"]',
            '#customNavLinks .q2-toggle-opt[href="/view/reddit"]',
            '[data-role="conv-rowstyle-palette"]', '[data-role="conv-bg-palette"]',
        ):
            self.assertIn("html.ccc-debug-mode " + selector, self.css)

    def test_interrupt_path_is_dismiss_only_in_debug_mode(self):
        self.assertIn("function debugModeEnabled()", self.app)
        self.assertIn("if (debugModeEnabled())", self.app)
        self.assertIn("resolveInterruptAsk(ask, 'dismiss')", self.app)

    def test_mode_suppresses_diagnostic_operation_toasts(self):
        self.assertIn("function showOpToast(msg, kind, action)", self.app)
        self.assertIn(
            "if (debugModeEnabled() && (kind === 'error' || kind === 'info')) return;",
            self.app,
        )


if __name__ == "__main__":
    unittest.main()
