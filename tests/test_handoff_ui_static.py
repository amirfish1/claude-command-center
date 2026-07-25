"""Static invariants for the "Continue on…" session-handoff UI.

Greps the static sources (no server import) the same way the other
test_*_static.py files do — the goal is to catch accidental removal or
renaming of the modal entry point, its handoff API bindings, the overflow
menu action, and the modal markup / CSS.
"""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(*parts):
    return pathlib.Path(PROJECT_ROOT, *parts).read_text(encoding="utf-8")


class TestHandoffUiStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = _read("static", "app.js")
        cls.index_html = _read("static", "index.html")
        cls.app_css = _read("static", "app.css")

    def test_app_js_defines_the_modal_entry_point(self):
        self.assertIn("function openHandoffModal(", self.app_js)

    def test_app_js_binds_the_handoff_endpoints(self):
        self.assertIn("'/api/federation/handoff/preflight'", self.app_js)
        self.assertIn("'/api/federation/handoff/start'", self.app_js)
        self.assertIn("/api/federation/handoff/status", self.app_js)

    def test_app_js_has_continue_on_action(self):
        self.assertIn("Continue on", self.app_js)
        # The overflow-menu item wires the modal open.
        self.assertIn("data-handoff-continue", self.app_js)

    def test_app_js_renders_moved_chip(self):
        # "Moved to <node>" ownership chip, fetched once (no polling).
        self.assertIn("handoff-moved-chip", self.app_js)
        self.assertIn("Moved to another node", self.app_js)
        self.assertIn("owned_here", self.app_js)

    def test_index_html_has_modal_markup(self):
        self.assertIn('id="handoffModal"', self.index_html)
        self.assertIn('id="handoffBackdrop"', self.index_html)
        self.assertIn('id="handoffDest"', self.index_html)
        self.assertIn('id="handoffPlan"', self.index_html)
        self.assertIn('id="handoffConfirmBtn"', self.index_html)

    def test_no_peers_message_links_to_peers_modal(self):
        self.assertIn("No paired nodes", self.index_html)
        self.assertIn('id="handoffOpenPeers"', self.index_html)
        self.assertIn("openFederationPeersModal", self.app_js)

    def test_app_css_has_handoff_styles(self):
        self.assertIn(".handoff-plan", self.app_css)
        self.assertIn(".handoff-blocker", self.app_css)
        self.assertIn(".handoff-moved-chip", self.app_css)


if __name__ == "__main__":
    unittest.main()
