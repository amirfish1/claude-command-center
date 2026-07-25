"""Static regressions for the conversation workspace context strip."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class WorkspaceContextTests(unittest.TestCase):
    def test_worktree_context_keeps_a_visible_glyph_when_kind_chip_is_hidden(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        app_css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")

        self.assertIn("wp-worktree-icon", app_js)
        self.assertIn("🌿", app_js)
        self.assertNotIn(".conv-input-context .wp-worktree-icon { display: none; }", app_css)


if __name__ == "__main__":
    unittest.main()
