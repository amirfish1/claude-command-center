"""CCC-529: the session-row kebab opens a real dropdown, not an overlay on the row."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


class RowActionsMenuTest(unittest.TestCase):
    def test_open_state_is_an_anchored_labelled_dropdown(self):
        css = (STATIC / "app.css").read_text()
        block = re.search(
            r"\.conv-item\.is-actions-open \.conv-row-actions:not\(:empty\) \{(.*?)\}", css, re.S)
        self.assertIsNotNone(block)
        body = block.group(1)
        self.assertIn("position: fixed", body)
        self.assertIn("var(--ca-top", body)
        self.assertIn("var(--ca-right", body)
        self.assertIn("flex-direction: column", body)
        # Each icon button shows its title as a text label.
        self.assertRegex(css, r"\.is-actions-open \.conv-row-actions > button::after \{\s*content: attr\(title\)")

    def test_kebab_stays_visible_while_open(self):
        css = (STATIC / "app.css").read_text()
        self.assertNotIn(
            ".conv-item.is-actions-open .conv-row-end > .conv-kebab-btn,\n"
            "  .conv-item.is-actions-open .conv-row-end .conv-kebab-btn {\n    opacity: 0;", css)

    def test_js_anchors_menu_to_kebab_and_closes_on_scroll(self):
        js = (STATIC / "app.js").read_text()
        self.assertIn("setProperty('--ca-top'", js)
        self.assertIn("setProperty('--ca-right'", js)
        # Rect is read before the row flips to the open state.
        self.assertLess(js.index("const r = btn.getBoundingClientRect();\n        item.classList.toggle('is-actions-open'"),
                        js.index("setProperty('--ca-top'"))
        self.assertRegex(js, r"(?s)addEventListener\('scroll'.{0,200}is-actions-open")


if __name__ == "__main__":
    unittest.main()
