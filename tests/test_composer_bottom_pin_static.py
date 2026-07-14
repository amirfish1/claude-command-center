"""Contracts for preserving transcript bottom position on composer focus."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "static" / "app.js"


class TestComposerBottomPinStatic(unittest.TestCase):
    def test_pointer_focus_preserves_bottom_or_exact_reading_position(self):
        source = APP_JS.read_text(encoding="utf-8")
        fn_start = source.index("function preserveConversationBottomOnComposerPointerDown(ev)")
        fn_end = source.index("\n  document.addEventListener('pointerdown'", fn_start)
        fn = source[fn_start:fn_end]

        self.assertIn("const wasAtBottom = isConversationAtBottom(view);", fn)
        self.assertIn("const restoreScrollTop = view.scrollTop;", fn)
        self.assertIn("requestAnimationFrame(() =>", fn)
        self.assertIn("if (wasAtBottom) scrollConversationToEnd(view);", fn)
        self.assertIn("else view.scrollTop = restoreScrollTop;", fn)
        self.assertIn(
            "document.addEventListener('pointerdown', "
            "preserveConversationBottomOnComposerPointerDown, true)",
            source,
        )

    def test_end_button_uses_an_immediate_tail_jump(self):
        source = APP_JS.read_text(encoding="utf-8")
        fn_start = source.index("function attachConversationEndAffordance(view)")
        fn_end = source.index("\n  function ensureAllConversationEndAffordances", fn_start)
        fn = source[fn_start:fn_end]

        self.assertIn("scrollConversationToEnd(targetView);", fn)
        self.assertNotIn("scrollConversationToEnd(targetView, 'smooth');", fn)
        self.assertIn("const restoreEnd = () =>", fn)
        self.assertIn("requestAnimationFrame(restoreEnd);", fn)


if __name__ == "__main__":
    unittest.main()
