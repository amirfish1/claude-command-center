"""Regression coverage for opening a worker session from the queue."""

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class QueueOpenSessionTests(unittest.TestCase):
    def test_raw_session_open_keeps_the_composer_available(self):
        app = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        start = app.index("async function selectConversation(id, paneId)")
        selection = app[
            start:
            app.index("// Update split panel input bar visibility", start)
        ]

        self.assertIn("const selectedSessionId =", selection)
        self.assertIn("isSessionId ? id : null", selection)
        self.assertIn(
            "setCurrentSession(\n        source,\n        selectedSessionId,",
            selection,
        )


if __name__ == "__main__":
    unittest.main()
