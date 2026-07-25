"""Contracts for preserving transcript bottom position on composer focus."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "static" / "app.js"


class TestComposerBottomPinStatic(unittest.TestCase):
    def test_pointer_focus_restores_only_an_already_bottom_pinned_transcript(self):
        source = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function preserveConversationBottomOnComposerPointerDown(ev)", source)
        self.assertIn("isConversationAtBottom(view)", source)
        self.assertIn("requestAnimationFrame(() =>", source)
        self.assertIn("scrollConversationToEnd(view)", source)
        self.assertIn(
            "document.addEventListener('pointerdown', "
            "preserveConversationBottomOnComposerPointerDown, true)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
