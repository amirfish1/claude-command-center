import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NewSessionQueueIsolationTests(unittest.TestCase):
    def test_browser_verifier_exercises_real_new_session_lifecycle(self):
        source = (ROOT / "scripts/verify-new-session-queue-isolation.js").read_text()

        self.assertIn("require('../require-puppeteer.js')", source)
        self.assertIn("listView.click();", source)
        self.assertIn("newSession.click();", source)
        self.assertIn("#convInputBar .queued-steer-tray", source)
        self.assertIn("CCC_NEW_SESSION_FOREIGN_QUEUE_SENTINEL", source)
        self.assertIn("title.textContent.trim() === 'New session'", source)

    def test_new_session_entry_removes_active_pane_queued_tray(self):
        source = (ROOT / "static/app.js").read_text()
        start = source.index("function enterNewSessionMode()")
        end = source.index("\n  async function", start)
        function_source = source[start:end]

        self.assertIn("getConvInputBarForPane(paneId)", function_source)
        self.assertIn("querySelector('.queued-steer-tray')", function_source)
        self.assertIn("staleQueuedTray.remove()", function_source)


if __name__ == "__main__":
    unittest.main()
