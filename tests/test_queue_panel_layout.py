import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueuePanelLayout(unittest.TestCase):
    def test_queue_panel_note_text_expands_with_rail_width(self):
        """Queue rows should not pre-truncate notes before CSS can size them."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        queue_js = app_js[
            app_js.index("function _renderQueuePanel()"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel()"))
        ]
        self.assertNotIn("noteFull.length > 30", queue_js)
        self.assertIn("+ '<span class=\"fq-note\">' + escapeHtml(noteFull) + '</span>'", queue_js)

        note_css = app_css[
            app_css.index(".fq-note {"):
            app_css.index(".fq-status {", app_css.index(".fq-note {"))
        ]
        self.assertIn("flex: 1 1 auto;", note_css)
        self.assertIn("min-width: 0;", note_css)
        self.assertIn("overflow: hidden;", note_css)
        self.assertIn("text-overflow: ellipsis;", note_css)
        self.assertIn("white-space: nowrap;", note_css)

    def test_queue_panel_empty_state_explains_project_scope(self):
        """An empty scoped Queue tab should say why it is empty."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        queue_js = app_js[
            app_js.index("function _renderQueuePanel()"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel()"))
        ]
        self.assertIn("function _uxqEmptyHtml(project, totalCount)", app_js)
        self.assertIn("No tickets for ' + escapeHtml(project)", app_js)
        self.assertIn("' tickets in other projects.'", app_js)
        self.assertIn("_uxqEmptyHtml(proj, items.length)", queue_js)
        self.assertNotIn("Queue is empty.</div>", queue_js)
        self.assertIn(".fq-empty-sub", app_css)


if __name__ == "__main__":
    unittest.main()
