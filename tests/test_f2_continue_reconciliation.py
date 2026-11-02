"""Regression coverage for F2 Continue New Codex handoff."""

import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestF2ContinueReconciliation(unittest.TestCase):
    def test_continue_uses_the_parent_display_name_for_its_spawn_title(self):
        """Continuation rows should identify the conversation, not its opaque id."""
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        f2_start = app_js.index("async function f2RunContinue")
        f2_end = app_js.index("// One delegated listener", f2_start)
        f2_block = app_js[f2_start:f2_end]

        self.assertIn("const parentTitle = sidebarRowDisplayTitle(", f2_block)
        self.assertIn("const subject = parentTitle;", f2_block)
        self.assertNotIn("'continue-' + String(sid).slice(0, 8)", f2_block)

    def test_codex_continue_watches_for_a_durable_spawned_session(self):
        subprocess.run(
            ["node", "--test", "tests/codex-spawn-handoff.test.cjs"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )


if __name__ == "__main__":
    unittest.main()
