"""Static invariants for the Fleet view UI.

Greps the static sources (no server import) the same way the other
test_*_static.py files do — the goal is to catch accidental removal or
renaming of the view entry point, its Fleet API bindings, the nav label,
the modal markup, and the fleet- CSS.
"""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _read(*parts):
    return pathlib.Path(PROJECT_ROOT, *parts).read_text(encoding="utf-8")


class TestFleetUiStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = _read("static", "app.js")
        cls.index_html = _read("static", "index.html")
        cls.app_css = _read("static", "app.css")

    def test_app_js_defines_the_view_entry_points(self):
        self.assertIn("function openFleetView(", self.app_js)
        self.assertIn("function renderFleetView(", self.app_js)

    def test_app_js_binds_the_fleet_endpoints(self):
        for path in (
            "/api/fleet/inventory",
            "/api/fleet/plan",
            "/api/fleet/execute",
            "/api/fleet/job",
            "/api/fleet/attribute",
            "/api/fleet/ping-session",
        ):
            self.assertIn(path, self.app_js, f"expected {path} bound in app.js")

    def test_a_fleet_nav_label_is_present(self):
        label_present = (
            "Fleet" in self.index_html or "Fleet" in self.app_js
        )
        self.assertTrue(
            label_present,
            "expected a 'Fleet' nav label in index.html or app.js",
        )

    def test_index_html_has_nav_entry_and_modal_markup(self):
        self.assertIn('id="fleetViewBtn"', self.index_html)
        self.assertIn('id="fleetModal"', self.index_html)
        self.assertIn('id="fleetBackdrop"', self.index_html)
        self.assertIn('id="fleetHealthStrip"', self.index_html)
        self.assertIn('id="fleetMatrix"', self.index_html)
        self.assertIn('id="fleetPlanModal"', self.index_html)
        self.assertIn('id="fleetResolveBtn"', self.index_html)

    def test_app_js_has_who_attribution_and_ping(self):
        # Per-dirty-worktree "Who?" affordance + "Ping to commit".
        self.assertIn("data-fleet-who", self.app_js)
        self.assertIn("Ping to commit", self.app_js)
        self.assertIn("kind: 'commit'", self.app_js)

    def test_app_js_renders_stale_and_dimension_chips(self):
        # Stale sources are badged, not hidden; deployment is its own chip.
        self.assertIn("fleet-stale-badge", self.app_js)
        self.assertIn("behind origin", self.app_js)
        self.assertIn("PR data unavailable", self.app_js)

    def test_app_css_has_fleet_styles(self):
        self.assertIn(".fleet-repo-card", self.app_css)
        self.assertIn(".fleet-node-chip", self.app_css)
        self.assertIn(".fleet-cell", self.app_css)
        self.assertIn(".fleet-attr-popover", self.app_css)
        self.assertIn(".fleet-action", self.app_css)


if __name__ == "__main__":
    unittest.main()
