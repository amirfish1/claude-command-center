import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestComposerMobileInputSpace(unittest.TestCase):
    """Phone composer always keeps a typing area: controls wrap, they never
    share the textarea's row."""

    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
        cls.html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        cls.app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

    def test_phone_textarea_keeps_width_and_bottom_row_wraps(self):
        self.assertRegex(
            self.css,
            r"\.conv-input-bar \.conv-input-row-top textarea \{\s*min-width: 0;\s*width: 100%;",
        )
        self.assertRegex(
            self.css,
            r"\.conv-input-bar \.conv-input-row-bottom \{\s*flex-wrap: wrap;",
        )

    def test_top_row_holds_only_the_input(self):
        top = self.html.split('<div class="conv-input-row-top">', 1)[1]
        top = top.split('<div class="conv-input-row-bottom">', 1)[0]
        self.assertIsNone(re.search(r"<button|<select", top), top)

    def test_minimized_state_that_squeezed_the_input_is_gone(self):
        self.assertNotIn("is-composer-minimized", self.css)
        self.assertNotIn("is-composer-minimized", self.app)
        self.assertNotIn("'minimize'", self.app)


if __name__ == "__main__":
    unittest.main()
