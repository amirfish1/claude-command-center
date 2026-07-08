import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestSplitPaneIsolationStatic(unittest.TestCase):
    def test_bottom_composer_updates_are_scoped_to_active_pane(self):
        """Split panes must not update p1's composer while showing p2's bar."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        fn_start = app_js.index("function updateInputBar()")
        fn_end = app_js.index("\n  const SLASH_FALLBACK_COMMANDS", fn_start)
        fn = app_js[fn_start:fn_end]

        self.assertIn("const activeInputControls = inputControlsForBar(_activeInputBar);", fn)
        self.assertIn("activeInputControls.ttyLabel.textContent", fn)
        self.assertNotIn("$convTtyLabel.textContent", fn)
        self.assertNotIn("$convInput.placeholder", fn)
        self.assertNotIn("$convSendBtn.disabled", fn)

    def test_split_clone_does_not_keep_a_second_rhs_rail(self):
        """Only the active pane may host the singleton status rail."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        build_start = app_js.index("function buildPaneElement(paneId)")
        build_end = app_js.index("\n  // Toggle the split layout", build_start)
        build_fn = app_js[build_start:build_end]
        active_start = app_js.index("function setActivePaneById")
        active_end = app_js.index("\n\n  // Compatibility shim", active_start)
        active_fn = app_js[active_start:active_end]

        self.assertIn("removeSplitPaneSingletonChrome(clone);", build_fn)
        self.assertIn("mountStatusRailForActivePane();", active_fn)


if __name__ == "__main__":
    unittest.main()
