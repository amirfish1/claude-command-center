"""Pin the GH Pages demo bundle (`docs/demo/`) so it stays loadable.

The demo is a static snapshot of the dashboard served from GitHub Pages
(see issue #49). It has three moving parts:

1. `docs/demo/index.html` — the entry point with the demo-mode flag pre-set.
2. `docs/demo/static/*` — a snapshot of the real `static/` assets.
3. `docs/demo/api/**/*.json` — seeded fixtures the demo-mode fetch wrapper
   in `static/app.js` rewires `/api/*` requests to.

These tests verify the bundle is shaped correctly:

- Every JSON fixture parses.
- The demo entry HTML sets `window.__CCC_DEMO__ = true` BEFORE loading the
  real app.js (so the wrapper installs before any fetch fires).
- The fetch wrapper in `static/app.js` is present and isolated behind the
  flag (so real-mode behavior is untouched).
- The set of fixtures covers every endpoint the dashboard hits during
  initial page load.
"""
import json
import os
import pathlib
import re
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEMO_DIR = PROJECT_ROOT / "docs" / "demo"


class TestDemoBundle(unittest.TestCase):
    def test_demo_directory_exists(self):
        self.assertTrue(DEMO_DIR.is_dir(), f"missing {DEMO_DIR}")
        self.assertTrue((DEMO_DIR / "index.html").is_file())
        self.assertTrue((DEMO_DIR / "api").is_dir())
        self.assertTrue((DEMO_DIR / "static").is_dir())

    def test_all_demo_fixtures_parse_as_json(self):
        api = DEMO_DIR / "api"
        fixtures = sorted(api.rglob("*.json"))
        self.assertGreater(len(fixtures), 0, "no JSON fixtures found")
        for fx in fixtures:
            with self.subTest(fixture=fx.relative_to(PROJECT_ROOT)):
                json.loads(fx.read_text(encoding="utf-8"))

    def test_essential_endpoints_have_fixtures(self):
        """The dashboard hits these endpoints on initial paint. Without a
        fixture, the demo renders empty / errors out, so the test pins
        them. Add a fixture file when adding a new endpoint that's loaded
        at boot time."""
        essential = [
            "config.json",
            "features.json",
            "healthcheck.json",
            "identity.json",
            "registry.json",
            "version.json",
            "repo/list.json",
            "conversations/all.json",
            "issues/all.json",
            "group-chats/active.json",
            "archive/loading-status.json",
            "loading-status.json",
            "history/status.json",
        ]
        for rel in essential:
            with self.subTest(endpoint=rel):
                self.assertTrue(
                    (DEMO_DIR / "api" / rel).is_file(),
                    f"missing fixture: docs/demo/api/{rel}",
                )

    def test_demo_index_sets_flag_before_app_js(self):
        html = (DEMO_DIR / "index.html").read_text(encoding="utf-8")
        self.assertIn("__CCC_DEMO__", html,
                      "demo flag not set in docs/demo/index.html")
        flag_idx = html.index("window.__CCC_DEMO__ = true")
        app_idx = html.index("./static/app.js")
        self.assertLess(flag_idx, app_idx,
                        "demo flag must be set before app.js loads")

    def test_demo_wrapper_in_app_js_is_isolated_behind_flag(self):
        """The fetch wrapper must be a no-op when the flag isn't set,
        otherwise real-mode users would see /api/* rewritten to fixture
        paths and the dashboard would break."""
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("installDemoMode", app_js,
                      "demo-mode wrapper missing from static/app.js")
        # The wrapper must short-circuit when neither the flag nor the
        # query string opts in. Grep for the exact guard so a refactor
        # that loses it trips this test.
        self.assertRegex(
            app_js,
            r"if\s*\(\s*!fromFlag\s*&&\s*!fromQuery\s*\)\s*return\s*;",
            "demo-mode wrapper must bail out when neither flag nor query is set",
        )

    def test_conversations_fixture_has_realistic_demo_data(self):
        data = json.loads(
            (DEMO_DIR / "api" / "conversations" / "all.json").read_text("utf-8")
        )
        convs = data.get("conversations") or []
        self.assertGreaterEqual(len(convs), 8,
                                "demo should ship at least 8 sessions")
        # Cover the kanban: at least one live, one PR-open, one merged,
        # one archived, one waiting-for-input. If any of these are
        # missing, the board looks empty in spots that matter for the
        # screenshot.
        live = [c for c in convs if c.get("is_live")]
        merged = [c for c in convs if c.get("pr_state") == "MERGED"]
        archived = [c for c in convs if c.get("archived")]
        waiting = [c for c in convs if c.get("question_waiting")]
        self.assertGreaterEqual(len(live), 1, "need at least one live session")
        self.assertGreaterEqual(len(merged), 1, "need at least one merged PR")
        self.assertGreaterEqual(len(archived), 1, "need at least one archived")
        self.assertGreaterEqual(len(waiting), 1, "need at least one waiting")
        # Hygiene: every cwd / repo path must be fake. The OSS repo can't
        # ship real user paths in committed fixtures.
        for c in convs:
            cwd = c.get("session_cwd", "") or ""
            with self.subTest(session=c.get("session_id")):
                self.assertNotIn("/Users/", cwd,
                                 "demo fixtures must not contain real /Users/ paths")
                self.assertNotIn("/home/amir", cwd)


if __name__ == "__main__":
    unittest.main()
