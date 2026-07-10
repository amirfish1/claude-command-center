"""Static invariants for the federation "Nodes & peers" management UI.

Greps the static sources (no server import) the same way the other
test_*_static.py files do — the goal is to catch accidental removal or
renaming of the modal entry point, its API bindings, and its markup.
"""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(*parts):
    return pathlib.Path(PROJECT_ROOT, *parts).read_text(encoding="utf-8")


class TestFederationUiStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = _read("static", "app.js")
        cls.index_html = _read("static", "index.html")
        cls.app_css = _read("static", "app.css")

    def test_app_js_defines_the_modal_entry_point(self):
        self.assertIn("function openFederationPeersModal()", self.app_js)

    def test_app_js_binds_the_federation_endpoints(self):
        self.assertIn("'/api/federation/peers'", self.app_js)
        self.assertIn("'/api/federation/peers/pair'", self.app_js)
        self.assertIn("'/api/federation/peers/test'", self.app_js)
        self.assertIn("'/api/federation/peers/rename'", self.app_js)
        self.assertIn("'/api/federation/peers/remove'", self.app_js)
        self.assertIn("'/api/federation/repo-map'", self.app_js)
        self.assertIn("'/api/federation/node'", self.app_js)

    def test_nodes_and_peers_label_is_present(self):
        label_present = (
            "Nodes & peers" in self.index_html
            or "Nodes &amp; peers" in self.index_html
            or "Nodes & peers" in self.app_js
        )
        self.assertTrue(
            label_present,
            "expected a 'Nodes & peers' label in index.html or app.js",
        )

    def test_index_html_has_menu_entry_and_modal_markup(self):
        self.assertIn('id="federationPeersBtn"', self.index_html)
        self.assertIn('id="fedPeersModal"', self.index_html)
        self.assertIn('id="fedPeersBackdrop"', self.index_html)
        self.assertIn('id="fedPeersList"', self.index_html)
        self.assertIn('id="fedRepoMapList"', self.index_html)

    def test_unconfigured_transport_renders_warning_chip(self):
        self.assertIn("no route back", self.app_js)
        self.assertIn("fed-chip-warn", self.app_js)

    def test_app_css_has_fed_styles(self):
        self.assertIn(".fed-row", self.app_css)
        self.assertIn(".fed-chip-warn", self.app_css)
        self.assertIn(".fed-inline-error", self.app_css)


if __name__ == "__main__":
    unittest.main()
