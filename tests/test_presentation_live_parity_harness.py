"""Contract for the browser-level presentation parity verifier."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = PROJECT_ROOT / "scripts" / "verify-presentation-live-parity.js"


class TestPresentationLiveParityHarness(unittest.TestCase):
    def test_verifier_is_deterministic_and_failure_safe(self):
        if not VERIFIER.exists():
            self.fail("presentation live parity verifier is missing")
        source = VERIFIER.read_text(encoding="utf-8")

        self.assertIn("require('../require-puppeteer.js')", source)
        self.assertIn("CCC_PRESENTATION_PARITY_URL", source)
        self.assertIn("try {", source)
        self.assertIn("finally {", source)
        self.assertIn("await browser.close()", source)
        self.assertIn("page.waitForFunction", source)
        self.assertNotIn("page.waitForTimeout", source)
        self.assertIn("process.exitCode = 1", source)

    def test_verifier_covers_the_full_live_update_matrix(self):
        if not VERIFIER.exists():
            self.fail("presentation live parity verifier is missing")
        source = VERIFIER.read_text(encoding="utf-8")

        for label in (
            "pending", "queued", "delivered", "failed", "removed", "durable",
            "sending", "thinking", "long-thinking", "generating", "tool",
            "tokens", "elapsed", "stream", "completed", "approval", "question",
            "wake", "warning", "error", "done", "attribute", "class", "disabled",
            "enabled", "click", "input", "change", "details", "historical-cursor",
            "split-pane", "resize", "off-restore", "legacy-mode-one", "added",
            "edited", "tool-group", "tool-complete", "approval-state",
            "queue-reason", "outcome-banner", "dismissal", "frame-bound",
            "completion-supersede", "reactivation",
        ):
            self.assertIn(f"'{label}'", source)

        for token in (
            "function snapshot(node)",
            "sourceSnapshot",
            "mirrorSnapshot",
            "presentationProjectionId",
            "getClientRects",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
