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


def _run_group_packer(items, capacity):
    groups_source = _javascript_function_source("presentationItemGroups")
    packer_source = _javascript_function_source("paginatePresentationGroups")
    script = groups_source + "\n" + packer_source + "\n" + f"""
const items = {json.dumps(items)};
const capacity = {json.dumps(capacity)};
const groups = presentationItemGroups(items);
const pages = paginatePresentationGroups(
  groups,
  candidate => candidate.reduce((sum, item) => sum + item.height, 0) <= capacity,
);
process.stdout.write(JSON.stringify(pages.map(page => page.map(item => item.id))));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run_projection_path_fixture():
    path_source = _javascript_function_source("presentationElementPath")
    resolve_source = _javascript_function_source("presentationResolvePath")
    script = path_source + "\n" + resolve_source + r"""
function makeNode(name) {
  return {
    name,
    children: [],
    parentElement: null,
    contains(target) {
      if (target === this) return true;
      return this.children.some(child => child.contains(target));
    },
  };
}
function append(parent, child) {
  parent.children.push(child);
  child.parentElement = parent;
  return child;
}
const root = makeNode('root');
append(root, makeNode('first'));
const second = append(root, makeNode('second'));
const target = append(second, makeNode('target'));
const path = presentationElementPath(root, target);
const resolved = presentationResolvePath(root, path);
process.stdout.write(JSON.stringify({ path, resolved: resolved && resolved.name }));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class TestPresentationModeStatic(unittest.TestCase):
    def test_selector_exposes_only_off_and_present(self):
        html = INDEX.read_text(encoding="utf-8")

        self.assertIn('data-role="presentation-toolbar"', html)
        self.assertEqual(html.count("data-presentation-mode="), 2)
        self.assertIn('data-presentation-mode="off"', html)
        self.assertIn('data-presentation-mode="2"', html)
        self.assertNotIn('data-presentation-mode="1"', html)
        self.assertNotIn("Mode 1", html)
        self.assertNotIn('id="presentationMode', html)

    def test_legacy_mode_one_migrates_to_present(self):
        self.assertEqual(_run_javascript_function("normalizePresentationMode", "1"), "2")
        self.assertEqual(_run_javascript_function("normalizePresentationMode", "2"), "2")
        self.assertEqual(_run_javascript_function("normalizePresentationMode", "present"), "2")
        self.assertEqual(_run_javascript_function("normalizePresentationMode", "off"), "off")

    def test_projection_helpers_are_generic_and_clone_safe(self):
        source_root = _javascript_function_source("presentationSourceRoot")
        clone = _javascript_function_source("presentationCloneForProjection")
        roots_after_answer = _javascript_function_source(
            "presentationRootsAfterLatestAnswer"
        )

        self.assertEqual(
            _run_projection_path_fixture(),
            {"path": [1, 0], "resolved": "target"},
        )
        self.assertIn("parentElement !== view", source_root)
        self.assertIn("conv-presentation-stage", source_root)
        self.assertNotIn("pending", source_root)
        self.assertNotIn("tool", source_root.lower())

        for token in (
            "cloneNode(true)",
            "value",
            "checked",
            "indeterminate",
            "selectedIndex",
            "open",
            "scrollTop",
            "aria-labelledby",
            "aria-describedby",
            "aria-controls",
        ):
            self.assertIn(token, clone)
        self.assertIn("directChildren", roots_after_answer)
        self.assertIn("event.assistant", roots_after_answer)
        self.assertNotIn("conv-live-tool-inline", roots_after_answer)

    def test_mode_click_delegation_does_not_match_pane_state(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        handler_start = app_js.index("const modeButton = ev.target")
        handler_end = app_js.index("const navButton = ev.target", handler_start)
        handler = app_js[handler_start:handler_end]

        self.assertIn(
            "closest('.conv-presentation-mode[data-presentation-mode]')",
            handler,
        )
        self.assertNotIn("closest('[data-presentation-mode]')", handler)

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

    def test_compact_numbered_items_share_a_page(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function presentationItemGroups(items)", source)
        self.assertIn("function paginatePresentationGroups(groups, fits", source)

        pages = _run_group_packer(
            [
                {"id": "heading", "keepWithNext": True, "height": 2},
                {"id": "intro", "height": 2},
                {"id": "number-1", "height": 3},
                {"id": "number-2", "height": 3},
            ],
            capacity=12,
        )

        self.assertEqual(
            pages,
            [["heading", "intro", "number-1", "number-2"]],
        )

    def test_overflow_moves_a_whole_semantic_group(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function presentationItemGroups(items)", source)
        self.assertIn("function paginatePresentationGroups(groups, fits", source)

        pages = _run_group_packer(
            [
                {"id": "intro", "height": 5},
                {"id": "heading", "keepWithNext": True, "height": 2},
                {"id": "body", "height": 4},
            ],
            capacity=8,
        )

        self.assertEqual(
            pages,
            [["intro"], ["heading", "body"]],
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

    def test_mode_two_uses_layout_active_measurement_surface(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        css = APP_CSS.read_text(encoding="utf-8")

        self.assertIn("function ensurePresentationMeasureSurface(stage)", app_js)
        self.assertIn("function paginatePresentationItemsMeasured(view, turn)", app_js)
        build_deck = _javascript_function_source("buildPresentationDeck")
        ensure_surface = _javascript_function_source("ensurePresentationMeasureSurface")

        self.assertIn("paginatePresentationItemsMeasured(view, turn)", build_deck)
        self.assertIn("paginatePresentationItems(turn.blocks, budget)", build_deck)
        self.assertIn("aria-hidden", ensure_surface)
        self.assertIn(".conv-presentation-measure", css)
        self.assertIn("visibility: hidden", css)
        self.assertIn("pointer-events: none", css)

    def test_mode_two_opens_on_first_slide_of_latest_answer(self):
        deck = [
            {"dataset": {"answerIndex": "8", "partIndex": "0"}},
            {"dataset": {"answerIndex": "8", "partIndex": "1"}},
            {"dataset": {"answerIndex": "9", "partIndex": "0"}},
            {"dataset": {"answerIndex": "9", "partIndex": "1"}},
            {"dataset": {"answerIndex": "9", "partIndex": "2"}},
        ]

        self.assertEqual(
            _run_javascript_function("firstSlideOfLatestPresentationTurn", deck),
            2,
        )

    def test_repagination_preserves_the_current_semantic_item(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function presentationCursorIndex(deck, previousSlide, fallback)", source)
        deck = [
            {
                "dataset": {
                    "presentationKey": "answer:0",
                    "presentationItemKeys": "a,b",
                }
            },
            {
                "dataset": {
                    "presentationKey": "answer:1",
                    "presentationItemKeys": "c,d",
                }
            },
        ]
        previous = {
            "dataset": {
                "presentationKey": "answer:1",
                "presentationItemKeys": "b",
            }
        }

        self.assertEqual(
            _run_javascript_function("presentationCursorIndex", deck, previous, 1),
            0,
        )

    def test_mode_two_repaginates_after_meaningful_slot_resize(self):
        source = APP_JS.read_text(encoding="utf-8")
        self.assertIn("function ensurePresentationResizeObserver(view, paneId)", source)
        observer = _javascript_function_source("ensurePresentationResizeObserver")

        self.assertIn("new ResizeObserver", observer)
        self.assertIn("Math.abs(width - previous.width) < 4", observer)
        self.assertIn(
            "refreshPresentationForPane(paneId, { preserveCursor: true })",
            observer,
        )
        self.assertIn("disconnectPresentationResizeObserver(view)", source)

    def test_mode_state_is_pane_scoped_and_only_default_is_persisted(self):
        app_js = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function setPresentationMode(paneId, mode", app_js)
        self.assertIn("pane.dataset.presentationMode", app_js)
        self.assertIn("ccc-conv-presentation-mode", app_js)
        self.assertIn("function refreshPresentationForPane(paneId", app_js)
        self.assertIn("function stepPresentationSlide(paneId, delta)", app_js)

    def test_escape_exits_mode_two_even_from_the_composer(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        scheduler = _javascript_function_source("schedulePresentationEscape")

        self.assertIn("if (!ev || ev.key !== 'Escape') return false", scheduler)
        self.assertIn("setTimeout(() =>", scheduler)
        self.assertIn("if (ev.defaultPrevented) return", scheduler)
        self.assertIn("!== '2'", scheduler)
        self.assertIn("setPresentationMode(paneId, 'off')", scheduler)

        handler_start = app_js.index(
            "document.addEventListener('keydown', (ev) =>",
            app_js.index("function schedulePresentationEscape"),
        )
        handler_end = app_js.index("let _presentationResizeTimer", handler_start)
        handler = app_js[handler_start:handler_end]
        self.assertLess(
            handler.index("schedulePresentationEscape(ev)"),
            handler.index("const target = ev.target"),
        )

        composer_start = app_js.index("$convInput.addEventListener('keydown', (e) =>")
        composer_end = app_js.index("// Various callsites", composer_start)
        composer_handler = app_js[composer_start:composer_end]
        self.assertIn(
            "normalizePresentationMode(presentationPane.dataset.presentationMode) === '2'",
            composer_handler,
        )
        self.assertLess(
            composer_handler.index("normalizePresentationMode"),
            composer_handler.index("sendEscToTerminal()"),
        )

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
        ensure_stage = _javascript_function_source("ensurePresentationStage")
        ensure_dock = _javascript_function_source("ensurePresentationDock")

        self.assertIn("button.addEventListener('click'", ensure_stage)
        self.assertIn("stepPresentationSlide(pane.dataset.paneId", ensure_stage)
        self.assertNotIn("data-presentation-nav", ensure_dock)

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
        self.assertIn("breakBefore: index === 0", app_js)
        self.assertNotIn("breakBefore: ordered || index === 0", app_js)
        self.assertIn("function syncPresentationActivity(view)", app_js)
        self.assertIn("conv-presentation-activity", app_js)
        self.assertIn(".conv-presentation-activity", css)

    def test_presentation_css_has_stage_dock_and_right_rail_areas(self):
        css = APP_CSS.read_text(encoding="utf-8")

        self.assertIn(".conv-presentation-toolbar", css)
        self.assertIn(".conv-presentation-stage", css)
        self.assertIn(".conv-presentation-slide", css)
        self.assertIn(".conv-presentation-dock", css)
        self.assertIn(".conv-presentation-side-nav", css)
        self.assertIn("top: 50%", css)
        self.assertIn('"present-toolbar rail"', css)
        self.assertIn('"present-dock    rail"', css)
        self.assertIn('"present-toolbar"', css)
        self.assertIn('"present-dock"', css)
        self.assertIn("prefers-reduced-motion: reduce", css)


if __name__ == "__main__":
    unittest.main()
