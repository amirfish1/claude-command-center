"""CCC-129: split-view focus cues and the left-docked ticket composer."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")


class SplitFocusHighlightTests(unittest.TestCase):
    def test_other_pane_rows_are_marked_on_render_and_focus_change(self):
        self.assertIn("function otherPaneConvIds()", APP_JS)
        self.assertIn("const _otherPaneIds = otherPaneConvIds();", APP_JS)
        self.assertIn("_otherPaneIds.has(c.id) ? ' in-other-pane' : ''", APP_JS)
        self.assertIn("el.classList.toggle('in-other-pane', !isActive && otherIds.has(el.dataset.id));", APP_JS)

    def test_other_pane_row_has_its_own_color_in_every_row_style(self):
        self.assertIn(".conv-item.in-other-pane {", APP_CSS)
        self.assertIn('#convList[data-row-delineation="cards"] .conv-item.in-other-pane {', APP_CSS)
        self.assertIn("#convList.workers-dense .conv-item.in-other-pane {", APP_CSS)

    def test_focused_split_pane_header_is_highlighted(self):
        self.assertIn('.conv-split[data-orientation="vertical"] .conv-pane.is-active > .conv-pane-header,', APP_CSS)

    def test_desktop_pane_title_uses_sidebar_row_name(self):
        self.assertIn("const headerTitle = opts.title !== undefined ? String(title || '').trim() : rowName;", APP_JS)
        self.assertIn("titleEl.textContent = headerTitle;", APP_JS)

    def test_ticket_composer_docks_left_with_light_dim(self):
        self.assertIn(".fq-ticket-composer.upd-overlay {\n  justify-content: flex-start;", APP_CSS)
        self.assertIn(".fq-ticket-composer > .upd-backdrop {\n  background: rgba(0, 0, 0, 0.18);", APP_CSS)


if __name__ == "__main__":
    unittest.main()
