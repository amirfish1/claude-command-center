"""Contracts for the client-only conversation presentation modes."""

import json
import pathlib
import subprocess
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = PROJECT_ROOT / "static" / "index.html"
APP_JS = PROJECT_ROOT / "static" / "app.js"
APP_CSS = PROJECT_ROOT / "static" / "app.css"


def _javascript_function_source(name):
    source = APP_JS.read_text(encoding="utf-8")
    marker = f"function {name}("
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"{marker} is missing from static/app.js")
    brace = source.find("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"could not find the end of {name}")


def _run_javascript_function(name, *args):
    function_source = _javascript_function_source(name)
    script = (
        function_source
        + f"\nconst result = {name}(..."
        + json.dumps(args)
        + ");\nprocess.stdout.write(JSON.stringify(result));"
    )
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run_paginator(items, budget):
    return _run_javascript_function("paginatePresentationItems", items, budget)


class TestPresentationModeStatic(unittest.TestCase):
    def test_selector_is_clone_safe_and_exposes_three_modes(self):
        html = INDEX.read_text(encoding="utf-8")

        self.assertIn('data-role="presentation-toolbar"', html)
        self.assertEqual(html.count("data-presentation-mode="), 3)
        self.assertNotIn('id="presentationMode', html)

    def test_paginator_keeps_a_heading_with_the_following_block(self):
        pages = _run_paginator(
            [
                {"id": "intro", "weight": 7},
                {"id": "heading", "weight": 2, "keepWithNext": True},
                {"id": "body", "weight": 4},
            ],
            9,
        )

        self.assertEqual(
            [[item["id"] for item in page] for page in pages],
            [["intro"], ["heading", "body"]],
        )

    def test_paginator_keeps_an_oversized_atomic_block_on_one_page(self):
        pages = _run_paginator(
            [
                {"id": "intro", "weight": 3},
                {"id": "table", "weight": 30},
                {"id": "tail", "weight": 3},
            ],
            10,
        )

        self.assertEqual(
            [[item["id"] for item in page] for page in pages],
            [["intro"], ["table"], ["tail"]],
        )

    def test_paginator_starts_semantic_list_boundaries_on_fresh_pages(self):
        pages = _run_paginator(
            [
                {"id": "intro", "weight": 3},
                {"id": "number-1", "weight": 3, "breakBefore": True},
                {"id": "number-2", "weight": 3, "breakBefore": True},
            ],
            12,
        )

        self.assertEqual(
            [[item["id"] for item in page] for page in pages],
            [["intro"], ["number-1"], ["number-2"]],
        )

    def test_mode_two_budget_is_capped_for_tall_windows(self):
        self.assertLessEqual(
            _run_javascript_function("presentationPageBudget", {"clientHeight": 1600}),
            18,
        )

    def test_mode_state_is_pane_scoped_and_only_default_is_persisted(self):
        app_js = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function setPresentationMode(paneId, mode", app_js)
        self.assertIn("pane.dataset.presentationMode", app_js)
        self.assertIn("ccc-conv-presentation-mode", app_js)
        self.assertIn("function refreshPresentationForPane(paneId", app_js)
        self.assertIn("function stepPresentationSlide(paneId, delta)", app_js)

    def test_live_refresh_follows_tail_only_if_reader_was_already_there(self):
        self.assertTrue(
            _run_javascript_function("shouldFollowPresentationTail", 30, 31, True)
        )
        self.assertFalse(
            _run_javascript_function("shouldFollowPresentationTail", 29, 31, True)
        )
        self.assertFalse(
            _run_javascript_function("shouldFollowPresentationTail", 30, 31, False)
        )

    def test_conversation_end_control_targets_presentation_tail(self):
        attach = _javascript_function_source("attachConversationEndAffordance")
        update = _javascript_function_source("updateConversationEndAffordance")
        render_cursor = _javascript_function_source("renderPresentationCursor")

        self.assertIn("jumpPresentationToEnd(targetView)", attach)
        self.assertIn("function jumpPresentationToEnd(view)", APP_JS.read_text(encoding="utf-8"))
        self.assertIn("presentationDeck.length - 1", update)
        self.assertIn("updateConversationEndAffordance(view)", render_cursor)

    def test_dock_navigation_is_bound_at_the_control(self):
        ensure_dock = _javascript_function_source("ensurePresentationDock")

        self.assertIn("button.addEventListener('click'", ensure_dock)
        self.assertIn("stepPresentationSlide(pane.dataset.paneId", ensure_dock)

    def test_durable_and_streaming_renders_refresh_the_active_deck(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        durable_start = app_js.index("function renderConversationEvents(events, paneId, opts)")
        durable_end = app_js.index("\n  // CCC-185:", durable_start)
        streaming_start = app_js.index("function handleSpawnEvents(events, paneId, convId)")
        streaming_end = app_js.index("\n  function stopPkoodTailPoller", streaming_start)

        self.assertIn("refreshPresentationForPane(paneId", app_js[durable_start:durable_end])
        self.assertIn("refreshPresentationForPane(paneId", app_js[streaming_start:streaming_end])

    def test_mode_two_splits_lists_and_surfaces_live_activity(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        css = APP_CSS.read_text(encoding="utf-8")

        self.assertIn("function presentationListItems(list)", app_js)
        self.assertIn("breakBefore: ordered || index === 0", app_js)
        self.assertIn("function syncPresentationActivity(view)", app_js)
        self.assertIn("conv-presentation-activity", app_js)
        self.assertIn(".conv-presentation-activity", css)

    def test_presentation_css_has_stage_dock_and_right_rail_areas(self):
        css = APP_CSS.read_text(encoding="utf-8")

        self.assertIn(".conv-presentation-toolbar", css)
        self.assertIn(".conv-presentation-stage", css)
        self.assertIn(".conv-presentation-slide", css)
        self.assertIn(".conv-presentation-dock", css)
        self.assertIn('"present-toolbar rail"', css)
        self.assertIn('"present-dock    rail"', css)
        self.assertIn('"present-toolbar"', css)
        self.assertIn('"present-dock"', css)
        self.assertIn("prefers-reduced-motion: reduce", css)


if __name__ == "__main__":
    unittest.main()
