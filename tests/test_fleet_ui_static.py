"""Static invariants for the Fleet view UI.

Greps the static sources (no server import) the same way the other
test_*_static.py files do — the goal is to catch accidental removal or
renaming of the view entry point, its Fleet API bindings, the page markup,
the dashboard's launcher, and the fleet- CSS.

Fleet lives on its own page (static/fleet.html + static/fleet.js), not in a
dashboard modal: a fleet scan touches every mapped repo on every paired node,
which is far too slow to hold the dashboard behind an overlay.
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
        cls.fleet_js = _read("static", "fleet.js")
        cls.fleet_html = _read("static", "fleet.html")

    def test_fleet_js_defines_the_view_entry_points(self):
        self.assertIn("function renderFleetView(", self.fleet_js)
        self.assertIn("function loadFleetInventory(", self.fleet_js)

    def test_fleet_js_binds_the_fleet_endpoints(self):
        for path in (
            "/api/fleet/inventory",
            "/api/fleet/plan",
            "/api/fleet/execute",
            "/api/fleet/job",
            "/api/fleet/attribute",
            "/api/fleet/ping-session",
        ):
            self.assertIn(path, self.fleet_js, f"expected {path} bound in fleet.js")

    def test_a_fleet_nav_label_is_present(self):
        label_present = (
            "Fleet" in self.index_html or "Fleet" in self.app_js
        )
        self.assertTrue(
            label_present,
            "expected a 'Fleet' nav label in index.html or app.js",
        )

    def test_dashboard_launches_the_fleet_page(self):
        # Two entry points from the dashboard: the settings row and the pill.
        self.assertIn('id="fleetViewBtn"', self.index_html)
        self.assertIn("/fleet.html", self.app_js)
        self.assertIn("cccFleetPill", self.app_js)

    def test_dashboard_no_longer_embeds_the_fleet_modal(self):
        # The modal is what made the whole dashboard wait on a fleet scan.
        self.assertNotIn('id="fleetModal"', self.index_html)
        self.assertNotIn('id="fleetPlanModal"', self.index_html)

    def test_fleet_page_has_the_view_markup(self):
        self.assertIn('id="fleetHealthStrip"', self.fleet_html)
        self.assertIn('id="fleetMatrix"', self.fleet_html)
        self.assertIn('id="fleetPlanModal"', self.fleet_html)
        self.assertIn('id="fleetResolveBtn"', self.fleet_html)
        # The progress line that replaced the unexplained spinner.
        self.assertIn('id="fleetProgress"', self.fleet_html)
        self.assertIn("/static/fleet.js", self.fleet_html)

    def test_fleet_page_loads_in_two_passes(self):
        # Pass 1 drops the three slow dimensions so the matrix paints in
        # seconds; pass 2 fills them in. Losing this reintroduces the
        # multi-minute spinner.
        self.assertIn("prs=0&deploy=0&sessions=0", self.fleet_js)
        self.assertIn("fleetSetProgress", self.fleet_js)
        # Skipped dimensions must render as pending, never as a zero.
        self.assertIn("fleetPendingChip", self.fleet_js)
        self.assertIn("checking…", self.fleet_js)

    def test_fleet_js_has_who_attribution_and_ping(self):
        # Per-dirty-worktree "Who?" affordance + "Ping to commit".
        self.assertIn("data-fleet-who", self.fleet_js)
        self.assertIn("Ping to commit", self.fleet_js)
        self.assertIn("kind: 'commit'", self.fleet_js)

    def test_fleet_js_renders_stale_and_dimension_chips(self):
        # Stale sources are badged, not hidden; deployment is its own chip.
        self.assertIn("fleet-stale-badge", self.fleet_js)
        self.assertIn("behind origin", self.fleet_js)
        self.assertIn("PR data unavailable", self.fleet_js)

    def test_app_css_has_fleet_styles(self):
        self.assertIn(".fleet-repo-card", self.app_css)
        self.assertIn(".fleet-node-chip", self.app_css)
        self.assertIn(".fleet-cell", self.app_css)
        self.assertIn(".fleet-attr-popover", self.app_css)
        self.assertIn(".fleet-action", self.app_css)


if __name__ == "__main__":
    unittest.main()
