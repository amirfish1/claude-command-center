from tests.dashboard_source import dashboard_source
import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestSplitPaneIsolationStatic(unittest.TestCase):
    def test_bottom_composer_updates_are_scoped_to_active_pane(self):
        """Split panes must not update p1's composer while showing p2's bar."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "conversation-input.js").read_text(encoding="utf-8")
        fn_start = app_js.index("function updateInputBar()")
        fn_end = app_js.index("\n  function composerUserEditRevision", fn_start)
        fn = app_js[fn_start:fn_end]

        self.assertIn("const activeInputControls = inputControlsForBar(_activeInputBar);", fn)
        self.assertIn("activeInputControls.ttyLabel.textContent", fn)
        self.assertNotIn("$convTtyLabel.textContent", fn)
        self.assertNotIn("$convInput.placeholder", fn)
        self.assertNotIn("$convSendBtn.disabled", fn)

    def test_split_clone_does_not_keep_a_second_rhs_rail(self):
        """Only the active pane may host the singleton status rail."""
        app_js = dashboard_source(pathlib.Path(PROJECT_ROOT, "static", "app.js"))
        build_start = app_js.index("function buildPaneElement(paneId)")
        build_end = app_js.index("\n  // Toggle the split layout", build_start)
        build_fn = app_js[build_start:build_end]
        active_fn = pathlib.Path(PROJECT_ROOT, "static", "conversation-panes.js").read_text(encoding="utf-8")

        self.assertIn("removeSplitPaneSingletonChrome(clone);", build_fn)
        self.assertIn("helpers.mountStatusRailForActivePane)();", active_fn)

    def test_split_clone_binds_image_paste_to_its_own_composer(self):
        """A cloned right-hand composer must retain image-paste support."""
        app_js = dashboard_source(pathlib.Path(PROJECT_ROOT, "static", "app.js"))
        build_start = app_js.index("function buildPaneElement(paneId)")
        build_end = app_js.index("\n  // Toggle the split layout", build_start)
        build_fn = app_js[build_start:build_end]

        self.assertIn("attachImagePaste(input);", build_fn)

if __name__ == "__main__":
    unittest.main()
