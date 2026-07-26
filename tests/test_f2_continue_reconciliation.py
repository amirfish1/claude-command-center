"""Regression coverage for F2 Continue New Codex handoff."""

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestF2ContinueReconciliation(unittest.TestCase):
    def test_codex_continue_watches_for_a_durable_spawned_session(self):
        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        f2_start = app_js.index("async function f2RunContinue")
        f2_end = app_js.index("// One delegated listener", f2_start)
        f2_block = app_js[f2_start:f2_end]
        self.assertIn("_watchF2CodexSpawnRegistration(", f2_block)

        helper_start = app_js.index("function _watchF2CodexSpawnRegistration")
        helper_end = app_js.index("function _watchPendingSpawnRegistration", helper_start)
        helper = app_js[helper_start:helper_end]
        self.assertIn("Date.now() + 30000", helper)
        self.assertIn("/api/sessions/spawned?engine=codex", helper)
        self.assertIn("row.session_id", helper)
        self.assertIn("row.spawn_id", helper)
        self.assertIn("refreshArchiveData({ force: true })", helper)
        self.assertIn("markPendingSpawnNotAcknowledged(pid, fallbackId)", helper)


if __name__ == "__main__":
    unittest.main()
