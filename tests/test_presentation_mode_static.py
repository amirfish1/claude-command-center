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


def _run_paginator(items, budget):
    function_source = _javascript_function_source("paginatePresentationItems")
    script = (
        function_source
        + "\nconst result = paginatePresentationItems("
        + json.dumps(items)
        + ", "
        + str(budget)
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

    def test_mode_state_is_pane_scoped_and_only_default_is_persisted(self):
        app_js = APP_JS.read_text(encoding="utf-8")

        self.assertIn("function setPresentationMode(paneId, mode", app_js)
        self.assertIn("pane.dataset.presentationMode", app_js)
        self.assertIn("ccc-conv-presentation-mode", app_js)
        self.assertIn("function refreshPresentationForPane(paneId", app_js)
        self.assertIn("function stepPresentationSlide(paneId, delta)", app_js)

    def test_durable_and_streaming_renders_refresh_the_active_deck(self):
        app_js = APP_JS.read_text(encoding="utf-8")
        durable_start = app_js.index("function renderConversationEvents(events, paneId, opts)")
        durable_end = app_js.index("\n  // CCC-185:", durable_start)
        streaming_start = app_js.index("function handleSpawnEvents(events, paneId, convId)")
        streaming_end = app_js.index("\n  function stopPkoodTailPoller", streaming_start)

        self.assertIn("refreshPresentationForPane(paneId", app_js[durable_start:durable_end])
        self.assertIn("refreshPresentationForPane(paneId", app_js[streaming_start:streaming_end])

    def test_presentation_css_has_stage_dock_and_right_rail_areas(self):
        css = APP_CSS.read_text(encoding="utf-8")

        self.assertIn(".conv-presentation-toolbar", css)
        self.assertIn(".conv-presentation-stage", css)
        self.assertIn(".conv-presentation-slide", css)
        self.assertIn(".conv-presentation-dock", css)
        self.assertIn('"present-toolbar rail"', css)
        self.assertIn('"present-dock    rail"', css)
        self.assertIn("prefers-reduced-motion: reduce", css)


if __name__ == "__main__":
    unittest.main()
