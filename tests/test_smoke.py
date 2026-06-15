"""Lightweight smoke tests that don't depend on optional plugins.

Anything Morning-specific lives in `tests/test_morning.py` which is
gitignored alongside the Morning plugin itself; CI never sees it.
"""
import importlib
import inspect
import fcntl
import json
import os
import pathlib
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import unittest
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestServerImports(unittest.TestCase):
    def test_server_imports_without_morning(self):
        """server.py must import cleanly even when the optional Morning
        plugin (morning.py, morning_store.py, etc.) isn't on disk. The
        plugin is gitignored — CI clones see no morning files at all."""
        # Ensure no stale module cached from a prior test run.
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "__version__"))
        self.assertIsInstance(server.__version__, str)
        self.assertRegex(server.__version__, r"^\d+\.\d+\.\d+")

    def test_open_session_in_claude_desktop_rejects_bad_input(self):
        """The helper exists and rejects empty / non-UUID session IDs
        without trying to spawn `open(1)`."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "open_session_in_claude_desktop"))
        # Empty
        r = server.open_session_in_claude_desktop("")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
        # Not a UUID
        r = server.open_session_in_claude_desktop("not-a-uuid")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_open_session_in_codex_desktop_rejects_bad_input(self):
        """The helper exists and rejects empty / non-Codex session IDs
        without trying to spawn `open(1)`."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "open_session_in_codex_desktop"))
        with mock.patch.object(server.subprocess, "Popen") as popen:
            r = server.open_session_in_codex_desktop("")
            self.assertFalse(r["ok"])
            self.assertIn("error", r)
            with mock.patch.object(server, "_is_codex_session", return_value=False):
                r = server.open_session_in_codex_desktop("not-codex")
            self.assertFalse(r["ok"])
            self.assertIn("error", r)
            popen.assert_not_called()

    def test_repo_ship_flow_is_wired(self):
        """The "Push all" ship flow exposes its server helpers and the static
        UI carries the control + endpoints. Import-level only — no git runs."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        for name in ("start_repo_ship", "repo_ship_status",
                     "_ship_candidate_sessions", "_run_ship_flow"):
            self.assertTrue(hasattr(server, name), name)
        # The Tier-A nudge must steer sessions toward path-scoped commits and
        # away from the index-sweeping forms that clobber sibling sessions.
        self.assertIn("--only", server.TIER_A_COMMIT_NUDGE)
        self.assertIn("git add -A", server.TIER_A_COMMIT_NUDGE)
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-role="ship-push-all"', app_js)
        self.assertIn("/api/repo/ship", app_js)
        self.assertIn("_startShipPushAll", app_js)
        self.assertIn(".conv-folder-ship", app_css)
        # Editor/cache cruft is junk (gitignore material), not "app/deploy
        # review" — otherwise Push all parks on it every time. The cache/ prefix
        # is anchored so a legit src/cache/ deeper in the tree isn't swept.
        self.assertTrue(server._ship_is_junk("apps/x/.obsidian/app.json"))
        self.assertTrue(server._ship_is_junk("cache/projects.json"))
        self.assertFalse(server._ship_is_junk("apps/x/src/cache/util.ts"))
        # Resolving the last handoff action must carry through to a push, so the
        # integrate step is a standalone helper shared by the flow + the action
        # handler (the "I clicked skip and nothing happened" fix).
        self.assertTrue(hasattr(server, "_ship_integrate"))
        # A diverged branch is auto-reconciled in an ISOLATED throwaway worktree
        # (cherry-pick local commits onto origin → push → ff the shared clone),
        # falling back to the manual hand-off only on a real conflict. The
        # reconcile helper exists and _ship_integrate dispatches to it on the
        # diverged branch. String-level only — no real git/worktree runs here.
        self.assertTrue(hasattr(server, "_ship_reconcile_diverged"))
        self.assertIn("_ship_reconcile_diverged",
                      inspect.getsource(server._ship_integrate))
        # Loose repo-root scratch (a Puppeteer snapshot.js + its snapshot.png
        # output) is dev one-off noise, not app/deploy code — it must NOT park
        # Push all as "review". Anything under a source dir still does.
        self.assertNotEqual(server._ship_classify_remaining("snapshot.png"), "review")
        self.assertNotEqual(server._ship_classify_remaining("snapshot.js"), "review")
        self.assertEqual(server._ship_classify_remaining("snapshot.png"), "infra")
        self.assertEqual(server._ship_classify_remaining("snapshot.js"), "infra")
        self.assertEqual(server._ship_classify_remaining("apps/x/page.tsx"), "review")

    def test_stale_sidecar_does_not_count_as_live(self):
        """A Claude liveness sidecar only counts while fresh. The hooks never
        delete these markers on session end, so a stale marker must NOT keep a
        long-dead session flagged live (regression: sessions idle for days were
        still reported is_live)."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        sid = "11111111-2222-3333-4444-555555555555"  # not a real engine session
        with tempfile.TemporaryDirectory() as d:
            dpath = pathlib.Path(d)
            engine_patches = [
                mock.patch.object(server, name, return_value=False)
                for name in ("_is_codex_session", "_is_cursor_session",
                             "_is_gemini_session", "_is_antigravity_session",
                             "_is_kilo_session")
            ]
            with mock.patch.object(server, "SIDECAR_STATE_DIR", dpath), \
                 mock.patch.object(server, "_live_engine_session_ids", return_value=set()):
                for p in engine_patches:
                    p.start()
                try:
                    marker = dpath / f"{sid}.json"
                    marker.write_text("{}")
                    # Fresh marker → live.
                    self.assertTrue(server._archive_session_is_live(sid))
                    # Stale marker (older than the window) → not live.
                    old = time.time() - (server._SIDECAR_LIVE_WINDOW + 600)
                    os.utime(marker, (old, old))
                    self.assertFalse(server._archive_session_is_live(sid))
                finally:
                    for p in engine_patches:
                        p.stop()

    def test_ship_index_attribution_is_wired_and_degrades(self):
        """The conversation-index attribution layer is defined, the verdict +
        ship-flow consult it, and a missing/erroring index degrades silently to
        git-only (None) — never raises. No real index is touched: we monkeypatch
        search_conversation_history to mimic the index-missing/error contract."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        # Helper exists and is referenced by both consumers (source-level — we
        # don't run the daemon flow, just prove the wiring).
        self.assertTrue(hasattr(server, "_ship_index_attribution"))
        self.assertIn("_ship_index_attribution", inspect.getsource(server._ship_review_verdict))
        self.assertIn("_ship_index_attribution", inspect.getsource(server._run_ship_flow))
        # Index missing → {"error": ...} contract → None, no raise.
        with mock.patch.object(server, "search_conversation_history",
                               return_value={"error": "no index", "results": []}):
            self.assertIsNone(server._ship_index_attribution("/tmp/repo", "static/app.js"))
        # The reader raising → still None (never load-bearing).
        with mock.patch.object(server, "search_conversation_history",
                               side_effect=RuntimeError("boom")):
            self.assertIsNone(server._ship_index_attribution("/tmp/repo", "static/app.js"))

    def test_claude_append_prompt_discourages_blocking_recursive_grep(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        args = server._claude_session_state_args()

        self.assertEqual(args[0], "--append-system-prompt")
        self.assertIn("Do not run `grep -r`", args[1])
        self.assertIn(".claude/logs/*.stdin", args[1])
        self.assertIn("<session-state>", args[1])

    def test_spawn_defaults_drive_omitted_spawn_engine_and_model(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.SPAWN_DEFAULTS_FILE
            server.SPAWN_DEFAULTS_FILE = pathlib.Path(td) / "spawn-defaults.json"
            try:
                with mock.patch.object(server, "_antigravity_cli_configured_model", return_value=""):
                    saved = server._save_spawn_defaults({
                        "engine": "codex",
                        "models": {
                            "claude": "sonnet-4-6",
                            "codex": "gpt-5-codex",
                            "cursor": "composer-2.5",
                            "antigravity": "",
                        },
                    })
                    self.assertTrue(saved["ok"])

                    engine, model = server._spawn_request_engine_and_model({})
                    self.assertEqual(engine, "codex")
                    self.assertEqual(model, "gpt-5-codex")

                    engine, model = server._spawn_request_engine_and_model({"engine": "claude"})
                    self.assertEqual(engine, "claude")
                    self.assertEqual(model, "sonnet-4-6")

                    engine, model = server._spawn_request_engine_and_model({
                        "engine": "claude",
                        "model": "opus-4-7",
                    })
                    self.assertEqual(engine, "claude")
                    self.assertEqual(model, "opus-4-7")

                    engine, model = server._spawn_request_engine_and_model({"engine": "cursor"})
                    self.assertEqual(engine, "cursor")
                    self.assertEqual(model, "composer-2.5")

                    engine, model = server._spawn_request_engine_and_model({"engine": "gemini"})
                    self.assertEqual(engine, "antigravity")
                    self.assertIsNone(model)

                    engine, model = server._spawn_request_engine_and_model({"engine": "bogus"})
                    self.assertIsNone(engine)
                    self.assertIsNone(model)
            finally:
                server.SPAWN_DEFAULTS_FILE = old_file

    def test_morning_disabled_when_plugin_absent(self):
        """If morning.py isn't importable, MORNING_ENABLED must be False
        no matter what CCC_ENABLE_MORNING says."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        # Force-flag morning as missing by setting an env that doesn't
        # affect the actual import attempt — we just verify the gate
        # short-circuits when _MORNING_IMPORTABLE is False.
        server = importlib.import_module("server")
        if not server._MORNING_IMPORTABLE:
            self.assertFalse(server.MORNING_ENABLED,
                             "MORNING_ENABLED must be False when plugin missing")

    def test_page_annotation_is_bounded_and_persisted(self):
        """Browser annotations should store local context without requiring
        screenshot support or touching the real user state directory."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.ANNOTATIONS_FILE
            old_dir = server.ANNOTATION_SCREENSHOT_DIR
            server.ANNOTATIONS_FILE = pathlib.Path(td) / "annotations.json"
            server.ANNOTATION_SCREENSHOT_DIR = pathlib.Path(td) / "annotation-screenshots"
            try:
                # html_excerpt / nearby_text / selected_text / document_rect
                # used to be persisted alongside each annotation. They were
                # dropped because the screenshot + selector + note are enough
                # for Claude to act, and the raw outerHTML / surrounding
                # paragraphs added kilobytes of noise to every wire payload.
                result = server.create_annotation({
                    "note": "Check this button state",
                    "url": "http://127.0.0.1:8090/",
                    "title": "Claude Command Center",
                    "rect": {"x": 10, "y": 20, "width": 120, "height": 32},
                    "element": {
                        "tag": "button",
                        "selector": "#annotationStartBtn",
                        "text": "Annotate",
                    },
                    "html_excerpt": "<button>" + ("x" * 9000) + "</button>",
                    "nearby_text": "surrounding context",
                    "capture_screen": False,
                })
                self.assertTrue(result["ok"])
                saved = server.list_annotations(limit=10)
                self.assertEqual(saved["count"], 1)
                ann = saved["annotations"][0]
                self.assertEqual(ann["note"], "Check this button state")
                self.assertEqual(ann["element"]["selector"], "#annotationStartBtn")
                self.assertNotIn("html_excerpt", ann)
                self.assertNotIn("nearby_text", ann)
                self.assertNotIn("selected_text", ann)
                self.assertNotIn("document_rect", ann)
                self.assertNotIn("screenshot_path", ann)
            finally:
                server.ANNOTATIONS_FILE = old_file
                server.ANNOTATION_SCREENSHOT_DIR = old_dir

    def test_screen_annotation_saves_local_screenshot(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.ANNOTATIONS_FILE
            old_dir = server.ANNOTATION_SCREENSHOT_DIR
            server.ANNOTATIONS_FILE = pathlib.Path(td) / "annotations.json"
            server.ANNOTATION_SCREENSHOT_DIR = pathlib.Path(td) / "annotation-screenshots"
            try:
                result = server.create_annotation({
                    "note": "Look at this screen region",
                    "source": "screen-capture",
                    "screenshot_b64": "dGVzdC1pbWFnZS1ieXRlcw==",
                })
                self.assertTrue(result["ok"])
                ann = result["annotation"]
                self.assertEqual(ann["source"], "screen-capture")
                shot = pathlib.Path(ann["screenshot_path"])
                self.assertTrue(shot.is_file())
                self.assertEqual(shot.read_bytes(), b"test-image-bytes")
            finally:
                server.ANNOTATIONS_FILE = old_file
                server.ANNOTATION_SCREENSHOT_DIR = old_dir

    def test_breadcrumb_has_popout_button_wired_to_existing_helper(self):
        """The conversation breadcrumb gains a pop-out button that reuses the
        existing drag-to-out-of-window helper. The button is delegated so it
        survives every updatePaneHeader innerHTML rewrite, and it is hidden
        when the page is itself the popout (CONV_POPOUT_MODE)."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-role="ccc-breadcrumb-popout"', app_js)
        self.assertIn("CONV_POPOUT_MODE ? ''", app_js)
        self.assertIn("openConversationPopout(convId, null, null)", app_js)
        self.assertIn(".ccc-breadcrumb-popout", app_css)

    def test_terminal_pick_a_repo_label_is_clickable(self):
        """The terminal panel's "Pick a repo" placeholder used to be
        passive text — user had no way to actually pick a repo from
        there. The placeholder now opens window.cccOpenRepoPicker
        (the existing repo-picker modal exposed for non-IIFE
        callers)."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        # App.js exposes the picker for inline callers outside the IIFE.
        self.assertIn("window.cccOpenRepoPicker = openRepoPickerModal", app_js)
        # Terminal panel wires the click to the exposed picker.
        self.assertIn("cccOpenRepoPicker", index_html)
        self.assertIn("is-pickable", index_html)

    def test_annotation_text_strips_lone_surrogates(self):
        """An unpaired UTF-16 surrogate code point (U+D800..U+DFFF)
        coming from the browser's clipboard / selection APIs used to
        sail through _annotation_text and then break the downstream
        Anthropic API call with "no low surrogate in string". The
        sanitizer must drop lone surrogates AND leave real astral
        chars (paired surrogates collapsed into a single Python code
        point) untouched."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        # Lone high surrogate in the middle of normal text. Build it at
        # runtime so the source file itself never contains a literal
        # backslash-u surrogate escape that can poison a Claude transcript
        # when another agent reads this test.
        lone_high = chr(0xD83D)
        dirty = "fix the bug " + lone_high + " in row 42"
        cleaned = server._annotation_text(dirty)
        self.assertNotIn(lone_high, cleaned)
        # Result must round-trip through json + utf-8 — that's the
        # failure surface the Anthropic API rejects.
        json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
        # A real astral character (😀 = U+1F600, one Python code point)
        # must survive — only LONE surrogates are stripped.
        kept = server._annotation_text("hi 😀 there")
        self.assertIn("😀", kept)
        # _inject_text_into_session uses the same strip so a missed
        # entry point still can't leak a surrogate to the API.
        self.assertIn("_strip_lone_surrogates", inspect.getsource(server._inject_text_into_session))

    def test_annotation_notes_render_screenshots(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("ann-note-shot", app_js)
        self.assertIn("/api/local-image?path=", app_js)
        self.assertIn("data-ann-open-session", app_js)
        self.assertIn("function annOpenNewSessionWithContext", app_js)
        self.assertIn("enterNewSessionMode(text)", app_js)
        self.assertIn("data-ann-ux-queue", app_js)
        self.assertIn("function annOpenUxFixesQueue", app_js)
        self.assertIn("/api/annotations/ux-fixes-queue", app_js)
        self.assertIn("Add to UX fixes queue", app_js)
        self.assertIn("Session ID: ", app_js)
        self.assertIn("persistAnnotation", app_js)
        self.assertIn("annCaptureRegionB64", app_js)
        self.assertIn("annBeginTabCaptureRequest", app_js)
        self.assertIn("annCaptureDomRegionB64", app_js)
        self.assertIn("data-ann-enable-shot", app_js)
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("screenshot_warning", server_py)
        self.assertNotIn("other tool", app_js.lower())

    def test_ux_fixes_queue_progress_badge_is_rendered_from_queue_api(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("/api/ux-fixes/list", app_js)
        self.assertIn("claimed_by", app_js)
        self.assertIn("conv-ux-fix-progress", app_js)
        self.assertIn(".conv-item .conv-ux-fix-progress", app_css)

    def test_sidebar_refresh_defers_while_dragging(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function deferSidebarRenderIfDragging", app_js)
        self.assertIn(".flow-node.dragging", app_js)
        self.assertIn(".flow-board.is-zooming", app_js)
        self.assertIn("beginSidebarDrag();", app_js)
        self.assertIn("function markFlowZoomInteraction", app_js)
        self.assertIn("markFlowZoomInteraction(targetEl);", app_js)
        self.assertIn("markFlowZoomInteraction(ev.currentTarget);", app_js)
        self.assertIn("if (deferSidebarRenderIfDragging()) return;", app_js)

    def test_flow_new_session_drafts_wait_for_play(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("ccc-flow-draft-sessions", app_js)
        self.assertIn("function createFlowDraftSession", app_js)
        self.assertIn("function playFlowDraftSession", app_js)
        self.assertIn("function flowRepoPathForNode", app_js)
        self.assertIn("ccc-flow-collapsed-nodes", app_js)
        self.assertIn("function toggleFlowNodeCollapsed", app_js)
        self.assertIn("function setFlowAllNodesCollapsed", app_js)
        self.assertIn("function ensureFlowDefaultRepoCollapsed", app_js)
        self.assertIn("function carryFlowPendingSpawnNode", app_js)
        self.assertIn("function flowParentForCollapse", app_js)
        self.assertIn("function flowHasAncestorNode", app_js)
        self.assertIn("function flowSessionSignal", app_js)
        self.assertIn("function archiveFlowSession", app_js)
        self.assertIn("let flowSelectedNodes", app_js)
        self.assertIn("function startFlowRangeSelection", app_js)
        self.assertIn("FLOW_CANVAS_PAD_RATIO = 0.30", app_js)
        self.assertIn("FLOW_CANVAS_PAD_MIN_PX = 260", app_js)
        self.assertIn("function flowCanvasPadding", app_js)
        self.assertIn("data-flow-pad-x", app_js)
        self.assertIn("flowCanvasPaddingFromCanvas", app_js)
        self.assertIn("canvas.addEventListener('pointerdown'", app_js)
        self.assertIn("flowSelectedNodeIds", app_js)
        self.assertIn("const isGroupDrag = dragItems.length > 1;", app_js)
        self.assertIn("data-flow-action=\"play-draft-session\"", app_js)
        self.assertIn("data-flow-action=\"archive-session\"", app_js)
        self.assertIn("data-flow-action=\"toggle-collapse\"", app_js)
        self.assertIn("data-flow-action=\"collapse-all\"", app_js)
        self.assertIn("flowRecencyButtonHtml('1d', '1d'", app_js)
        self.assertIn("flowHasCollapsedAncestor(nodeId, repoId)", app_js)
        self.assertIn("function flowIsVisibleSession", app_js)
        self.assertIn("if (col === 'backlog') return false;", app_js)
        self.assertIn("if (col === 'archived' && !flowIncludeArchived && !pinnedInFlow) return false;", app_js)
        self.assertIn("flow_parent_node_id", app_js)
        self.assertIn("return ts ? relativeTime(ts) : '';", app_js)
        self.assertIn("if (value === 'flow') return FLOW_POPOUT_MODE ? 'flow' : 'list';", app_js)
        self.assertIn("if (value === 'board' || value === 'kanban') return 'board';", app_js)
        self.assertIn("return localStorage.getItem('ccc-kanban-view') === 'true' ? 'board' : 'list';", app_js)
        self.assertIn("New session draft connected here", app_js)
        self.assertIn("if (isFlowView()) createFlowDraftSession();", app_js)
        self.assertIn(".flow-node-archive", app_css)
        self.assertIn(".flow-selection-box", app_css)
        self.assertIn(".flow-node.selected", app_css)
        self.assertIn("--flow-grid-size", app_css)
        self.assertIn("background-position:", app_css)

    def test_flow_work_item_inspector_wired(self):
        """Repo/object Flow nodes open a Markdown-backed work-item inspector,
        and work-item cards use automatic accents plus parsed Flow fields."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("FLOW_STATE_DIR", server_py)
        self.assertIn("/api/flow/node", server_py)
        self.assertIn("/api/flow/node/refresh", server_py)
        self.assertIn("/api/flow/index", server_py)
        self.assertIn("function openFlowNodeInspector", app_js)
        self.assertIn("flowInspectorPayloadFromNode", app_js)
        self.assertIn("flowInspectorRefresh", app_js)
        self.assertIn("data-flow-inspector-action=\"refresh\"", app_js)
        self.assertIn("flow-node-work-item", app_js)
        self.assertIn("flowAccentStyle", app_js)
        self.assertIn("flowWorkItemCardHtml", app_js)
        self.assertIn("accentSeed: flowColorSeedForNode(nodeId, obj.id)", app_js)
        self.assertIn("function _isAbsoluteLocalPath", app_js)
        self.assertIn("[row.repo_path, row.folder_path, row.spawn_cwd, row.session_cwd, row.cwd]", app_js)
        self.assertIn("absolute || row.repo_path || row.spawn_cwd || row.session_cwd || row.cwd || row.folder_path", app_js)
        self.assertIn("function flowIndexedRepoEntries", app_js)
        self.assertIn("indexedRepoEntries.forEach(entry =>", app_js)
        self.assertIn("ensureRepoGroup(entry.repo_path).metaEntry = entry;", app_js)
        self.assertIn(".flow-node-work-item", app_css)
        self.assertIn(".flow-inspector", app_css)
        self.assertIn("--flow-accent", app_css)

    def test_flow_state_helpers_create_save_refresh_markdown(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as td:
            old_dir = server.FLOW_STATE_DIR
            old_index = server.FLOW_INDEX_FILE
            server.FLOW_STATE_DIR = pathlib.Path(td) / "flow"
            server.FLOW_INDEX_FILE = server.FLOW_STATE_DIR / "index.json"
            try:
                payload = {"kind": "object", "object_id": "obj-test", "title": "Release work"}
                result, status = server._flow_load_node_payload(payload, create=True)
                self.assertEqual(status, 200)
                self.assertTrue(result["ok"])
                self.assertIn("## Flow fields", result["content"])
                self.assertIn("ccc:auto:start status-table", result["content"])
                edited = result["content"].replace(
                    "Write the current state here.",
                    "Manual summary survives refresh.",
                )
                saved, status = server._flow_save_node_payload({
                    **payload,
                    "content": edited,
                    "mtime": result["mtime"],
                })
                self.assertEqual(status, 200)
                refreshed, status = server._flow_refresh_node_payload({
                    **payload,
                    "items": [{
                        "title": "Fix layout",
                        "status": "working",
                        "session": "abc12345",
                        "updated": "just now",
                        "notes": "main",
                    }],
                })
                self.assertEqual(status, 200)
                self.assertIn("Manual summary survives refresh.", refreshed["content"])
                self.assertIn("Fix layout", refreshed["content"])
                self.assertIn("abc12345", refreshed["content"])
                index = server._flow_index_payload()
                self.assertEqual(index["count"], 1)
                self.assertEqual(index["entries"][0]["fields"]["status"], "Active")
            finally:
                server.FLOW_STATE_DIR = old_dir
                server.FLOW_INDEX_FILE = old_index

    def test_mobile_breakpoint_covers_phones_landscape(self):
        """Mobile single-column layout (conv list full-width, conv pane
        slides in as overlay, back button shows) must trigger on phones
        in BOTH portrait and landscape. iPhone Pro Max landscape is
        932px so the breakpoint must be ≥ 932; 950px gives a small
        safety margin. JS _mobileMQ and the relevant CSS @media blocks
        must use the same threshold so isMobile() and the slide-in
        overlay agree."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("matchMedia('(max-width: 950px)')", app_js)
        # CSS for the back-button visibility + main-overlay must match.
        self.assertIn("@media (max-width: 950px)", app_css)
        self.assertIn('id="mobileBackBtn"', index_html)
        self.assertNotIn('data-role="pane-mobile-back"', index_html)
        self.assertNotIn("mobile-show-main .conv-split[data-orientation=\"\"] .conv-pane > .conv-pane-header", app_css)
        self.assertNotIn("_captureRailEl(document.getElementById('mobileBackBtn'))", app_js)

    def test_flow_group_chat_nodes_and_drop(self):
        """Group chats render as a third node kind on the flow board
        (alongside repo and object), have a "+ Group chat" toolbar
        button that triggers createEmptyGroupChat, click opens the
        existing group-chat reader, and dropping a session node onto
        a group-chat node calls addSessionToGroupChat — same outcome
        as dragging a conv-list row onto a chat row in the sidebar."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-flow-action="add-group-chat"', app_js)
        self.assertIn("createEmptyGroupChat()", app_js)
        self.assertIn("flow-node-group-chat", app_js)
        # Records carry gc-path / gc-id / gc-mode for the drop handler.
        self.assertIn("groupChatPath", app_js)
        self.assertIn("data-gc-path", app_js)
        # Drop handler: session → group-chat node calls
        # addSessionToGroupChat instead of the parent-link operation.
        self.assertIn("targetIsGroupChat", app_js)
        self.assertIn("addSessionToGroupChat(gcPath, sid, displayName, gcId)", app_js)
        # Click on a group-chat node opens the reader.
        self.assertIn("openGroupChatReader(gcPath, topic, gcMode", app_js)
        # CSS for the distinct accent.
        self.assertIn(".flow-node-group-chat", app_css)

    def test_flow_popout_reader_toggle(self):
        """Flow popout has a button to show/hide a conversation reader
        on the right side. Toggling writes ccc-flow-popout-reader to
        localStorage; the body class flow-popout-reader splits the
        viewport into flow-left + conv-pane-right via CSS."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-flow-action="toggle-reader"', app_js)
        self.assertIn("flowPopoutReaderEnabled", app_js)
        self.assertIn("ccc-flow-popout-reader", app_js)
        self.assertIn("body.flow-popout.flow-popout-reader", app_css)

    def test_sidebar_search_hides_group_chat_rows(self):
        """Sidebar search is for sessions/issues. Active and archived
        group-chat rows are navigation chrome, so they should not appear
        in In progress or Archived while a query is active."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("const _hideGroupChatsForSearch = !!_qActive;", app_js)
        self.assertIn(
            "const _gcItems = _hideGroupChatsForSearch ? [] : (_gcActiveChats || []).map(chat => {",
            app_js,
        )
        self.assertIn("const _gcCountForSection = _hideGroupChatsForSearch ? 0", app_js)
        self.assertIn("const _archivedGroupChatsForRender = _hideGroupChatsForSearch", app_js)
        self.assertIn("const hasGc = !q && _gcActiveChats && _gcActiveChats.length > 0;", app_js)

    def test_flow_popout_button_and_mode_wired(self):
        """Flow toolbar gets a pop-out button (skipped inside the popout
        itself). Click → openFlowPopout → window.open with ccc_popout=flow.
        Body class + popout-only mode routing send the popped-out tab
        straight into the Flow view without polluting main-window storage."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        # Boot flag + body class.
        self.assertIn("FLOW_POPOUT_MODE", app_js)
        self.assertIn("'ccc_popout') === 'flow'", app_js)
        self.assertIn("document.body.classList.add('flow-popout')", app_js)
        self.assertIn("if (FLOW_POPOUT_MODE) return 'flow';", app_js)
        self.assertNotIn("localStorage.setItem('ccc-session-view', 'flow')", app_js)
        self.assertIn("localStorage.setItem('ccc-session-view', sidebarViewMode === 'flow' ? 'list' : sidebarViewMode)", app_js)
        # Helper + URL shape.
        self.assertIn("function openFlowPopout", app_js)
        self.assertIn("let _flowPopoutWindow = null;", app_js)
        self.assertIn("function focusFlowPopoutWindow", app_js)
        self.assertIn("if (focusFlowPopoutWindow()) return true;", app_js)
        self.assertIn("_flowPopoutWindow = popup;", app_js)
        self.assertIn("u.searchParams.set('ccc_popout', 'flow')", app_js)
        self.assertIn("u.searchParams.set('title', 'Flow')", app_js)
        # Toolbar button + click wiring.
        self.assertIn('data-flow-action="popout"', app_js)
        self.assertIn('openFlowPopout(null)', app_js)
        # CSS gates the popout layout.
        self.assertIn("body.flow-popout", app_css)
        self.assertIn(".conv-list-panel > *:not(#flowBoard)", app_css)

    def test_tool_results_attach_to_matching_tool_call(self):
        """Tool result previews should render under the command/tool whose
        tool_use id matches, with a visible result/error label."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("function toolCallForResult", app_js)
        self.assertIn("tc.dataset.toolUseId || '') === toolUseId", app_js)
        self.assertIn("data-tool-use-id=\"' + escapeAttr(toolUseId) + '\"", app_js)
        self.assertIn("const last = toolCallForResult(_currentToolGroup, ev.tool_use_id || '');", app_js)
        self.assertIn("out.dataset.resultLabel = ", app_js)
        self.assertIn("toolResultOutputLabel(last, ", app_js)
        self.assertIn("Command result", app_js)
        self.assertIn("Command error", app_js)
        self.assertIn(".tool-result-output::before", app_css)
        self.assertIn("content: attr(data-result-label);", app_css)

    def test_organize_is_incremental_with_overlap_resolve(self):
        """Per user request: Organize must keep repos/objects where they
        are, only moving them when absolutely needed to avoid overlap,
        and the total pixel displacement should be minimized. Strategy:
        anchor each chain at its root's current position, then
        greedy-resolve overlaps by pushing the less-displaced chain by
        the minimum right/down amount. Untouched chains seed from the
        legacy bin-pack cursor."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        # New R10 rule documented in the algorithm comment block.
        self.assertIn("R10. INCREMENTAL", app_js)
        # Per-cluster anchoring — every cluster (root AND nested) starts
        # at its own parent's current offsetLeft/offsetTop, not a
        # chain-relative derived offset.
        self.assertIn("INCREMENTAL ORGANIZE, per-cluster", app_js)
        self.assertIn("parentNode.offsetLeft", app_js)
        self.assertIn("parentNode.offsetTop", app_js)
        self.assertIn("clusterPlacements", app_js)
        # Unplaced nested clusters seed BELOW the ancestor (not right of
        # it) per the 2026-06-05 layout rule: nested objects/repos stack
        # vertically under their parent, not horizontally.
        self.assertIn("ancPlace.y + ancPlace.h + CLUSTER_MARGIN", app_js)
        # Overlap-resolve picks the worst overlap each iteration and
        # pushes the cluster with smaller displacement.
        self.assertIn("worstArea", app_js)
        self.assertIn("totalPushPx", app_js)
        self.assertIn("aDisp <= bDisp", app_js)

    def test_flow_record_mode_and_organize_plus_wired(self):
        """Flow has a Record mode that stores before/after layout examples
        and an Organize+ button that replays the best matching example."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("FLOW_ORGANIZE_PLUS_KEY = 'ccc-flow-organize-plus-examples'", app_js)
        self.assertIn("function flowSnapshotNodePositions", app_js)
        self.assertIn("function toggleFlowOrganizeRecord", app_js)
        self.assertIn("function applyFlowOrganizePlus", app_js)
        self.assertIn('data-flow-action="record-organize"', app_js)
        self.assertIn('data-flow-action="organize-plus"', app_js)
        self.assertIn("flowOrganizePlusExamples.unshift(example)", app_js)
        self.assertIn("organizeFlowSessions(targetEl, { silent: true });", app_js)
        self.assertIn("updateFlowOrganizeRecordState(targetEl);", app_js)
        self.assertIn(".flow-toolbar-btn.is-recording", app_css)

    def test_inline_rename_force_renders_even_when_search_focused(self):
        """Inline session rename commit() must force the sidebar render —
        the rename input itself is a text input, and after Enter/blur
        focus is either on it or has moved to the search box (also
        text). Either case trips shouldPauseSidebarRender, which would
        suppress the post-commit render and leave the title stuck in
        edit mode."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        # The rename commit's renderSidebar call must pass force:true.
        self.assertIn(
            "renderSidebar(filterConversations($convSearch.value), { force: true });",
            app_js,
        )
        # And the surrounding function should still be the inline-rename
        # commit (so we can be sure the right call site got forced).
        self.assertIn("function startInlineRename", app_js)
        # Defensive: the inline-rename comment mentions the pause-guard
        # rationale so a future refactor can't silently drop the force.
        self.assertIn("trips shouldPauseSidebarRender", app_js)

    def test_flow_edges_are_selectable_deletable_draggable(self):
        """Flow edges (the lines connecting child nodes to their parent)
        are now selectable with a click, deletable with Backspace, and
        draggable from one parent to another. Each edge renders as a
        <g class="flow-edge"> containing a wide transparent hit path
        plus a thin visible line."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        # Edge selection state + DOM contract.
        self.assertIn("_selectedFlowEdgeChildId", app_js)
        self.assertIn("function selectFlowEdge", app_js)
        self.assertIn("function clearFlowEdgeSelection", app_js)
        self.assertIn("'flow-edge'", app_js)
        self.assertIn("'flow-edge-hit'", app_js)
        self.assertIn("'flow-edge-line'", app_js)
        # Backspace handler + delete helper.
        self.assertIn("function deleteFlowEdge", app_js)
        self.assertIn("ev.key === 'Backspace'", app_js)
        # Drag-to-reparent.
        self.assertIn("function startEdgeReparentDrag", app_js)
        self.assertIn("function reparentFlowNode", app_js)
        self.assertIn("is-drop-target", app_js)
        # CSS for the hit-area, selected state, drag ghost, drop target.
        self.assertIn(".flow-edge-hit", app_css)
        self.assertIn(".flow-edge.is-selected", app_css)
        self.assertIn(".flow-edge-line.is-dragging", app_css)
        self.assertIn(".flow-node.is-drop-target", app_css)

    def test_mermaid_code_blocks_render_as_svg(self):
        """```mermaid fenced blocks render as SVG instead of raw code.
        renderCodeBlock emits a .mermaid-block carrier whose .mermaid-source
        pre is the offline fallback; a lazy lib loader replaces the
        block with rendered SVG on first appearance. Hooked into the
        existing conv-view MutationObserver so every render path picks
        it up for free."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        # Detection inside renderCodeBlock
        self.assertIn("(lang || '').toLowerCase() === 'mermaid'", app_js)
        self.assertIn('"mermaid-block"', app_js.replace("'", '"'))
        # Lazy loader + render helper
        self.assertIn("_loadMermaid", app_js)
        self.assertIn("_renderMermaidBlocks", app_js)
        self.assertIn("cdn.jsdelivr.net/npm/mermaid", app_js)
        # Hooked into the existing observer that already does RTL tagging.
        self.assertIn("_renderMermaidBlocks(n)", app_js)
        # CSS for the carrier + SVG container.
        self.assertIn(".mermaid-block", app_css)
        self.assertIn(".mermaid-svg", app_css)

    def test_tts_rate_knob_is_live_and_persisted(self):
        """User wanted a live knob that adjusts TTS speed while it's
        playing, plus a persisted default. The rate is no longer
        baked-in to 1.25 — it's read from localStorage at init,
        controlled by a range input next to the TTS button, and the
        change cancels + re-speaks from the current word boundary so
        the new rate kicks in within ~180ms instead of waiting for
        the next turn."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        # Markup
        self.assertIn('id="convTtsRateControl"', index_html)
        self.assertIn('id="convTtsRateDown"', index_html)
        self.assertIn('id="convTtsRateUp"', index_html)
        # State + persistence
        self.assertIn("let _ttsRate", app_js)
        self.assertIn("ccc-tts-rate", app_js)
        self.assertNotIn("const _TTS_RATE =", app_js)
        # Live restart wiring — click listener + restart helper.
        self.assertIn("_restartTtsAtCurrentPosition", app_js)
        self.assertIn("addEventListener('click'", app_js)
        # CSS
        self.assertIn(".tts-rate-control", app_css)

    def test_first_existing_dir_picks_first_real_path(self):
        """Codex / claude rows used to surface a tail-extracted worktree
        cwd that had since been deleted, so Launch built
        `cd '/.../no-such-worktree' && resume` and dropped the user in
        their home dir. _first_existing_dir prefers the first cwd
        candidate that still exists on disk."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        srv = importlib.import_module("server")
        self.assertTrue(hasattr(srv, "_first_existing_dir"))
        with tempfile.TemporaryDirectory() as td:
            real = pathlib.Path(td, "real-repo")
            real.mkdir()
            missing = pathlib.Path(td, "deleted-worktree-sGH1nB")
            # missing intentionally never created
            self.assertEqual(srv._first_existing_dir(str(missing), str(real)), str(missing.parent / "real-repo"))
            # All missing → None.
            other_missing = pathlib.Path(td, "also-missing")
            self.assertIsNone(srv._first_existing_dir(str(missing), str(other_missing)))
            # Empty / None args skip cleanly.
            self.assertEqual(srv._first_existing_dir("", None, str(real)), str(real))

    def test_launch_falls_back_to_repo_when_cwd_missing(self):
        """buildResumeCommand used to emit `cd '/.../no-such-worktree' &&
        resume` for non-.claude/worktrees paths that don't exist on
        disk — `cd` fails, `&&` blocks the resume. Falls back to the
        session's repoPath, and drops the `cd` entirely if no fallback
        is known."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        # Fallback to currentSession.repoPath is the documented escape.
        self.assertIn("currentSession.repoPath", app_js)
        # No-cd path returns the bare resumeCmd.
        self.assertIn("return resumeCmd;", app_js)

    def test_macapp_cmd_backtick_cycles_windows(self):
        """Cmd+` should switch between CCC windows (main ↔ flow popout ↔
        conv popout). Default Cmd+` works for AppKit apps with multiple
        windows, but WKWebView swallows the keystroke before AppKit
        sees it — so we surface explicit Window-menu items for forward
        and shift-reverse cycling, bound at the menu-bar level."""
        macapp = pathlib.Path(PROJECT_ROOT, "scripts", "macapp", "main.swift").read_text(encoding="utf-8")
        self.assertIn('cycleWindowsForward', macapp)
        self.assertIn('cycleWindowsReverse', macapp)
        self.assertIn('"Cycle Through Windows"', macapp)
        # Bound to Cmd+` and Cmd+Shift+` in the Window menu.
        self.assertIn('keyEquivalent: "`"', macapp)

    def test_macapp_does_not_quit_when_last_window_closes(self):
        """Closing a conversation pop-out (or the main window momentarily)
        must NOT terminate the app — that kills the server we spawned
        and yanks every other open window. Mirrors Safari / Mail
        behavior: Cmd+Q is the explicit quit path; closing windows
        leaves the app running. Dock-click re-opens main."""
        macapp = pathlib.Path(PROJECT_ROOT, "scripts", "macapp", "main.swift").read_text(encoding="utf-8")
        # Must explicitly return false, not true. A bare "return true" in
        # this delegate method is the bug we just fixed.
        self.assertIn(
            "func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {",
            macapp,
        )
        self.assertNotRegex(
            macapp,
            r"applicationShouldTerminateAfterLastWindowClosed\(_ sender: NSApplication\) -> Bool \{\s*return true",
        )
        # Dock-click reopen must rebuild a window when the last one closed.
        self.assertIn(
            "func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool",
            macapp,
        )

    def test_sending_sidebar_render_bypasses_textarea_pause_guard(self):
        """Hitting Send leaves focus in the conv input textarea, which
        normally pauses sidebar renders (so background pollers can't
        yank the list around mid-type). But the user's own send IS a
        user-initiated event and must paint the "Sending…" pill in the
        sidebar row immediately. markSessionSending/clearSessionSending
        therefore pass {force: true} to renderSidebar, and renderSidebar
        skips the periodic-pause guard when force is set."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("renderSidebar(filterConversations($convSearch.value), { force: true });", app_js)
        self.assertIn("function renderSidebar(convs, opts)", app_js)
        self.assertIn("if (!(opts && opts.force) && shouldPauseSidebarRender()) return;", app_js)

    def test_conv_pct_badge_is_clickable_compact_shortcut(self):
        """The context-% badge on each conv row is a one-click shortcut to
        /compact. Click -> confirm -> run the engine-aware compact helper. The
        row-click handler must EXCLUDE the badge so clicking it doesn't
        also open the conversation."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn('data-role="conv-pct-compact"', app_js)
        self.assertIn("conv-pct-badge is-actionable", app_js)
        # Badge must be in the row-click exclusion list so the row
        # itself doesn't open underneath the /compact confirm.
        self.assertIn('ev.target.closest(\'[data-role="conv-pct-compact"]\')', app_js)
        # Confirm + POST shape — compact is a command operation, not a
        # generic text inject. Both Claude and Codex now route through
        # /api/session/compact (postCompactSession); Codex compaction runs via
        # the app-server thread/compact/start RPC, not a literal text inject.
        self.assertIn("window.confirm(msg)", app_js)
        self.assertIn("postRunCompactForSession(sid, source)", app_js)
        self.assertIn("postCompactSession", app_js)
        self.assertIn("/api/session/compact", app_js)
        self.assertIn(".conv-pct-badge.is-actionable", app_css)

    def test_codex_slash_commands_are_wired_as_codex_commands(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("const CODEX_SLASH_FALLBACK_COMMANDS = [", app_js)
        self.assertIn("{ name: '/compact', description: 'Summarize the visible conversation to free tokens' }", app_js)
        self.assertIn("return source === 'codex' ? CODEX_SLASH_FALLBACK_COMMANDS : SLASH_FALLBACK_COMMANDS;", app_js)
        self.assertIn("compactCommand && isCompactionCapableSource(currentSession.source)", app_js)
        self.assertNotIn("Codex sessions do not use Claude slash commands", app_js)
        self.assertIn("const failurePrefix = compactCommand ? '/compact failed'", app_js)

    def test_slash_command_picker_selects_on_press(self):
        """Mouse/touch selection must commit on press, before focus refreshes
        or document-level click handlers can interfere with the popup."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function selectSlashCommandMenuItemFromEvent(ev)", app_js)
        self.assertIn("target && target.closest ? target : (target && target.parentElement)", app_js)
        self.assertIn("el.closest('.slash-command-item')", app_js)
        self.assertIn("_slashMenuEl.addEventListener('pointerdown'", app_js)
        self.assertIn("_slashMenuEl.addEventListener('mousedown'", app_js)
        self.assertIn("_slashMenuEl.addEventListener('touchstart'", app_js)
        self.assertIn("_slashMenuEl.addEventListener('click'", app_js)
        self.assertIn("return commitSlashCommandSelection(_slashMenuInput);", app_js)
        self.assertIn("function syncSlashCommandMenuSelection()", app_js)
        self.assertIn("btn.classList.toggle('selected', selected);", app_js)
        self.assertIn("syncSlashCommandMenuSelection();", app_js)
        self.assertNotIn("renderSlashCommandMenu(input, _slashMenuItems, q);", app_js)

    def test_relayed_question_renders_inline_in_conv_view(self):
        """The "Session is asking a question" surface is an inline card
        mounted inside the active conversation view (not a body-level
        modal overlay) so it inherits the conv pane's font stack and
        lives where the user is reading. Guarded by class names so a
        future refactor that accidentally reintroduces the modal trips
        this test."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        # Inline card class + data-role must exist in JS and CSS.
        self.assertIn('ccc-inline-question', app_js)
        self.assertIn('data-role="ccc-inline-question"', app_js)
        self.assertIn(".ccc-inline-question", app_css)
        # Mount must target the active conv view, not document.body.
        self.assertIn("$view.appendChild(modal)", app_js)
        self.assertIn("showRelayedQuestionInline", app_js)
        self.assertIn("closeRelayedQuestionInline", app_js)
        # Old modal shell must be gone — no more body-level overlay.
        self.assertNotIn("cccQuestionModal", app_js)
        self.assertNotIn('upd-overlay ccc-question-modal', app_js)
        # The old .ccc-question-modal selector block must not declare
        # any actual rules (comment mentions of the migrated name are
        # fine; an active selector means the modal CSS came back).
        self.assertNotIn(".ccc-question-modal {", app_css)
        self.assertNotIn(".ccc-question-modal .", app_css)
        # Fonts inherit from the conv pane (rather than the modal's own).
        self.assertIn(".ccc-inline-question {", app_css)
        self.assertIn("font: inherit;", app_css)

    def test_live_question_indicator_renders_prompt_and_options(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        self.assertIn("function liveQuestionDetailHtml", app_js)
        self.assertIn("liveStatus.questionText", app_js)
        self.assertIn("questionPreamble", app_js)
        self.assertIn("question_preamble", app_js)
        self.assertIn("questionOptionDetails", app_js)
        self.assertIn("question_option_details", app_js)
        self.assertIn("liveQuestionOptionParts", app_js)
        self.assertIn("liveQuestionDisplayOptions", app_js)
        self.assertIn("handleLiveQuestionActionClick", app_js)
        self.assertIn("data-live-question-action", app_js)
        self.assertIn("Type something", app_js)
        self.assertIn("Chat about this", app_js)
        self.assertIn("cl-question-options", app_js)
        self.assertIn(".conv-live-tool-inline .cl-question-detail", app_css)
        self.assertIn(".conv-live-tool-inline .cl-question-preamble", app_css)
        self.assertIn(".conv-live-tool-inline .cl-question-options", app_css)
        self.assertIn("flex-direction: column", app_css)
        self.assertIn("cl-question-option-btn", app_css)
        self.assertIn("cl-question-option-desc", app_css)

    def test_live_refresh_has_active_group_chat_pill(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        server_py = pathlib.Path(PROJECT_ROOT, "server.py").read_text(encoding="utf-8")
        self.assertIn("activeGroupChatPill", index_html)
        self.assertIn("Active Group chat", index_html)
        self.assertIn(".active-group-chat-pill", app_css)
        self.assertIn("function updateActiveGroupChatPill", app_js)
        self.assertIn("function openActiveGroupChatPillTarget", app_js)
        self.assertIn("orchestrator_timer_active", app_js)
        self.assertIn("orchestrator_last_trigger_at", server_py)

    def test_codex_steer_button_is_distinct_from_send(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        self.assertIn('id="convSteerBtn"', index_html)
        self.assertIn(".conv-input-bar .steer-btn", app_css)
        self.assertIn("sendToTerminal('p1', 'steer')", app_js)
        self.assertIn("mode: injectMode", app_js)

    def test_cursor_engine_is_wired_in_static_ui(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        self.assertIn('<option value="cursor">cursor</option>', index_html)
        self.assertIn('<option value="cursor">Cursor</option>', index_html)
        self.assertIn("'cursor', 'antigravity'", app_js)
        self.assertIn("/api/sessions/spawn-cursor", app_js)
        self.assertIn("Auto (default)", app_js)
        self.assertIn("composer-2.5-fast", app_js)
        self.assertIn("renderCursorLogHtml", app_js)
        self.assertIn("function isCursorUsageLimitFailure", app_js)
        self.assertIn("Cursor usage limit hit. Cursor says:", app_js)
        self.assertIn(".source-badge.cursor", app_css)
        self.assertIn(".event.system.send-failure", app_css)

    def test_cursor_sidebar_visibility_rejects_bad_input(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        
        # Empty session id
        self.assertFalse(server._ensure_cursor_session_visible(""))
        
        # Non-UUID session id
        self.assertFalse(server._ensure_cursor_session_visible("not-a-uuid"))
        
        # Valid UUID but no cwd/spawn_entry
        self.assertFalse(server._ensure_cursor_session_visible("00000000-0000-4000-8000-000000000001"))

    def test_ensure_cursor_session_visible_creates_store_db(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(server.Path, "home", return_value=pathlib.Path(td)):
                sid = "00000000-0000-4000-8000-000000000001"
                spawn_entry = {
                    "cwd": td,
                    "name": "Test Cursor Session",
                    "started": "20260601T120000",
                }
                res = server._ensure_cursor_session_visible(sid, spawn_entry=spawn_entry)
                self.assertTrue(res)
                
                import hashlib
                project_hash = hashlib.md5(str(pathlib.Path(td).resolve()).encode("utf-8")).hexdigest()
                db_path = pathlib.Path(td) / ".cursor" / "chats" / project_hash / sid / "store.db"
                self.assertTrue(db_path.is_file())
                
                import sqlite3
                conn = sqlite3.connect(str(db_path))
                row = conn.execute("SELECT value FROM meta WHERE key = '0'").fetchone()
                conn.close()
                self.assertIsNotNone(row)
                data = json.loads(bytes.fromhex(row[0]).decode("utf-8"))
                self.assertEqual(data["agentId"], sid)
                self.assertEqual(data["name"], "Test Cursor Session")

    def test_ensure_cursor_session_visible_registers_composer(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(server.Path, "home", return_value=pathlib.Path(td)):
                import urllib.parse
                
                # Mock platforms to Darwin so it uses standard macOS App Support dir in tests
                with mock.patch("sys.platform", "darwin"):
                    # Create workspaceStorage and workspace.json
                    ws_dir = pathlib.Path(td) / "Library" / "Application Support" / "Cursor" / "User" / "workspaceStorage" / "test-workspace-id"
                    ws_dir.mkdir(parents=True, exist_ok=True)
                    
                    ws_json = ws_dir / "workspace.json"
                    project_dir = pathlib.Path(td) / "my-project"
                    project_dir.mkdir(parents=True, exist_ok=True)
                    
                    with open(ws_json, "w", encoding="utf-8") as f:
                        json.dump({"folder": project_dir.as_uri()}, f)
                        
                    ws_db = ws_dir / "state.vscdb"
                    import sqlite3
                    conn = sqlite3.connect(str(ws_db))
                    conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
                    # Seed with some existing composer data
                    conn.execute(
                        "INSERT INTO ItemTable (key, value) VALUES ('composer.composerData', ?)",
                        (json.dumps({"allComposers": []}),)
                    )
                    conn.commit()
                    conn.close()
                    
                    # Create globalStorage and state.vscdb
                    global_dir = pathlib.Path(td) / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage"
                    global_dir.mkdir(parents=True, exist_ok=True)
                    global_db = global_dir / "state.vscdb"
                    conn = sqlite3.connect(str(global_db))
                    conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
                    conn.execute(
                        "INSERT INTO ItemTable (key, value) VALUES ('composer.composerHeaders', ?)",
                        (json.dumps([]),)
                    )
                    conn.commit()
                    conn.close()
                    
                    sid = "00000000-0000-4000-8000-000000000001"
                    spawn_entry = {
                        "cwd": str(project_dir),
                        "name": "Test Cursor Session",
                        "started": "20260601T120000",
                    }
                    res = server._ensure_cursor_session_visible(sid, spawn_entry=spawn_entry)
                    self.assertTrue(res)
                    
                    # Assert workspace db updated
                    conn = sqlite3.connect(str(ws_db))
                    row = conn.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerData'").fetchone()
                    conn.close()
                    self.assertIsNotNone(row)
                    ws_data = json.loads(row[0])
                    self.assertEqual(len(ws_data["allComposers"]), 1)
                    self.assertEqual(ws_data["allComposers"][0]["composerId"], sid)
                    self.assertEqual(ws_data["allComposers"][0]["name"], "Test Cursor Session")
                    
                    # Assert global db updated
                    conn = sqlite3.connect(str(global_db))
                    row = conn.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerHeaders'").fetchone()
                    conn.close()
                    self.assertIsNotNone(row)
                    global_data = json.loads(row[0])
                    self.assertEqual(len(global_data), 1)
                    self.assertEqual(global_data[0]["composerId"], sid)
                    self.assertEqual(global_data[0]["workspaceIdentifier"]["id"], "test-workspace-id")



class TestPrStateResolution(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.server._PR_STATE_CACHE.clear()

    def tearDown(self):
        self.server._PR_STATE_CACHE.clear()

    def test_pr_state_falls_back_to_gh_api(self):
        url = f"https://github.com/octo-org/demo-repo/pull/{25}"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1:3] == ["pr", "view"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            if cmd[1] == "api":
                self.assertIn("repos/octo-org/demo-repo/pulls/25", cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="MERGED\n", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        with mock.patch.object(self.server.shutil, "which",
                               return_value="/opt/homebrew/bin/gh"), \
             mock.patch.object(self.server.subprocess, "run",
                               side_effect=fake_run):
            self.assertEqual(self.server._get_pr_state(url), "MERGED")
            self.assertEqual(self.server._get_pr_state(url), "MERGED")

        self.assertEqual(len(calls), 2, "second lookup should hit cache")
        cached = self.server._PR_STATE_CACHE[url]
        self.assertEqual(cached["state"], "MERGED")
        self.assertEqual(cached["ttl"], self.server._PR_STATE_TTL)

    def test_pr_state_failures_use_short_ttl(self):
        url = f"https://github.com/octo-org/demo-repo/pull/{25}"

        def fail_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        with mock.patch.object(self.server.shutil, "which",
                               return_value="/opt/homebrew/bin/gh"), \
             mock.patch.object(self.server.subprocess, "run",
                               side_effect=fail_run):
            self.assertIsNone(self.server._get_pr_state(url))

        cached = self.server._PR_STATE_CACHE[url]
        self.assertIsNone(cached["state"])
        self.assertEqual(cached["ttl"], self.server._PR_STATE_FAILURE_TTL)


class TestRunScript(unittest.TestCase):
    def test_run_script_syntax_is_valid(self):
        script = pathlib.Path(PROJECT_ROOT, "run.sh")
        result = subprocess.run(["bash", "-n", str(script)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_script_help_advertises_launchd_service(self):
        script = pathlib.Path(PROJECT_ROOT, "run.sh")
        result = subprocess.run(["bash", str(script), "--help"],
                                cwd=PROJECT_ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--install-service", result.stdout)
        self.assertIn("--uninstall-service", result.stdout)
        self.assertIn("--service-status", result.stdout)


class TestRepoContextHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="ccc-repo-context-home-")
        self._prev_home = os.environ.get("HOME")
        self._prev_ux_fixes_queue_file = os.environ.get("UX_FIXES_QUEUE_FILE")
        os.environ["HOME"] = str(pathlib.Path(self.tmp_home).resolve())
        self.ux_fixes_queue_file = pathlib.Path(
            self.tmp_home, ".claude", "command-center", "ux-fixes-queue.json"
        ).resolve()
        os.environ["UX_FIXES_QUEUE_FILE"] = str(self.ux_fixes_queue_file)
        for mod in ("server", "morning", "morning_store", "ux_fixes_queue"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.repo = pathlib.Path(self.tmp_home, "demo-repo").resolve()
        self.repo.mkdir()
        (self.repo / ".git").mkdir()

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._prev_home
        if self._prev_ux_fixes_queue_file is None:
            os.environ.pop("UX_FIXES_QUEUE_FILE", None)
        else:
            os.environ["UX_FIXES_QUEUE_FILE"] = self._prev_ux_fixes_queue_file
        for mod in ("server", "morning", "morning_store", "ux_fixes_queue"):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def test_valid_repo_path_is_accepted(self):
        self.assertEqual(self.server.resolve_repo_path(str(self.repo)), str(self.repo))

    def test_ux_fixes_queue_file_is_isolated_to_test_home(self):
        self.assertEqual(
            self.server.ux_fixes_queue.QUEUE_FILE,
            self.ux_fixes_queue_file,
        )
        result = self.server.enqueue_annotation_ux_fixes_queue("Annotation: isolated")
        self.assertTrue(result["ok"])
        self.assertTrue(self.ux_fixes_queue_file.exists())

    def test_repo_path_with_plus_resolves_when_query_decoded_to_space(self):
        """A repo with `+` in its name arrives as a space via URL query-string
        decoding (`+` → ` `). resolve_repo_path() must recover by trying `+`
        variants instead of forcing every caller to encode as %2B."""
        plus_repo = pathlib.Path(self.tmp_home, "BYM+Finie").resolve()
        plus_repo.mkdir()
        (plus_repo / ".git").mkdir()
        # 1. Exact path still works.
        self.assertEqual(self.server.resolve_repo_path(str(plus_repo)), str(plus_repo))
        # 2. The +→space mangled form (what a URL query carrying `+`
        #    produces) resolves to the real repo.
        mangled = str(plus_repo).replace("+", " ")
        self.assertEqual(self.server.resolve_repo_path(mangled), str(plus_repo))
        # 3. Genuinely missing paths still 400.
        with self.assertRaises(self.server.RepoContextError):
            self.server.resolve_repo_path(str(pathlib.Path(self.tmp_home, "no such repo")))

    def test_find_conversations_honors_relocation_budget(self):
        """A repo with many transcripts whose recorded cwd no longer exists
        must NOT spend its entire budget walking the filesystem for every
        dead worktree. With the per-request relocation budget in place (and
        the on-disk cache cold), find_conversations() should return within
        a few seconds even with hundreds of seeded sessions.

        This is the perf guard for the BYM+Finie regression where 128
        missing cwds + per-session os.walk burnt ~40s on every cold scan.
        """
        seed_count = 200
        target_seconds = 3.0  # generous CI bound; warm calls return <2s
        # Build a fake project dir matching the slug encoder.
        slug = self.server._encode_project_slug(self.repo)
        project_dir = pathlib.Path(self.tmp_home, ".claude", "projects", slug)
        project_dir.mkdir(parents=True, exist_ok=True)
        bogus_cwd = str(pathlib.Path(self.tmp_home, "deleted-worktrees", "no-such"))
        # Each seeded JSONL records a cwd that doesn't exist on disk — the
        # exact shape that used to trigger the expensive relocation walk.
        for i in range(seed_count):
            sid = f"00000000-0000-4000-8000-{i:012d}"
            entry = {
                "type": "user",
                "sessionId": sid,
                "cwd": bogus_cwd,
                "timestamp": "2026-06-01T00:00:00.000Z",
                "gitBranch": "main",
                "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            }
            (project_dir / f"{sid}.jsonl").write_text(
                json.dumps(entry) + "\n",
                encoding="utf-8",
            )
        # Tight budget so the test fails loudly if it ever regresses.
        prev_budget = os.environ.get("CCC_CWD_RELOCATION_BUDGET_S")
        os.environ["CCC_CWD_RELOCATION_BUDGET_S"] = "0.5"
        try:
            start = time.monotonic()
            rows = self.server.find_conversations(
                str(self.repo), include_old=True
            )
            elapsed = time.monotonic() - start
        finally:
            if prev_budget is None:
                os.environ.pop("CCC_CWD_RELOCATION_BUDGET_S", None)
            else:
                os.environ["CCC_CWD_RELOCATION_BUDGET_S"] = prev_budget
        self.assertGreater(
            len(rows), 0,
            "scan should still return rows even when relocation budget trips",
        )
        self.assertLess(
            elapsed, target_seconds,
            f"find_conversations took {elapsed:.2f}s for {seed_count} seeded sessions; budget is {target_seconds}s",
        )

    def test_repo_path_plus_fallback_keeps_real_space_repo(self):
        """A repo with a real space in its name still resolves directly — the
        fallback only kicks in when the as-given path does not exist."""
        space_repo = pathlib.Path(self.tmp_home, "Foo Bar").resolve()
        space_repo.mkdir()
        (space_repo / ".git").mkdir()
        self.assertEqual(self.server.resolve_repo_path(str(space_repo)), str(space_repo))

    def test_session_registry_accepts_native_claude_binary_path(self):
        sid = "00000000-0000-4000-8000-000000000001"
        sessions_dir = pathlib.Path(self.server.SESSIONS_REGISTRY)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "123.json").write_text(json.dumps({
            "pid": 123,
            "sessionId": sid,
            "cwd": str(self.repo),
            "kind": "bg",
        }))
        native_bin = pathlib.Path(
            self.tmp_home,
            ".local",
            "share",
            "claude",
            "versions",
            "2.1.144",
        )

        def fake_run(args, **kwargs):
            if args == ["ps", "-A", "-o", "pid=,comm="]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=f"123 {native_bin}\n456 /usr/bin/python3\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {args}")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            registry = self.server._load_session_registry()

        self.assertIn(sid, registry)
        self.assertEqual(registry[sid]["pid"], 123)

    def test_daemon_socket_allows_claude_tmp_path(self):
        allowed = f"/tmp/cc-daemon-{os.getuid()}/abc/spare/session.pty.sock"
        denied = f"/tmp/not-cc-daemon-{os.getuid()}/session.pty.sock"

        self.assertTrue(self.server._daemon_socket_path_allowed(allowed))
        self.assertFalse(self.server._daemon_socket_path_allowed(denied))

    def test_background_agent_pty_inject_frames_paste_and_submit(self):
        base = pathlib.Path("/tmp", f"cc-daemon-{os.getuid()}")
        base.mkdir(parents=True, exist_ok=True)
        frames = []
        errors = []

        def recv_exact(conn, n):
            chunks = []
            remaining = n
            while remaining:
                chunk = conn.recv(remaining)
                if not chunk:
                    raise EOFError("socket closed")
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

        with tempfile.TemporaryDirectory(dir=base) as td:
            sock_path = pathlib.Path(td, "test.pty.sock")
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(str(sock_path))
            server_sock.listen(1)
            server_sock.settimeout(2)

            def accept_frames():
                try:
                    conn, _ = server_sock.accept()
                    with conn:
                        conn.settimeout(2)
                        for _ in range(2):
                            header = recv_exact(conn, 5)
                            size = int.from_bytes(header[:4], "big")
                            kind = header[4]
                            frames.append((kind, recv_exact(conn, size)))
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=accept_frames)
            thread.start()
            try:
                # Delivery confirmation polls a real transcript (CCC-113);
                # this test asserts the wire framing only.
                with mock.patch.object(self.server, "_transcript_gains_text", return_value=True):
                    result = self.server._inject_bg_agent_via_pty_socket(
                        {"pid": 123, "sessionId": "sid", "ptySock": str(sock_path)},
                        "hi\x1b\nthere",
                    )
                thread.join(timeout=2)
            finally:
                server_sock.close()

        self.assertFalse(errors)
        self.assertTrue(result["ok"])
        self.assertEqual(frames, [
            (0, b"\x1b[200~hi\nthere\x1b[201~"),
            (0, b"\r"),
        ])

    def test_strips_ccc_session_state_instruction_from_visible_text(self):
        text = (
            "now to 00000000-0000-4000-8000-000000000001: "
            "/Users/example/.claude/command-center/pasted-images/paste-1.png\n\n"
            "Before your final reply, end with a block formatted EXACTLY like this "
            "(the Claude Command Center dashboard parses it):\n"
            "<session-state>\n"
            "DID: <one sentence>\n"
            "INSIGHT: <one sentence>\n"
            "NEXT_STEP_USER: <one sentence>\n"
            "</session-state>"
        )
        self.assertEqual(
            self.server._strip_ccc_session_state_instruction(text),
            "now to 00000000-0000-4000-8000-000000000001: "
            "/Users/example/.claude/command-center/pasted-images/paste-1.png",
        )

    def test_terminal_inject_strips_ccc_session_state_instruction(self):
        text = (
            "follow up\n\n"
            "Before your final reply, end with a block formatted EXACTLY like this:\n"
            "<session-state>\n"
            "DID: <one sentence>\n"
            "INSIGHT: <one sentence>\n"
            "NEXT_STEP_USER: <one sentence>\n"
            "</session-state>"
        )
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "/dev/ttys001",
                     "terminal_app": "Terminal",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "keystroke"},
             ) as inject:
            result = self.server._inject_text_into_session(
                "00000000-0000-4000-8000-000000000001",
                text,
            )
        self.assertTrue(result["ok"])
        inject.assert_called_once_with("/dev/ttys001", "Terminal", "follow up")

    def test_terminal_inject_queues_when_live_session_is_busy(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "_terminal_input_queue_has_pending",
                     return_value=True,
                 ), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": "/dev/ttys001",
                         "terminal_app": "Terminal",
                         "status": "busy",
                         "pid": 123,
                     },
                 ), \
                 mock.patch.object(
                     self.server,
                     "_find_live_spawn_entry_for_session",
                     return_value=None,
                 ), \
                 mock.patch.object(
                     self.server,
                     "_spawn_entry_active_tool_child",
                     return_value={"pid": 23456, "command": "grep -r"},
                 ), \
                 mock.patch.object(
                     self.server,
                     "_terminal_input_queue_has_pending",
                     return_value=True,
                 ), \
                 mock.patch.object(self.server, "_write_stream_json_user_message") as write:
                result = self.server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["status"], "busy")
            self.assertEqual(result["via"], "terminal-queued")
            write.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_compact_inject_delegates_to_compact_helper(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(
            self.server,
            "compact_session_context",
            return_value={"ok": True, "compact": True},
        ) as compact, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server._inject_text_into_session(sid, "/compact")

        self.assertTrue(result["ok"])
        compact.assert_called_once_with(sid, _from_terminal_queue=False)
        resume.assert_not_called()

    def test_compact_live_terminal_submits_slash_command(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "/dev/ttys001",
                     "terminal_app": "Terminal",
                     "status": "idle",
                 },
             ), \
             mock.patch.object(self.server, "_pending_ask_user_question_for_session", return_value=False), \
             mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=False), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact", return_value="/tmp/backup.jsonl") as backup, \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control"},
             ) as inject:
            result = self.server.compact_session_context(sid)

        self.assertTrue(result["ok"])
        self.assertTrue(result["compact"])
        self.assertEqual(result["backup_path"], "/tmp/backup.jsonl")
        backup.assert_called_once_with(sid)
        inject.assert_called_once_with("/dev/ttys001", "Terminal", "/compact")

    def test_compact_dormant_session_launches_interactive_resume(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False, "tty": None, "terminal_app": None},
             ), \
             mock.patch.object(self.server, "_pending_ask_user_question_for_session", return_value=False), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=None), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact", return_value="/tmp/backup.jsonl"), \
             mock.patch.object(
                 self.server,
                 "launch_terminal_for_session",
                 return_value={"ok": True, "terminal_app": "Terminal", "command": "claude --resume ..."},
             ) as launch:
            result = self.server.compact_session_context(sid)

        self.assertTrue(result["ok"])
        self.assertTrue(result["compact"])
        self.assertTrue(result["launched"])
        self.assertEqual(result["via"], "terminal-launch")
        launch.assert_called_once_with(
            sid,
            str(self.repo),
            None,
            post_slash_commands=["/compact"],
            stop_headless=True,
        )

    def test_compact_rejects_unsupported_engine(self):
        result = None
        with mock.patch.object(self.server, "_detect_session_engine", return_value="cursor"), \
             mock.patch.object(self.server, "launch_terminal_for_session") as launch, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server.compact_session_context("cursor-session")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "compact_unsupported_engine")
        launch.assert_not_called()
        resume.assert_not_called()

    def test_compact_routes_codex_to_app_server(self):
        with mock.patch.object(self.server, "_detect_session_engine", return_value="codex"), \
             mock.patch.object(
                 self.server, "_backup_codex_rollout_before_compact",
                 return_value="/tmp/backup.jsonl",
             ) as backup, \
             mock.patch.object(
                 self.server, "_codex_compact_via_app_server",
                 return_value={"ok": True, "via": "codex-compact", "session_id": "codex-session"},
             ) as compact, \
             mock.patch.object(self.server, "launch_terminal_for_session") as launch, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server.compact_session_context("codex-session")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-compact")
        self.assertEqual(result["engine"], "codex")
        self.assertEqual(result["backup_path"], "/tmp/backup.jsonl")
        self.assertTrue(result["compact"])
        backup.assert_called_once_with("codex-session")
        compact.assert_called_once_with("codex-session")
        launch.assert_not_called()
        resume.assert_not_called()

    def test_extract_session_slash_commands_returns_codex_catalog(self):
        with mock.patch.object(self.server, "_detect_session_engine", return_value="codex"):
            result = self.server.extract_session_slash_commands("codex-session")

        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "codex")
        self.assertEqual(result["source"], "codex-fallback")
        names = {cmd["name"] for cmd in result["commands"]}
        self.assertIn("/compact", names)
        self.assertIn("/model", names)
        self.assertIn("/status", names)

    def test_compact_live_headless_spawn_queues_when_busy(self):
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {
            "pid": 12345,
            "log": "spawn.log",
        }
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "pid": 12345,
                 },
             ), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=spawn), \
             mock.patch.object(self.server, "_spawn_entry_active_tool_child", return_value=True), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact") as backup, \
             mock.patch.object(self.server, "_queue_terminal_input", return_value={"ok": True, "queued": True}) as queue, \
             mock.patch.object(self.server, "launch_terminal_for_session") as launch, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject, \
             mock.patch.object(self.server, "_write_stream_json_user_message") as write:
            result = self.server.compact_session_context(sid)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "terminal-queued-headless")
        backup.assert_not_called()
        queue.assert_called_once_with(sid, "/compact", {"pid": 12345, "status": "headless"})
        launch.assert_not_called()
        inject.assert_not_called()
        write.assert_not_called()

    def test_compact_live_tty_plus_headless_spawn_runs_in_terminal(self):
        # Concurrent terminal + headless no longer blocks /compact. With the
        # staleness machinery (GH #71) retiring a stale headless the moment CCC
        # would route to it, /compact runs in the terminal (keystroke) and the
        # headless can't be reused with a pre-compact view. (Previously this
        # rejected with compact_headless_running.)
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {"pid": 12345}
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "/dev/ttys001",
                     "terminal_app": "Terminal",
                     "pid": 54321,
                 },
             ), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=spawn), \
             mock.patch.object(self.server, "_pending_ask_user_question_for_session", return_value=None), \
             mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=False), \
             mock.patch.object(self.server, "_session_status_is_busy", return_value=False), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact", return_value="/tmp/bk.jsonl") as backup, \
             mock.patch.object(self.server, "_queue_terminal_input") as queue, \
             mock.patch.object(self.server, "inject_input_via_keystroke", return_value={"ok": True, "submitted": True}) as inject:
            result = self.server.compact_session_context(sid)

        # Runs /compact in the terminal, not rejected as headless-running.
        self.assertNotEqual(result.get("code"), "compact_headless_running")
        inject.assert_called_once()
        backup.assert_called_once()
        queue.assert_not_called()

    def test_compact_live_no_tty_registry_queues_when_busy(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(self.server, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "pid": 12345,
                     "status": "busy",
                 },
             ), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session", return_value=None), \
             mock.patch.object(self.server, "_backup_jsonl_before_compact") as backup, \
             mock.patch.object(self.server, "_queue_terminal_input", return_value={"ok": True, "queued": True}) as queue, \
             mock.patch.object(self.server, "launch_terminal_for_session") as launch, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server.compact_session_context(sid)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "terminal-queued-headless")
        backup.assert_not_called()
        queue.assert_called_once_with(sid, "/compact", {"pid": 12345, "status": "busy"})
        launch.assert_not_called()
        inject.assert_not_called()

    def test_live_background_agent_injects_via_daemon_pty(self):
        sid = "00000000-0000-4000-8000-000000000001"
        worker = {"pid": 12345, "sessionId": sid, "ptySock": "/tmp/cc-daemon-501/x.sock"}
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "kind": "bg",
                     "status": "busy",
                     "job_id": "00000000",
                     "pid": 54324,
                 },
             ), \
             mock.patch.object(self.server, "_bg_agent_ready_for_input", return_value=True), \
             mock.patch.object(
                 self.server,
                 "_find_live_bg_agent_entry_for_session",
                 return_value=worker,
             ) as find_worker, \
             mock.patch.object(
                 self.server,
                 "_inject_bg_agent_via_pty_socket",
                 return_value={"ok": True, "via": "bg-agent-pty"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server._inject_text_into_session(sid, "follow up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "bg-agent-pty")
        find_worker.assert_called_once_with(sid)
        inject.assert_called_once_with(worker, "follow up", session_id=sid)
        resume.assert_not_called()

    def test_live_background_agent_queues_until_prompt_ready(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": None,
                         "terminal_app": None,
                         "kind": "bg",
                         "status": "busy",
                         "job_id": "00000000",
                         "pid": 54324,
                     },
                 ), \
                 mock.patch.object(self.server, "_bg_agent_ready_for_input", return_value=False), \
                 mock.patch.object(self.server, "_inject_bg_agent_via_pty_socket") as inject:
                result = self.server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["via"], "terminal-queued")
            inject.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_annotation_ux_queue_injects_existing_session(self):
        sid = "00000000-0000-4000-8000-000000000010"
        old_root = self.server.CCC_ROOT
        self.server.CCC_ROOT = self.repo
        try:
            with mock.patch.object(
                self.server,
                "_find_annotation_ux_queue_session",
                return_value={"session_id": sid, "display_name": "UX-fixes-queue"},
            ), mock.patch.object(
                self.server,
                "_inject_text_into_session",
                return_value={"ok": True, "via": "spawn-fifo"},
            ) as inject, mock.patch.object(self.server, "spawn_session") as spawn:
                result = self.server.enqueue_annotation_ux_fixes_queue(
                    "Annotation: bad pill",
                    inject=True,
                )
        finally:
            self.server.CCC_ROOT = old_root

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "injected")
        self.assertEqual(result["session_id"], sid)
        inject.assert_called_once_with(sid, "Annotation: bad pill")
        spawn.assert_not_called()

    def test_annotation_ux_queue_spawns_named_session_when_missing(self):
        sid = "00000000-0000-4000-8000-000000000011"
        log_path = pathlib.Path(self.tmp_home, "spawn.log")
        log_path.write_text(json.dumps({"session_id": sid}) + "\n", encoding="utf-8")
        old_root = self.server.CCC_ROOT
        self.server.CCC_ROOT = self.repo
        try:
            with mock.patch.object(
                self.server,
                "_find_annotation_ux_queue_session",
                return_value=None,
            ), mock.patch.object(
                self.server,
                "spawn_session",
                return_value={"ok": True, "pid": 123, "log": str(log_path)},
            ) as spawn:
                result = self.server.enqueue_annotation_ux_fixes_queue(
                    "Annotation: bad pill",
                    inject=True,
                )
        finally:
            self.server.CCC_ROOT = old_root

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "spawned")
        self.assertEqual(result["session_id"], sid)
        self.assertEqual(
            self.server._load_session_name_overrides().get(sid),
            "UX-fixes-queue",
        )
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0], "Annotation: bad pill")
        self.assertEqual(spawn.call_args.kwargs["name"], "UX-fixes-queue")
        self.assertEqual(spawn.call_args.kwargs["repo_path"], str(self.repo))

    def test_codex_live_terminal_injects_via_tty(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_codex") as resume:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        inject.assert_called_once_with("ttys009", "Terminal", "hello")
        resume.assert_not_called()

    def test_codex_slash_idle_terminal_submits_with_return(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                     "status": "idle",
                 },
             ), \
             mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=False), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control", "submit_key": "return"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_codex") as resume:
            result = self.server._inject_text_into_session(sid, "/status")

        self.assertTrue(result["ok"])
        inject.assert_called_once_with("ttys009", "Terminal", "/status", submit_key="return")
        resume.assert_not_called()

    def test_codex_slash_busy_terminal_queues_with_tab(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                     "status": "busy",
                 },
             ), \
             mock.patch.object(self.server, "_terminal_input_queue_has_pending", return_value=False), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control", "submit_key": "tab"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_codex") as resume:
            result = self.server._inject_text_into_session(sid, "/compact")

        self.assertTrue(result["ok"])
        inject.assert_called_once_with("ttys009", "Terminal", "/compact", submit_key="tab")
        resume.assert_not_called()

    def test_codex_slash_without_live_tui_rejects_resume(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False, "tty": None, "terminal_app": None},
             ), \
             mock.patch.object(self.server, "resume_session_codex") as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "/status")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "codex_slash_requires_live_tui")
        resume.assert_not_called()
        inject.assert_not_called()

    def test_codex_busy_terminal_routes_to_resume_for_app_queue(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                     "status": "busy",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "queued": True, "via": "codex-app-queued"},
             ) as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "codex-app-queued")
        resume.assert_called_once_with(sid, "hello")
        inject.assert_not_called()

    def test_codex_steer_mode_routes_to_resume_steer(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                     "status": "busy",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "via": "codex-steer"},
             ) as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "hello", mode="steer")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-steer")
        resume.assert_called_once_with(sid, "hello", steer=True)
        inject.assert_not_called()

    def test_codex_without_live_tty_uses_resume(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False, "tty": None, "terminal_app": None},
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "via": "codex-resume"},
             ) as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-resume")
        resume.assert_called_once_with(sid, "hello")
        inject.assert_not_called()

    def test_antigravity_resume_falls_back_to_app_when_cli_missing(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(
            self.server,
            "_antigravity_cli_conversation_path",
            return_value=None,
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_app_conversation_path",
                 return_value=pathlib.Path("/tmp/xxx"),
             ), \
             mock.patch.object(
                 self.server,
                 "_resume_session_antigravity_app",
                 return_value={"ok": True, "via": "antigravity-app"},
             ) as app_resume:
            result = self.server.resume_session_antigravity(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "antigravity-app")
        app_resume.assert_called_once_with(sid, "hello")

    def test_antigravity_app_resume_records_interaction_on_success(self):
        sid = "00000000-0000-4000-8000-000000000001"
        user_config = {
            "plannerConfig": {
                "requestedModel": {"model": "MODEL_PLACEHOLDER_TEST"},
            },
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": True, "config": user_config},
             ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_app_rpc",
                 return_value={"ok": True, "port": 1234},
             ) as rpc, \
             mock.patch.object(self.server, "_record_interaction") as record:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertTrue(result["resumed"])
        self.assertEqual(result["via"], "antigravity-app")
        self.assertEqual(result["port"], 1234)
        record.assert_called_once_with(sid)
        rpc.assert_called_once_with(
            "SendUserCascadeMessage",
            {
                "cascadeId": sid,
                "items": [{"text": "hello"}],
                "cascadeConfig": user_config,
            },
            timeout=10,
        )

    def test_antigravity_app_resume_requires_model_config(self):
        """When trajectory loads but has no model picked, surface the
        'pick a model in Antigravity' error (not the RPC-failure error)."""
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": False, "rpc": None},
             ), \
             mock.patch.object(self.server, "_antigravity_app_rpc") as rpc:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "antigravity_app_model_config_missing")
        rpc.assert_not_called()

    def test_antigravity_app_resume_passes_through_rpc_failure(self):
        """When the trajectory RPC itself failed (app not running, etc.),
        surface the actual RPC error instead of the misleading
        'no reusable model config' message."""
        sid = "00000000-0000-4000-8000-000000000001"
        rpc_failure = {
            "ok": False,
            "error": "Antigravity app language server is not running. Open Antigravity, then retry.",
            "code": "antigravity_app_unavailable",
            "via": "antigravity-app",
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": False, "rpc": rpc_failure},
             ), \
             mock.patch.object(self.server, "_antigravity_app_rpc") as rpc:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "antigravity_app_unavailable")
        rpc.assert_not_called()

    def test_antigravity_latest_user_config_reuses_last_valid_model(self):
        config = {
            "plannerConfig": {
                "requestedModel": {"model": "MODEL_PLACEHOLDER_TEST"},
            },
        }
        trajectory = {
            "steps": [
                {"userInput": {"userConfig": {"plannerConfig": {}}}},
                {"userInput": {"lastUserConfig": config}},
            ],
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_rpc",
            return_value={"ok": True, "response": {"trajectory": trajectory}},
        ) as rpc:
            result = self.server._antigravity_latest_user_config("sid")

        self.assertTrue(result["ok"])
        self.assertEqual(result["config"], config)
        self.assertIsNot(result["config"], config)
        rpc.assert_called_once_with(
            "GetCascadeTrajectory",
            {"cascadeId": "sid"},
            timeout=5,
        )

    def test_finished_spawn_poll_closes_log_handle(self):
        proc = mock.Mock()
        proc.poll.return_value = 0
        log_fh = mock.Mock()
        entry = {
            "pid": 12345,
            "proc": proc,
            "log_fh": log_fh,
            "fifo": None,
            "stdin_fd": None,
        }

        with mock.patch.object(self.server, "_remove_spawn_from_registry") as remove:
            self.assertEqual(self.server._poll_spawn_entry(entry), 0)

        log_fh.close.assert_called_once()
        remove.assert_called_once_with(12345)
        self.assertIsNone(entry["log_fh"])
        self.assertTrue(entry["_cleanup_done"])

    def test_live_headless_spawn_queues_when_tool_child_running(self):
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {"pid": 12345}
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": None,
                         "terminal_app": None,
                         "pid": 12345,
                     },
                 ), \
                 mock.patch.object(
                     self.server,
                     "_find_live_spawn_entry_for_session",
                     return_value=spawn,
                 ), \
                 mock.patch.object(
                     self.server,
                     "_spawn_entry_active_tool_child",
                     return_value={"pid": 23456, "command": "grep -r"},
                 ), \
                 mock.patch.object(
                     self.server,
                     "_terminal_input_queue_has_pending",
                     return_value=True,
                 ), \
                 mock.patch.object(self.server, "_write_stream_json_user_message") as write:
                result = self.server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["status"], "busy")
            self.assertEqual(result["via"], "terminal-queued")
            write.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_live_headless_spawn_restarts_when_fifo_write_fails(self):
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {"pid": 12345}
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "pid": 12345,
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "_find_live_spawn_entry_for_session",
                 return_value=spawn,
             ), \
             mock.patch.object(self.server, "_spawn_entry_active_tool_child", return_value=None), \
             mock.patch.object(self.server, "_write_stream_json_user_message", return_value=False), \
             mock.patch.object(self.server, "_retire_unresponsive_spawn_entry") as retire, \
             mock.patch.object(
                 self.server,
                 "resume_session_headless",
                 return_value={"ok": True, "pid": 67890, "resumed": True},
             ) as resume:
            result = self.server._inject_text_into_session(sid, "follow up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], 67890)
        retire.assert_called_once_with(spawn, terminate=True)
        resume.assert_called_once_with(sid, "follow up")

    def test_fifo_writer_open_does_not_block_without_reader(self):
        with tempfile.TemporaryDirectory() as td:
            fifo = pathlib.Path(td) / "stdin.fifo"
            os.mkfifo(fifo, 0o600)
            start = time.monotonic()
            fd = self.server._open_fifo_writer(str(fifo))
            elapsed = time.monotonic() - start

        self.assertIsNone(fd)
        self.assertLess(elapsed, 0.5)

    def test_stream_json_fifo_write_does_not_block_when_pipe_full(self):
        read_fd, write_fd = os.pipe()
        try:
            flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
            fcntl.fcntl(write_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            chunk = b"x" * 8192
            while True:
                try:
                    os.write(write_fd, chunk)
                except BlockingIOError:
                    break
            entry = {"stdin_fd": write_fd, "fifo": None, "proc": None}
            start = time.monotonic()
            ok = self.server._write_stream_json_user_message(entry, "hello")
            elapsed = time.monotonic() - start
            write_fd = None

            self.assertFalse(ok)
            self.assertIsNone(entry["stdin_fd"])
            self.assertLess(elapsed, 0.5)
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def test_stream_json_writer_strips_lone_surrogates(self):
        read_fd, write_fd = os.pipe()
        try:
            entry = {"stdin_fd": write_fd, "fifo": None, "proc": None}
            ok = self.server._write_stream_json_user_message(
                entry,
                "queued annotation " + chr(0xD83D) + " after screenshot",
            )
            self.assertTrue(ok)
            os.close(write_fd)
            write_fd = None

            raw = os.read(read_fd, 65536).decode("utf-8")
            self.assertNotIn("\\u" + "d83d", raw.lower())
            payload = json.loads(raw)
            text = payload["message"]["content"][0]["text"]
            self.assertNotIn(chr(0xD83D), text)
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def test_terminal_inject_timeout_has_actionable_macos_error(self):
        timeout = subprocess.TimeoutExpired(cmd=["osascript", "-e", "secret"], timeout=5)
        with mock.patch.object(self.server.subprocess, "run", side_effect=timeout):
            result = self.server.inject_input_via_keystroke("/dev/ttys001", "Terminal", "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "macos_automation_timeout")
        self.assertIn("app_mode_loader", result["error"])
        self.assertIn("app_node", result["error"])
        self.assertNotIn("secret", result["error"])

    def test_terminal_inject_restores_focus_by_process_id(self):
        seen = {}

        def fake_run(args, **kwargs):
            seen["script"] = args[2]
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            result = self.server.inject_input_via_keystroke("/dev/ttys001", "Terminal", "hello")

        self.assertTrue(result["ok"])
        script = seen["script"]
        self.assertIn("unix id of first application process whose frontmost is true", script)
        self.assertIn("frontmost of first application process whose unix id is prevPid", script)
        self.assertNotIn("tell application prevApp", script)

    def test_terminal_inject_can_submit_tab(self):
        seen = {}

        def fake_run(args, **kwargs):
            seen["script"] = args[2]
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            result = self.server.inject_input_via_keystroke(
                "/dev/ttys001",
                "Terminal",
                "/compact",
                submit_key="tab",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["submit_key"], "tab")
        self.assertIn("key code 48", seen["script"])

    def test_live_claude_scan_skips_headless_processes_before_lsof(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:4] == ["ps", "-A", "-o", "pid=,comm="]:
                return subprocess.CompletedProcess(args, 0, stdout="100 claude\n101 claude\n102 node\n", stderr="")
            if args == ["ps", "-o", "pid,tty", "-p", "100,101"]:
                return subprocess.CompletedProcess(args, 0, stdout="  PID TTY\n  100 ??\n  101 ttys001\n", stderr="")
            raise AssertionError(f"unexpected command: {args}")

        cwd_calls = []

        def fake_cwd(pid):
            cwd_calls.append(pid)
            return "/tmp/demo"

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(self.server, "_proc_cwd", side_effect=fake_cwd), \
             mock.patch.object(self.server, "_proc_ancestor_terminal", return_value=("Terminal", 9)):
            procs = self.server.find_live_claude_processes()

        self.assertEqual([p["pid"] for p in procs], [101])
        self.assertEqual(cwd_calls, ["101"])

    def test_ask_user_question_tool_detail_surfaces_prompt(self):
        ev = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-question",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-question",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [{
                            "header": "Key flow",
                            "question": "How automated do you want this?",
                            "options": [
                                {"label": "Full auto", "description": "Run everything without checking back."},
                                {"label": "Half auto", "description": "Ask before the risky bits."},
                                {"label": "Skip Whisper"},
                            ],
                        }]
                    },
                }],
            },
        }

        parsed = self.server._parse_conversation_event(ev, 7)

        self.assertEqual(parsed["type"], "assistant")
        detail = parsed["blocks"][0]["detail"]
        self.assertEqual(parsed["blocks"][0]["id"], "toolu-question")
        self.assertIn("How automated do you want this?", detail)
        self.assertIn("Full auto", detail)
        self.assertIn("Half auto", detail)
        rich = parsed["blocks"][0]["question"]["questions"][0]["options"]
        self.assertEqual(rich[0]["description"], "Run everything without checking back.")

    def test_bash_tool_detail_strips_shell_wrapper(self):
        ev = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-bash",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-bash",
                    "name": "Bash",
                    "input": {
                        "command": (
                            "true && unsetopt NO_EXTENDED_GLOB 2>/dev/null || true && "
                            "setopt NO_EXTENDED_GLOB 2>/dev/null || true && "
                            "python3 render_short_slides.py 2>&1 | grep slide"
                        )
                    },
                }],
            },
        }

        parsed = self.server._parse_conversation_event(ev, 8)

        detail = parsed["blocks"][0]["detail"]
        self.assertEqual(parsed["blocks"][0]["id"], "toolu-bash")
        self.assertEqual(detail, "python3 render_short_slides.py 2>&1 | grep slide")
        self.assertNotIn("NO_EXTENDED_GLOB", detail)

    def test_pending_ask_user_question_clears_after_answer(self):
        sid = "00000000-0000-4000-8000-000000000099"
        project_dir = pathlib.Path(self.tmp_home, ".claude", "projects", "-demo-repo")
        project_dir.mkdir(parents=True)
        jsonl = project_dir / f"{sid}.jsonl"
        preamble_event = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": "Locked in. Back to the key flow question.",
                }],
            },
        }
        ask_event = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-question",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-question",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [{
                            "header": "Key flow",
                            "question": "How automated do you want this?",
                            "options": [{"label": "Half auto", "description": "Ask before destructive steps."}],
                        }]
                    },
                }],
            },
        }
        jsonl.write_text(
            json.dumps(preamble_event) + "\n" + json.dumps(ask_event) + "\n",
            encoding="utf-8",
        )

        pending = self.server._pending_ask_user_question_for_session(sid)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["question"], "How automated do you want this?")
        self.assertEqual(pending["preamble"], "Locked in. Back to the key flow question.")
        self.assertEqual(pending["options"], ["Half auto"])
        self.assertEqual(pending["option_details"][0]["description"], "Ask before destructive steps.")

        answer_event = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu-question",
                    "content": "answered",
                }],
            },
        }
        with jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(answer_event) + "\n")

        self.assertIsNone(self.server._pending_ask_user_question_for_session(sid))

    def test_ask_question_blocking_inject_gates_on_relay_request(self):
        """Inject queue gate uses the relay request file, not the transcript.

        The PreToolUse hook answers AskUserQuestion via a deny-reply, so the
        tool never runs and no tool_result lands in the transcript — a
        transcript scan can never see the question clear. Gating the queue on
        it deadlocks ("messages stuck on Queued forever"). The request file is
        the authoritative live signal.
        """
        sid = "00000000-0000-4000-8000-0000000000aa"
        relay_dir = self.server.QUESTION_RELAY_DIR
        relay_dir.mkdir(parents=True, exist_ok=True)
        req_path = relay_dir / f"{sid}.request.json"
        ans_path = relay_dir / f"{sid}.answer.json"
        headless = {}  # no TTY → a relay/headless session

        # No relay request -> nothing blocking, even with a stale transcript.
        self.assertFalse(self.server._ask_question_blocking_inject(sid, headless))

        # Live request, no answer yet -> hold the queue.
        req_path.write_text(json.dumps({
            "nonce": "n-1", "session_id": sid, "pid": os.getpid(),
            "questions": [{"header": "H", "question": "Q?", "options": []}],
        }), encoding="utf-8")
        self.assertTrue(self.server._ask_question_blocking_inject(sid, headless))

        # User relayed a matching-nonce answer -> resolved, drain the queue
        # (the hook hasn't cleared the request file yet).
        ans_path.write_text(json.dumps({"nonce": "n-1", "answers": []}),
                            encoding="utf-8")
        self.assertFalse(self.server._ask_question_blocking_inject(sid, headless))

        # A stale answer from a previous question must NOT unblock a fresh one.
        ans_path.write_text(json.dumps({"nonce": "n-OLD", "answers": []}),
                            encoding="utf-8")
        self.assertTrue(self.server._ask_question_blocking_inject(sid, headless))

    def _write_ask_question_session(self, sid, *, answered):
        """Write a transcript whose last assistant turn asks a question.

        When ``answered`` is True a matching tool_result is appended,
        simulating a question the user already answered or declined.
        """
        self.server.SIDECAR_STATE_DIR.mkdir(parents=True, exist_ok=True)
        project_dir = pathlib.Path(self.tmp_home, ".claude", "projects", "-demo-repo")
        project_dir.mkdir(parents=True, exist_ok=True)
        jsonl = project_dir / f"{sid}.jsonl"
        events = [{
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": "Locked in. Back to the key flow question.",
                }],
            },
        }, {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-question",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [{
                            "header": "Key flow",
                            "question": "How automated do you want this?",
                            "options": [{
                                "label": "Half auto",
                                "description": "Ask before destructive steps.",
                            }],
                        }]
                    },
                }],
            },
        }]
        if answered:
            # User hit Esc on the prompt — Claude Code returns an error
            # tool_result and never fires PostToolUse, so the in-flight
            # marker lingers. This must NOT keep the row "waiting".
            events.append({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": "toolu-question",
                        "is_error": True,
                        "content": "Answer questions?",
                    }],
                },
            })
        jsonl.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )
        marker = {
            "session_id": sid,
            "tool": "AskUserQuestion",
            "file": "Key flow: How automated do you want this?",
            "question": "How automated do you want this?",
            "header": "Key flow",
            "options": ["Half auto"],
            "option_details": [{
                "label": "Half auto",
                "description": "Ask before destructive steps.",
            }],
            "summary": "Key flow: How automated do you want this?",
            "started_at": 1778813567.0,
        }
        (self.server.SIDECAR_STATE_DIR / f"{sid}_in_flight.json").write_text(
            json.dumps(marker),
            encoding="utf-8",
        )

    def test_inflight_ask_user_question_marks_row_waiting(self):
        sid = "00000000-0000-4000-8000-000000000100"
        self._write_ask_question_session(sid, answered=False)

        entry = {"session_id": sid, "is_live": True}
        self.server._add_sidecar_fields(entry)

        self.assertEqual(entry["sidecar_tool"], "AskUserQuestion")
        self.assertTrue(entry["question_waiting"])
        self.assertEqual(entry["question_text"], "How automated do you want this?")
        self.assertEqual(entry["question_preamble"], "Locked in. Back to the key flow question.")
        self.assertEqual(entry["question_option_details"][0]["description"], "Ask before destructive steps.")

    def test_declined_ask_user_question_does_not_mark_row_waiting(self):
        # Regression: a declined AskUserQuestion (is_error tool_result, no
        # PostToolUse) leaves a stale in-flight marker. The transcript is
        # authoritative — the row must not show a phantom "waiting" box.
        sid = "00000000-0000-4000-8000-000000000101"
        self._write_ask_question_session(sid, answered=True)

        entry = {"session_id": sid, "is_live": True}
        self.server._add_sidecar_fields(entry)

        self.assertFalse(entry["question_waiting"])
        self.assertFalse(entry["sidecar_in_flight"])
        self.assertNotEqual(entry.get("sidecar_tool"), "AskUserQuestion")

    def test_spawn_session_preflights_missing_claude_cli(self):
        with mock.patch.object(
            self.server,
            "_resolve_claude_bin",
            return_value={
                "available": False,
                "bin": None,
                "code": "claude_unavailable",
                "reason": "Claude Code CLI not found",
            },
        ), mock.patch.object(
            self.server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)
        ), mock.patch.object(self.server.subprocess, "Popen") as popen:
            result = self.server.spawn_session(
                "do the thing",
                name="do the thing",
                cwd=str(self.repo),
                repo_path=str(self.repo),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        popen.assert_not_called()

    def test_spawn_session_accepts_unregistered_plain_cwd(self):
        scratch = pathlib.Path(self.tmp_home, "scratch-space").resolve()
        scratch.mkdir()
        with mock.patch.object(
            self.server,
            "_resolve_claude_bin",
            return_value={
                "available": False,
                "bin": None,
                "code": "claude_unavailable",
                "reason": "Claude Code CLI not found",
            },
        ), mock.patch.object(
            self.server, "_git_toplevel_for_existing_dir", return_value=None
        ), mock.patch.object(self.server.subprocess, "Popen") as popen:
            result = self.server.spawn_session(
                "do the thing",
                name="do the thing",
                cwd=str(scratch),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        self.assertIn(str(scratch), self.server._load_custom_repos())
        popen.assert_not_called()

    def test_unknown_repo_path_is_rejected(self):
        unknown = pathlib.Path(self.tmp_home, "not-a-repo").resolve()
        unknown.mkdir()
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.resolve_repo_path(str(unknown))
        self.assertEqual(ctx.exception.code, "repo_not_allowed")

    def test_all_is_not_a_repo_path(self):
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.resolve_repo_path("ALL")
        self.assertEqual(ctx.exception.code, "invalid_repo_path")

    def test_ambiguous_context_returns_repo_required(self):
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.require_repo_context({}, {}, allow_session=False)
        self.assertEqual(ctx.exception.code, "repo_required")

    def test_session_id_resolves_repo_context(self):
        sid = "00000000-0000-4000-8000-000000000099"
        transcript = self.server._canonical_conversation_path(str(self.repo), sid)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(self.repo),
                "sessionId": sid,
                "message": {"role": "user", "content": "hello"},
            }) + "\n",
            encoding="utf-8",
        )

        ctx = self.server.repo_from_session(sid)
        self.assertEqual(ctx["repo_path"], str(self.repo))
        self.assertEqual(ctx["cwd"], str(self.repo))

    def test_session_cwd_relocates_after_folder_move(self):
        sid = "00000000-0000-4000-8000-000000000100"
        old_cwd = self.repo / "old folder" / "app"
        new_cwd = self.repo / "code" / "old-folder" / "app"
        moved_file = new_cwd / "src" / "main.py"
        moved_file.parent.mkdir(parents=True)
        moved_file.write_text("print('ok')\n", encoding="utf-8")

        transcript = self.server._canonical_conversation_path(str(self.repo), sid)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(old_cwd),
                "sessionId": sid,
                "gitBranch": "main",
                "message": {"role": "user", "content": "read src/main.py"},
            }) + "\n" +
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-05-04T00:00:01.000Z",
                "sessionId": sid,
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": str(old_cwd / "src" / "main.py")},
                    }],
                },
            }) + "\n",
            encoding="utf-8",
        )

        self.assertEqual(self.server.find_session_cwd(sid), str(new_cwd))
        ctx = self.server.repo_from_session(sid)
        self.assertEqual(ctx["repo_path"], str(self.repo))
        self.assertEqual(ctx["cwd"], str(new_cwd))
        row = next(r for r in self.server.find_conversations(str(self.repo))
                   if r["session_id"] == sid)
        self.assertEqual(row["session_cwd"], str(new_cwd))
        self.assertTrue(row["session_cwd_exists"])

    def test_cwd_context_uses_nearest_claude_marker_parent(self):
        project = pathlib.Path(self.tmp_home, "plain-project").resolve()
        cwd = project / "nested" / "tool"
        cwd.mkdir(parents=True)
        (project / ".claude").mkdir()

        ctx = self.server._resolve_cwd_context(str(cwd))
        self.assertEqual(ctx["repo_path"], str(project))
        self.assertEqual(ctx["cwd"], str(cwd))

    def test_repo_required_endpoint_and_switch_compatibility(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as missing:
                urllib.request.urlopen(base + "/api/term/cwd", timeout=5)
            self.assertEqual(missing.exception.code, 400)
            missing_body = missing.exception.read().decode("utf-8")
            missing.exception.close()
            self.assertIn("repo_required", missing_body)

            with self.assertRaises(urllib.error.HTTPError) as conv_missing:
                urllib.request.urlopen(base + "/api/conversations", timeout=5)
            self.assertEqual(conv_missing.exception.code, 400)
            conv_missing_body = conv_missing.exception.read().decode("utf-8")
            conv_missing.exception.close()
            self.assertIn("repo_required", conv_missing_body)

            with urllib.request.urlopen(
                base + "/api/term/cwd?repo_path=" + urllib.parse.quote(str(self.repo)),
                timeout=5,
            ) as res:
                self.assertEqual(res.status, 200)
                self.assertIn(str(self.repo), res.read().decode("utf-8"))

            req = urllib.request.Request(
                base + "/api/repo/switch",
                data=json.dumps({"path": str(self.repo)}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as gone:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(gone.exception.code, 410)
            gone_body = gone.exception.read().decode("utf-8")
            gone.exception.close()
            self.assertIn("repo_switch_removed", gone_body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_sessions_all_endpoint_returns_archive_and_spawned_payload(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "_build_archive_conversations",
                return_value=[{"session_id": "codex-1", "engine": "codex"}],
            ), mock.patch.object(
                self.server,
                "list_spawned_sessions",
                return_value=[{"spawn_id": "123", "engine": "codex"}],
            ):
                with urllib.request.urlopen(base + "/api/sessions?all=1&engine=codex", timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))
            self.assertTrue(body["ok"])
            self.assertEqual(body["count"], 1)
            self.assertEqual(body["sessions"][0]["engine"], "codex")
            self.assertEqual(body["spawned"][0]["spawn_id"], "123")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_unified_spawn_endpoint_accepts_engine(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "spawn_session_codex",
                return_value={"ok": True, "pid": 123, "name": "demo", "log": "/tmp/demo.log"},
            ) as spawn_codex:
                req = urllib.request.Request(
                    base + "/api/sessions/spawn",
                    data=json.dumps({
                        "prompt": "do the thing",
                        "engine": "Codex",
                        "model": "gpt-test",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))
            self.assertEqual(body["engine"], "codex")
            spawn_codex.assert_called_once_with(
                "do the thing",
                name=None,
                cwd=None,
                repo_path=None,
                worktree=False,
                model="gpt-test",
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_write_port_file_never_publishes_wildcard_bind_address(self):
        url = self.server.write_port_file("0.0.0.0")
        self.assertEqual(url, f"http://127.0.0.1:{self.server.PORT}")
        port_file = self.server.COMMAND_CENTER_STATE_DIR / "port.txt"
        self.assertEqual(port_file.read_text().strip(), url)

    def test_resolve_codex_bin_prefers_env_override(self):
        """`_resolve_codex_bin` must honour CCC_CODEX_BIN when it points
        at an executable file. Verifies the precedence head — env var
        always wins over `which codex` and the app-bundle fallback."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_codex_bin"))

        with tempfile.NamedTemporaryFile(prefix="codex-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = f.name
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {"CCC_CODEX_BIN": fake_bin}), \
                 mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"), \
                 mock.patch.object(server, "CODEX_APP_BUNDLE_PATH", "/sentinel/from/bundle"):
                result = server._resolve_codex_bin()
            # Env override must win over both the PATH lookup and the bundle path.
            self.assertEqual(result["bin"], fake_bin)
            self.assertEqual(result["source"], "env")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_resolve_codex_bin_returns_unavailable_when_missing(self):
        """When CCC_CODEX_BIN points at a non-existent path AND the
        Codex.app bundle is absent AND `which codex` finds nothing,
        the resolver must return {available: False, reason: ...}
        rather than raising."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.dict(os.environ, {"CCC_CODEX_BIN": "/definitely/does/not/exist/codex"}), \
             mock.patch.object(server.shutil, "which", return_value=None), \
             mock.patch.object(server, "CODEX_APP_BUNDLE_PATH", "/nope/does-not-exist"):
            result = server._resolve_codex_bin()
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_resolve_claude_bin_prefers_env_override(self):
        """CCC_CLAUDE_BIN must win over PATH so launchd services can pin
        the same CLI path an interactive shell uses."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_claude_bin"))

        with tempfile.NamedTemporaryFile(prefix="claude-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = f.name
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {"CCC_CLAUDE_BIN": fake_bin}), \
                 mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"):
                result = server._resolve_claude_bin()
            self.assertEqual(result["bin"], fake_bin)
            self.assertEqual(result["source"], "env")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_resolve_claude_bin_returns_unavailable_for_bad_env_override(self):
        """A bad CCC_CLAUDE_BIN should fail clearly instead of falling
        through to another binary and hiding the service configuration error."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.dict(os.environ, {"CCC_CLAUDE_BIN": "/definitely/does/not/exist/claude"}), \
             mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"):
            result = server._resolve_claude_bin()
        self.assertFalse(result["available"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        self.assertIn("CCC_CLAUDE_BIN", result.get("reason", ""))

    def test_resolve_gemini_bin_uses_common_candidates(self):
        """Gemini should be available when installed in a user bin dir that
        launchd did not put on PATH."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_gemini_bin"))

        with tempfile.NamedTemporaryFile(prefix="gemini-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = pathlib.Path(f.name)
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(server.shutil, "which", return_value=None), \
                 mock.patch.object(server, "_iter_common_cli_candidates", return_value=[fake_bin]):
                result = server._resolve_gemini_bin()
            self.assertEqual(result["bin"], str(fake_bin))
            self.assertEqual(result["source"], "candidate")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_nextjs_turbo_workspace_uses_dev_filter(self):
        """Workspace Next.js apps should start with the scoped turbo command."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            app = root / "apps" / "bookyourmat"
            finie = root / "apps" / "finie"
            app.mkdir(parents=True)
            finie.mkdir(parents=True)
            (root / "turbo.json").write_text(json.dumps({"tasks": {"dev": {"persistent": True}}}))
            (root / "package.json").write_text(json.dumps({"workspaces": ["apps/*"]}))
            (app / "package.json").write_text(json.dumps({
                "name": "bookyourmat",
                "scripts": {"dev": "next dev --port 39001"},
                "dependencies": {"next": "16.1.6"},
            }))
            (finie / "package.json").write_text(json.dumps({
                "name": "finie",
                "scripts": {"dev": "next dev --port 3000"},
                "dependencies": {"next": "16.1.6"},
            }))

            cmd, cwd = server._resolve_dev_invocation(app)
            (root / ".git").mkdir()
            status = server.nextjs_status(str(root), str(app))
            root_status = server.nextjs_status(str(root))

        self.assertEqual(cmd, ["npx", "turbo", "dev", "--filter=bookyourmat"])
        self.assertEqual(cwd, root)
        self.assertEqual(status["launch_cmd"], "npx turbo dev --filter=bookyourmat")
        self.assertEqual(root_status["target_path"], str(app))
        self.assertEqual(root_status["launch_cmd"], "npx turbo dev --filter=bookyourmat")

    def test_nextjs_process_match_ignores_prompt_text(self):
        """Process rediscovery must not match an agent command line that
        merely pasted the same ps/rg pattern in its prompt."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        info = {
            "target_path": pathlib.Path("/tmp/repo/apps/bookyourmat"),
            "run_cwd": pathlib.Path("/tmp/repo"),
            "package_name": "bookyourmat",
            "filter_expected": True,
            "ports": [3001],
        }
        self.assertTrue(server._nextjs_command_matches(
            "npx turbo dev --filter=bookyourmat", info))
        self.assertTrue(server._nextjs_command_matches(
            "node /tmp/repo/node_modules/.bin/next dev --port 3001", info))
        self.assertFalse(server._nextjs_command_matches(
            "/opt/homebrew/bin/codex exec --json -- prompt contains "
            "turbo dev --filter=bookyourmat and next dev --port 3001",
            info,
        ))

    def test_spawn_session_codex_exists(self):
        """`spawn_session_codex` must exist alongside `spawn_session`
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_codex"))
        import inspect
        sig = inspect.signature(server.spawn_session_codex)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree", "model"])

    def test_spawn_session_gemini_exists(self):
        """`spawn_session_gemini` must exist alongside the other engines
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_gemini"))
        import inspect
        sig = inspect.signature(server.spawn_session_gemini)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree", "model"])

    def test_spawn_session_cursor_exists(self):
        """`spawn_session_cursor` must exist alongside the other engines
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_cursor"))
        import inspect
        sig = inspect.signature(server.spawn_session_cursor)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree", "model"])

    def test_orchestration_spawn_engine_normalization(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertEqual(server._normalize_orchestration_spawn_engine(None), "claude")
        self.assertEqual(server._normalize_orchestration_spawn_engine("Claude"), "claude")
        self.assertEqual(server._normalize_orchestration_spawn_engine("Codex"), "codex")
        self.assertEqual(server._normalize_orchestration_spawn_engine("cursor-agent"), "cursor")
        self.assertEqual(server._normalize_orchestration_spawn_engine("antigravity"), "antigravity")
        self.assertEqual(server._normalize_orchestration_spawn_engine("gemini"), "antigravity")

    def test_record_spawn_to_registry_persists_engine(self):
        """The on-disk spawn registry must round-trip an `engine` field
        so a CCC restart can branch claude-vs-codex reattach logic."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            registry_file = pathlib.Path(tmp) / "spawned-pids.json"
            orig = server.SPAWNED_PIDS_FILE
            server.SPAWNED_PIDS_FILE = registry_file
            try:
                server._record_spawn_to_registry(
                    pid=99999, name="t", log_path=pathlib.Path(tmp) / "x.log",
                    cwd=tmp, spawned_at="20260430T000000",
                    command_summary="test", fifo=None, engine="codex",
                    session_id="known-session-id",
                )
                with registry_file.open() as f:
                    rows = json.load(f)
                self.assertEqual(rows[-1]["engine"], "codex")
                self.assertEqual(rows[-1]["session_id"], "known-session-id")
            finally:
                server.SPAWNED_PIDS_FILE = orig

    def test_list_spawned_sessions_exposes_correlation_fields(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions[:] = [{
            "pid": 4242,
            "name": "reviewer",
            "log": "/tmp/reviewer.log",
            "prompt": "review this",
            "started": "20260530T120000",
            "proc": proc,
            "engine": "codex",
            "session_id": "codex-thread-1",
            "cwd": "/tmp/repo",
            "repo_path": "/tmp/repo",
            "model": "gpt-test",
        }]
        try:
            rows = server.list_spawned_sessions()
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertEqual(rows[0]["spawn_id"], "4242")
        self.assertEqual(rows[0]["session_id"], "codex-thread-1")
        self.assertFalse(rows[0]["session_id_pending"])
        self.assertEqual(rows[0]["engine"], "codex")
        self.assertEqual(rows[0]["repo_path"], "/tmp/repo")
        self.assertTrue(rows[0]["running"])

    def test_pid_is_engine_process_recognises_codex_and_gemini(self):
        """`_pid_is_engine_process` must accept an `engine` arg and match
        the right argv[0] basename for it."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_pid_is_engine_process"))

        def fake_run(args, **kw):
            class R: pass
            r = R(); r.returncode = 0; r.stdout = ""; r.stderr = ""
            if args[:2] == ["ps", "-p"]:
                pid = args[2]
                if pid == "11111":
                    r.stdout = "/usr/local/bin/claude -p --verbose\n"
                elif pid == "22222":
                    r.stdout = "/Applications/Codex.app/Contents/Resources/codex exec --json\n"
                elif pid == "33333":
                    r.stdout = "/usr/local/bin/node /usr/local/bin/gemini --output-format stream-json\n"
                elif pid == "44444":
                    r.stdout = "/Users/test/.local/bin/cursor-agent --resume 00000000-0000-4000-8000-000000000005\n"
            return r

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            self.assertTrue(server._pid_is_engine_process(11111, "claude"))
            self.assertFalse(server._pid_is_engine_process(11111, "codex"))
            self.assertTrue(server._pid_is_engine_process(22222, "codex"))
            self.assertFalse(server._pid_is_engine_process(22222, "claude"))
            self.assertTrue(server._pid_is_engine_process(33333, "gemini"))
            self.assertFalse(server._pid_is_engine_process(33333, "codex"))
            self.assertTrue(server._pid_is_engine_process(44444, "cursor"))
            self.assertFalse(server._pid_is_engine_process(44444, "codex"))

    def test_pid_is_engine_process_rejects_zombie(self):
        """A defunct reattached resume must not keep a Codex card live."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.object(server, "_pid_is_zombie", return_value=True), \
             mock.patch.object(server.subprocess, "run") as run:
            self.assertFalse(server._pid_is_engine_process(22222, "codex"))
        run.assert_not_called()

    def test_reattached_proc_poll_treats_zombie_as_exited(self):
        """After an in-place server restart, a child may become a zombie
        without a Popen handle. Polling must release queued resumes."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        proc = server._ReattachedProc(22222)
        with mock.patch.object(server.os, "waitpid", side_effect=ChildProcessError), \
             mock.patch.object(server.os, "kill", return_value=None), \
             mock.patch.object(server, "_pid_is_zombie", return_value=True):
            self.assertEqual(proc.poll(), -1)
            self.assertEqual(proc.poll(), -1)

    def test_gemini_chat_parsing_usage_and_row_signals(self):
        """Gemini chat JSON should render as a first-class session row."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        chat = {
            "sessionId": sid,
            "startTime": "2026-05-04T01:00:00.000Z",
            "lastUpdated": "2026-05-04T01:02:00.000Z",
            "kind": "main",
            "messages": [
                {
                    "id": "u1",
                    "timestamp": "2026-05-04T01:00:00.000Z",
                    "type": "user",
                    "content": [{"text": "Create the probe file."}],
                },
                {
                    "id": "g1",
                    "timestamp": "2026-05-04T01:01:00.000Z",
                    "type": "gemini",
                    "content": "I created and committed the probe.",
                    "model": "gemini-test-model",
                    "tokens": {
                        "input": 1200,
                        "output": 30,
                        "cached": 200,
                        "thoughts": 5,
                        "tool": 0,
                        "total": 1235,
                    },
                    "toolCalls": [{
                        "id": "run_shell_command_1",
                        "name": "run_shell_command",
                        "args": {
                            "command": "printf 'ok\\n' > probe.txt && git add probe.txt && git commit -m \"probe: gemini\"",
                            "description": "Create and commit probe file.",
                        },
                        "status": "success",
                        "timestamp": "2026-05-04T01:01:10.000Z",
                        "result": [{
                            "functionResponse": {
                                "response": {
                                    "output": "Output: [feat/demo abc1234] probe: gemini\n 1 file changed, 1 insertion(+)"
                                }
                            }
                        }],
                    }],
                },
                {
                    "id": "g2",
                    "timestamp": "2026-05-04T01:02:00.000Z",
                    "type": "gemini",
                    "content": "Branch: feat/demo\nCommit: abc1234 probe: gemini\nWorktree: /tmp/example-worktree",
                    "model": "gemini-test-model",
                    "tokens": {
                        "input": 1300,
                        "output": 20,
                        "cached": 250,
                        "thoughts": 4,
                        "tool": 0,
                        "total": 1324,
                    },
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            gemini_home = pathlib.Path(tmp) / ".gemini"
            project = gemini_home / "tmp" / "example-project"
            chats = project / "chats"
            chats.mkdir(parents=True)
            (project / ".project_root").write_text("/tmp/example-repo")
            chat_path = chats / "session-2026-05-04T01-00-aaaaaaaa.json"
            chat_path.write_text(json.dumps(chat))
            orig_home = server.GEMINI_HOME
            server.GEMINI_HOME = gemini_home
            try:
                self.assertTrue(server._is_gemini_session(sid))
                parsed = server.parse_conversation(sid)
                usage = server.extract_session_usage(sid)
                rows = server.find_gemini_conversations(include_old=True, repo_only=False)
            finally:
                server.GEMINI_HOME = orig_home

        self.assertGreaterEqual(len(parsed["events"]), 4)
        self.assertEqual(usage["latest_input_tokens"], 1300)
        self.assertEqual(usage["total_cache_read_tokens"], 450)
        self.assertEqual(usage["model"], "gemini-test-model")
        row = rows[0]
        self.assertEqual(row["source"], "gemini")
        self.assertTrue(row["has_edit"])
        self.assertTrue(row["has_commit"])
        self.assertEqual(row["effective_branch"], "feat/demo")
        self.assertEqual(row["session_cwd"], "/tmp/example-worktree")

    def test_shell_command_signals_detect_real_git_subcommands(self):
        cases = [
            ("git push", {"push": True}),
            ("git -C /tmp/repo push origin HEAD", {"push": True, "external_cd": True}),
            ("git -c user.name=Bot commit -m ok && git push", {"commit": True, "push": True}),
            ("command git commit -m ok", {"commit": True}),
            ("env GIT_DIR=/tmp/repo/.git git push", {"push": True}),
            ("bash -lc 'git push'", {"push": True}),
            ("gh --repo owner/repo pr create --title ok", {"pr": True}),
        ]
        for cmd, expected in cases:
            with self.subTest(cmd=cmd):
                signals = self.server._shell_command_signals(cmd)
                for key, value in expected.items():
                    self.assertEqual(signals[key], value)

    def test_shell_command_signals_ignore_git_text_in_other_commands(self):
        cases = [
            'rg -n "git push" server.py',
            'grep "git commit" rollout.jsonl',
            'echo "git push"',
            'python3 - <<\'PY\'\nprint("git push")\nPY',
            'git status | rg "push"',
        ]
        for cmd in cases:
            with self.subTest(cmd=cmd):
                signals = self.server._shell_command_signals(cmd)
                self.assertFalse(signals["commit"])
                self.assertFalse(signals["push"])

        signals = self.server._shell_command_signals(
            'git commit -m "document git push workflow"'
        )
        self.assertTrue(signals["commit"])
        self.assertFalse(signals["push"])

    def test_shell_command_signals_resolve_relative_worktree_add(self):
        base = pathlib.Path(self.tmp_home, "repo").resolve()
        base.mkdir()
        expected = base.parent / "repo-wt-ui"

        signals = self.server._shell_command_signals(
            "git worktree add -b fix/worktree-ui ../repo-wt-ui origin/main",
            base_cwd=str(base),
        )

        self.assertEqual(signals["worktree_branch"], "fix/worktree-ui")
        self.assertEqual(signals["worktree_path"], str(expected.resolve()))

    def test_codex_tail_meta_resolves_relative_worktree_add_from_workdir(self):
        base = pathlib.Path(self.tmp_home, "repo").resolve()
        base.mkdir()
        expected = base.parent / "repo-wt-ui"
        event = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_worktree",
                "arguments": json.dumps({
                    "cmd": "git worktree add -b fix/worktree-ui ../repo-wt-ui origin/main",
                    "workdir": str(base),
                }),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rollout.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_codex_tail_meta(path)

        self.assertEqual(meta["tail_branch"], "fix/worktree-ui")
        self.assertEqual(meta["tail_worktree_path"], str(expected.resolve()))

    def test_workspace_uses_explicit_worktree_tail_hint(self):
        worktree = pathlib.Path(self.tmp_home, "repo-wt-ui").resolve()
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /tmp/fake/worktrees/repo-wt-ui\n")

        with mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "_session_tail_worktree_hint",
                 return_value={
                     "path": str(worktree),
                     "branch": "fix/worktree-ui",
                     "source": "worktree-add",
                 },
             ), \
             mock.patch.object(self.server, "_infer_effective_repo") as infer:
            workspace = self.server.extract_session_workspace(
                "00000000-0000-4000-8000-000000000001"
            )

        infer.assert_not_called()
        self.assertEqual(workspace["effective_cwd"], str(worktree))
        self.assertEqual(workspace["effective_branch"], "fix/worktree-ui")
        self.assertEqual(workspace["effective_kind"], "worktree")
        self.assertEqual(workspace["effective_source"], "worktree-add")

    def test_tail_meta_ignores_bash_search_for_git_push(self):
        event = {
            "type": "assistant",
            "timestamp": "2026-05-11T12:00:00.000Z",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": 'rg -n "git push" server.py'},
                }],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "s.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_tail_meta(path)

        self.assertFalse(meta["has_commit"])
        self.assertFalse(meta["has_push"])

    def test_codex_tail_meta_ignores_exec_search_for_git_push(self):
        event = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_1",
                "arguments": json.dumps({"cmd": 'rg -n "git push" server.py'}),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rollout.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_codex_tail_meta(path)

        self.assertFalse(meta["has_commit"])
        self.assertFalse(meta["has_push"])

    def test_reattach_spawned_orphans_defaults_legacy_rows_to_claude(self):
        """A registry row written before the `engine` field existed
        must reattach as engine='claude' — not raise KeyError, not
        silently drop the row."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            registry_file = pathlib.Path(tmp) / "spawned-pids.json"
            log_file = pathlib.Path(tmp) / "fake.log"
            log_file.write_text("")
            # Legacy row — no `engine` key. PID is the current process so
            # the os.kill(pid, 0) liveness check succeeds without faking.
            legacy = [{
                "pid": os.getpid(),
                "session_id": None,
                "name": "legacy",
                "log": str(log_file),
                "fifo": None,
                "cwd": tmp,
                "spawned_at": "20260101T000000",
                "command_summary": "old row",
            }]
            registry_file.write_text(json.dumps(legacy))
            orig_registry = server.SPAWNED_PIDS_FILE
            orig_sessions = list(server._spawned_sessions)
            server.SPAWNED_PIDS_FILE = registry_file
            server._spawned_sessions.clear()
            try:
                # Bypass the real ps-grep — current pid isn't a `claude`
                # process, so without a stub it would be dropped.
                with mock.patch.object(server, "_pid_is_engine_process", return_value=True):
                    server._reattach_spawned_orphans()
                self.assertEqual(len(server._spawned_sessions), 1)
                self.assertEqual(server._spawned_sessions[0]["engine"], "claude")
            finally:
                server.SPAWNED_PIDS_FILE = orig_registry
                server._spawned_sessions.clear()
                server._spawned_sessions.extend(orig_sessions)


    def test_reveal_file_route_registered(self):
        """Smoke check: POST /api/reveal-file branch present in do_POST."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn('"/api/reveal-file"', src)
        # Defense-in-depth: extension clamp must be referenced near the
        # endpoint. Cheap signal that the security control wasn't dropped.
        idx = src.find('"/api/reveal-file"')
        self.assertGreater(idx, 0)
        nearby = src[idx:idx + 2000]
        self.assertIn("FILE_EXT_TO_CATEGORY", nearby,
                      "extension clamp missing near /api/reveal-file route")

    def test_open_target_resolves_relative_session_cwd_markdown(self):
        """Inline transcript links should resolve from the selected session cwd."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            checkpoint = session_cwd / ".claude" / "team-checkpoint.md"
            checkpoint.write_text("# checkpoint\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    ".claude/team-checkpoint.md",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), checkpoint.resolve())
        self.assertFalse(result["core_sandbox"])
        self.assertTrue(result["session_sandbox"])

    def test_open_launch_allows_markdown_session_cwd_files(self):
        """Markdown transcript links may launch externally via macOS open."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            doc = session_cwd / "notes.md"
            doc.write_text("# notes\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "notes.md",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_open_target_strips_markdown_angle_wrapped_paths(self):
        """Markdown links to paths with spaces use <...>; /api/open should unwrap."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "non-code projects" / "ADS"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            report_dir = repo / "final" / "posthog-export"
            report_dir.mkdir(parents=True)
            report = report_dir / "paid-meta-posthog-analysis-20260512.md"
            report.write_text("# report\n")

            result = server._resolve_open_target(
                f"<{report}>",
                cwd=str(repo),
                repo_path=str(repo),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), report.resolve())
        self.assertTrue(result["core_sandbox"])

    def test_open_target_falls_back_when_archive_repo_path_is_virtual(self):
        """Archive rows may pass a display slug as repo_path; cwd should win."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            doc = session_cwd / "notes.md"
            doc.write_text("# notes\n")

            result = server._resolve_open_target(
                str(doc),
                session_id="11111111-2222-3333-4444-555555555555",
                cwd=str(session_cwd),
                repo_path="-virtual-archive-folder",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), doc.resolve())

    def test_open_target_allows_exact_session_tool_file_outside_cwd(self):
        """Files explicitly touched by the selected session are revealable."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            external = root / "library" / "stack.md"
            external.parent.mkdir()
            external.write_text("# stack\n")

            with mock.patch.object(
                server,
                "_scan_session_tool_paths",
                return_value=([str(external)], []),
            ):
                result = server._resolve_open_target(
                    str(external),
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])
        self.assertTrue(result["session_file_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_open_target_resolves_absolute_looking_project_relative(self):
        """`/foo/bar` inside a transcript should fall back to repo-relative when no FS-root match exists."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            sub = repo / "growth-machine" / "content" / "landing"
            sub.mkdir(parents=True)
            target_file = sub / "index.html"
            target_file.write_text("<!doctype html>\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "/growth-machine/content/landing/index.html",
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"], msg=result)
        self.assertEqual(pathlib.Path(result["path"]), target_file.resolve())

    def test_open_target_allows_files_outside_sandbox(self):
        """Post-sandbox-removal: any resolvable path is allowed through /api/open."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            external = root / "library" / "stack.md"
            external.parent.mkdir()
            external.write_text("# stack\n")

            with mock.patch.object(
                server,
                "_scan_session_tool_paths",
                return_value=([], []),
            ):
                result = server._resolve_open_target(
                    str(external),
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])

    def test_open_launch_allowed_for_any_resolved_target(self):
        """Post-sandbox-removal: _open_launch_allowed returns True unconditionally."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            image = session_cwd / "screenshot.png"
            image.write_bytes(b"not really a png")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "screenshot.png",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_open_target_allows_command_center_pasted_images(self):
        """CCC-uploaded pasted images should be revealable from transcript links."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

        result = server._resolve_open_target(
            str(image),
            cwd=str(self.repo),
            repo_path=str(self.repo),
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])
        self.assertTrue(result["pasted_image_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_spawn_codex_attaches_command_center_pasted_images(self):
        """Pasted image paths in Codex prompts should be sent as --image args."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        proc = mock.Mock(pid=4242)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_codex_bin",
                return_value={"available": True, "bin": "/usr/bin/codex-test"},
            ), mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.spawn_session_codex(
                    f"inspect this screenshot {image}",
                    name="image prompt",
                    repo_path=str(self.repo),
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("--image", cmd)
        self.assertEqual(cmd[cmd.index("--image") + 1], str(image))

    def test_resume_codex_attaches_command_center_pasted_images(self):
        """Resumed Codex sessions need the same pasted-image attachment path."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        sid = "00000000-0000-4000-8000-000000000003"
        proc = mock.Mock(pid=4243)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.dict(os.environ, {"CCC_CODEX_APP_SERVER": "0"}), \
                 mock.patch.object(
                     server,
                     "_resolve_codex_bin",
                     return_value={"available": True, "bin": "/usr/bin/codex-test"},
                 ), mock.patch.object(server, "_codex_thread_row", return_value={"cwd": str(self.repo)}), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.resume_session_codex(sid, f"look at {image}")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("--image", cmd)
        self.assertEqual(cmd[cmd.index("--image") + 1], str(image))
        self.assertIn(sid, cmd)

    def test_resolve_cursor_bin_honors_env(self):
        server = self.server
        cursor_bin = pathlib.Path(self.tmp_home, "cursor-agent")
        cursor_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cursor_bin.chmod(cursor_bin.stat().st_mode | stat.S_IXUSR)

        with mock.patch.dict(os.environ, {"CCC_CURSOR_BIN": str(cursor_bin)}):
            result = server._resolve_cursor_bin()

        self.assertTrue(result["available"])
        self.assertEqual(result["bin"], str(cursor_bin))
        self.assertEqual(result["source"], "env")

    def test_spawn_cursor_builds_stream_json_command(self):
        server = self.server
        proc = mock.Mock(pid=4247)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_cursor_bin",
                return_value={"available": True, "bin": "/usr/bin/cursor-agent-test"},
            ), mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry") as record, \
                 mock.patch.object(server, "_wait_for_spawn_session_id", return_value=None):
                result = server.spawn_session_cursor(
                    "do cursor work",
                    name="cursor work",
                    repo_path=str(self.repo),
                    model="composer-2.5",
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "cursor")
        self.assertEqual(result["model"], "composer-2.5")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/bin/cursor-agent-test")
        self.assertIn("--print", cmd)
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")
        self.assertIn("--stream-partial-output", cmd)
        self.assertIn("--force", cmd)
        self.assertIn("--trust", cmd)
        self.assertEqual(cmd[cmd.index("--workspace") + 1], str(self.repo))
        self.assertEqual(cmd[cmd.index("--model") + 1], "composer-2.5")
        self.assertEqual(cmd[-1], "do cursor work")
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.repo))
        record.assert_called_once()

    def test_resume_cursor_queues_when_resume_already_running(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000004"
        original_spawns = list(server._spawned_sessions)
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        server._spawned_sessions[:] = [{
            "engine": "cursor",
            "resumed_sid": sid,
            "pid": 4248,
        }]
        try:
            with mock.patch.object(
                server,
                "_resolve_cursor_bin",
                return_value={"available": True, "bin": "/usr/bin/cursor-agent-test"},
            ), mock.patch.object(server, "_poll_spawn_entry", return_value=None), \
                 mock.patch.object(server.subprocess, "Popen") as popen:
                result = server.resume_session_cursor(sid, "second")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "cursor-resume-queued")
        popen.assert_not_called()
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["second"])
            server._pending_resume_queue.clear()
            server._pending_resume_queue.update(original_queue)

    def test_parse_cursor_event_reads_text_and_tool_blocks(self):
        server = self.server
        ev = {
            "role": "assistant",
            "timestamp": "2026-06-01T12:00:00Z",
            "message": {
                "content": [
                    {"type": "text", "text": "I will inspect it."},
                    {
                        "type": "tool_use",
                        "id": "toolu-cursor",
                        "name": "run_terminal_cmd",
                        "input": {"command": "git status --short"},
                    },
                ],
            },
        }

        parsed = server._parse_cursor_event(ev, 7)

        self.assertEqual(parsed["type"], "assistant")
        self.assertEqual(parsed["message_id"], "cursor-7")
        self.assertEqual(parsed["blocks"][0]["kind"], "text")
        self.assertEqual(parsed["blocks"][0]["text"], "I will inspect it.")
        self.assertEqual(parsed["blocks"][1]["kind"], "tool_use")
        self.assertEqual(parsed["blocks"][1]["name"], "run_terminal_cmd")
        self.assertEqual(parsed["blocks"][1]["id"], "toolu-cursor")
        self.assertIn("git status --short", parsed["blocks"][1].get("detail", ""))

    def test_parse_cursor_event_skips_redacted_placeholder_text(self):
        server = self.server
        ev = {
            "role": "assistant",
            "timestamp": "2026-06-01T12:00:00Z",
            "message": {
                "content": [
                    {"type": "text", "text": "[REDACTED]"},
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"path": str(self.repo / "server.py")},
                    },
                    {"type": "text", "text": "Done.\n\n[REDACTED]"},
                ],
            },
        }

        parsed = server._parse_cursor_event(ev, 8)

        self.assertEqual(parsed["type"], "assistant")
        self.assertEqual([b["kind"] for b in parsed["blocks"]], ["tool_use", "text"])
        self.assertEqual(parsed["blocks"][1]["text"], "Done.")
        self.assertNotIn("[REDACTED]", json.dumps(parsed))

    def test_resume_cursor_prefers_current_default_over_stale_spawn_model(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000006"
        proc = mock.Mock(pid=4249)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.dict(os.environ, {"CCC_CURSOR_MODEL": ""}), \
                 mock.patch.object(
                     server,
                     "_resolve_cursor_bin",
                     return_value={"available": True, "bin": "/usr/bin/cursor-agent-test"},
                 ), mock.patch.object(
                     server,
                     "_spawn_registry_entry_for_session",
                     return_value={"cwd": str(self.repo), "model": "composer-2.5-fast"},
                 ), mock.patch.object(server, "_cursor_transcript_path", return_value=None), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server, "_spawn_model_for_engine", return_value="auto"), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.resume_session_cursor(sid, "second")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "auto")
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--model") + 1], "auto")

    def test_resume_cursor_reports_immediate_usage_limit_failure(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000008"
        proc = mock.Mock(pid=4250)
        proc.poll.return_value = 0
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_cursor_bin",
                return_value={"available": True, "bin": "/usr/bin/cursor-agent-test"},
            ), mock.patch.object(
                server,
                "_spawn_registry_entry_for_session",
                return_value={"cwd": str(self.repo), "model": "auto"},
            ), mock.patch.object(server, "_cursor_transcript_path", return_value=None), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc), \
                 mock.patch.object(server.time, "sleep"), \
                 mock.patch.object(
                     server,
                     "_antigravity_read_log_tail",
                     return_value="S: You've hit your usage limit Get Cursor Pro for more Agent usage.",
                 ), mock.patch.object(server, "_record_spawn_to_registry") as record:
                result = server.resume_session_cursor(sid, "second")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertFalse(result["ok"])
        self.assertEqual(result["via"], "cursor-resume")
        self.assertIn("usage limit", result["error"])
        record.assert_not_called()

    def test_resolve_cursor_bin_uses_local_bin_candidate(self):
        server = self.server
        cursor_bin = pathlib.Path(self.tmp_home, ".local", "bin", "cursor-agent")
        cursor_bin.parent.mkdir(parents=True)
        cursor_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cursor_bin.chmod(cursor_bin.stat().st_mode | stat.S_IXUSR)

        with mock.patch.dict(os.environ, {"CCC_CURSOR_BIN": ""}), \
             mock.patch.object(server.shutil, "which", return_value=None), \
             mock.patch.object(server, "CURSOR_LOCAL_BIN", cursor_bin), \
             mock.patch.object(server, "CURSOR_APP_BUNDLE_CANDIDATES", ()):
            result = server._resolve_cursor_bin()

        self.assertTrue(result["available"])
        self.assertEqual(result["bin"], str(cursor_bin))
        self.assertEqual(result["source"], "candidate")

    def test_find_cursor_conversations_reads_agent_transcript(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000005"
        slug = server._cursor_project_slug(self.repo)
        transcript_dir = server.CURSOR_PROJECTS_ROOT / slug / "agent-transcripts" / sid
        transcript_dir.mkdir(parents=True)
        transcript_path = transcript_dir / f"{sid}.jsonl"
        transcript_path.write_text(
            "\n".join([
                json.dumps({
                    "role": "user",
                    "timestamp": "2026-06-01T12:00:00Z",
                    "message": {"content": [{"type": "text", "text": "<user_query>\nPlease inspect\n</user_query>"}]},
                }),
                json.dumps({
                    "role": "assistant",
                    "timestamp": "2026-06-01T12:01:00Z",
                    "message": {
                        "content": [
                            {"type": "text", "text": "I will commit it."},
                            {
                                "type": "tool_use",
                                "name": "run_terminal_cmd",
                                "input": {"command": "git commit -m 'cursor test'"},
                            },
                        ],
                    },
                }),
            ]) + "\n",
            encoding="utf-8",
        )

        rows = server.find_cursor_conversations(
            repo_path=str(self.repo),
            include_old=True,
            repo_only=True,
            resolve_pr_states=False,
            resolve_worktree_dirty=False,
        )
        parsed = server.parse_conversation(sid, use_cache=False)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "cursor")
        self.assertEqual(rows[0]["engine"], "cursor")
        self.assertEqual(rows[0]["first_message"], "Please inspect")
        self.assertTrue(rows[0]["has_commit"])
        self.assertEqual(parsed["events"][0]["type"], "user_text")
        self.assertEqual(parsed["events"][1]["type"], "assistant")

    def test_find_cursor_conversations_ignores_redacted_placeholder_tail(self):
        server = self.server
        sid = "00000000-0000-4000-8000-000000000007"
        slug = server._cursor_project_slug(self.repo)
        transcript_dir = server.CURSOR_PROJECTS_ROOT / slug / "agent-transcripts" / sid
        transcript_dir.mkdir(parents=True)
        transcript_path = transcript_dir / f"{sid}.jsonl"
        transcript_path.write_text(
            "\n".join([
                json.dumps({
                    "role": "user",
                    "timestamp": "2026-06-01T12:00:00Z",
                    "message": {"content": [{"type": "text", "text": "Please inspect"}]},
                }),
                json.dumps({
                    "role": "assistant",
                    "timestamp": "2026-06-01T12:01:00Z",
                    "message": {
                        "content": [
                            {"type": "text", "text": "[REDACTED]"},
                            {
                                "type": "tool_use",
                                "name": "Grep",
                                "input": {"pattern": "needle", "path": str(self.repo)},
                            },
                        ],
                    },
                }),
            ]) + "\n",
            encoding="utf-8",
        )
        server._conv_meta_cache.clear()

        rows = server.find_cursor_conversations(
            repo_path=str(self.repo),
            include_old=True,
            repo_only=True,
            resolve_pr_states=False,
            resolve_worktree_dirty=False,
        )
        row = next(r for r in rows if r["session_id"] == sid)
        parsed = server.parse_conversation(sid, use_cache=False)

        self.assertIsNone(row["last_assistant_text"])
        self.assertIsNone(row["pending_tool"])
        self.assertEqual(parsed["events"][1]["blocks"][0]["kind"], "tool_use")
        self.assertNotIn("[REDACTED]", json.dumps(parsed["events"]))

    def test_codex_app_server_queues_active_turn(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append((method, params, timeout))
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "active", "activeFlags": []},
                            "turns": [
                                {"id": "turn-old", "status": "completed"},
                                {"id": "turn-active", "status": "inProgress"},
                            ],
                        }
                    }
                }
            if method == "turn/start":
                return {"result": {"turn": {"id": "turn-next"}}}
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            result = server._codex_resume_or_steer_via_app_server(
                sid,
                "look here",
                cwd=str(self.repo),
                model="gpt-test",
                image_paths=["/tmp/paste.png"],
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "codex-app-queued")
        self.assertEqual(calls[0][0], "thread/resume")
        self.assertEqual(calls[1][0], "turn/start")
        start_params = calls[1][1]
        self.assertEqual(start_params["threadId"], sid)
        self.assertNotIn("cwd", start_params)
        self.assertNotIn("model", start_params)
        self.assertNotIn("approvalPolicy", start_params)
        self.assertNotIn("sandboxPolicy", start_params)
        self.assertEqual(
            start_params["input"],
            [
                {"type": "text", "text": "look here"},
                {"type": "localImage", "path": "/tmp/paste.png"},
            ],
        )

    def test_codex_app_server_steers_active_turn(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append((method, params, timeout))
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "active", "activeFlags": []},
                            "turns": [
                                {"id": "turn-old", "status": "completed"},
                                {"id": "turn-active", "status": "inProgress"},
                            ],
                        }
                    }
                }
            if method == "turn/steer":
                return {"result": {"turnId": "turn-active"}}
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            result = server._codex_steer_via_app_server(
                sid,
                "look now",
                cwd=str(self.repo),
                model="gpt-test",
                image_paths=["/tmp/paste.png"],
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-steer")
        self.assertEqual(calls[0][0], "thread/resume")
        self.assertEqual(calls[1][0], "turn/steer")
        steer_params = calls[1][1]
        self.assertEqual(steer_params["expectedTurnId"], "turn-active")
        self.assertEqual(
            steer_params["input"],
            [
                {"type": "text", "text": "look now"},
                {"type": "localImage", "path": "/tmp/paste.png"},
            ],
        )

    def test_codex_app_server_steer_requires_active_turn(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append(method)
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "idle"},
                            "turns": [],
                        }
                    }
                }
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            result = server._codex_steer_via_app_server(sid, "look now")

        self.assertFalse(result["ok"])
        self.assertEqual(result["via"], "codex-steer")
        self.assertEqual(result["code"], "codex_no_active_turn")
        self.assertEqual(calls, ["thread/resume"])

    def test_codex_app_server_does_not_start_parallel_turn_when_disallowed(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        calls = []

        def fake_request(method, params=None, timeout=20):
            calls.append(method)
            if method == "thread/resume":
                return {
                    "result": {
                        "thread": {
                            "status": {"type": "idle"},
                            "turns": [],
                        }
                    }
                }
            raise AssertionError(f"unexpected method: {method}")

        with mock.patch.object(server, "_codex_app_server_request", side_effect=fake_request):
            result = server._codex_resume_or_steer_via_app_server(
                sid,
                "second",
                allow_start=False,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["fallback"], "queue")
        self.assertEqual(calls, ["thread/resume"])

    def test_resume_codex_prefers_app_server_before_queued_cli_resume(self):
        server = self.server
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        original_spawns = list(server._spawned_sessions)
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        server._spawned_sessions[:] = [{
            "engine": "codex",
            "resumed_sid": sid,
            "pid": 4242,
        }]
        try:
            with mock.patch.object(
                server,
                "_resolve_codex_bin",
                return_value={"available": True, "bin": "/usr/bin/codex-test"},
            ), mock.patch.object(server, "_codex_thread_row", return_value={"cwd": str(self.repo)}), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(
                     server,
                     "_codex_resume_or_steer_via_app_server",
                     return_value={"ok": True, "queued": True, "via": "codex-app-queued"},
                 ) as app_queue, \
                 mock.patch.object(server, "_poll_spawn_entry", return_value=None), \
                 mock.patch.object(server.subprocess, "Popen") as popen:
                result = server.resume_session_codex(sid, "second")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)
            with server._pending_resume_lock:
                server._pending_resume_queue.clear()
                server._pending_resume_queue.update(original_queue)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "codex-app-queued")
        app_queue.assert_called_once()
        popen.assert_not_called()

    def test_resume_antigravity_adds_pasted_image_dir(self):
        """AGY needs pasted-image folders in its repeatable --add-dir workspace."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        sid = "00000000-0000-4000-8000-000000000004"
        conv = pathlib.Path(self.tmp_home) / "ag.pb"
        conv.write_bytes(b"pb")
        proc = mock.Mock(pid=4244)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server, "_antigravity_cli_conversation_path", return_value=conv), \
                 mock.patch.object(server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.resume_session_antigravity(sid, f"look at {image}")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        add_dirs = [cmd[i + 1] for i, word in enumerate(cmd[:-1]) if word == "--add-dir"]
        self.assertIn(str(self.repo), add_dirs)
        self.assertIn(str(paste_dir.resolve()), add_dirs)

    def test_spawn_antigravity_writes_model_to_cli_settings(self):
        """AGY print mode reads its model from settings.json, not argv."""
        server = self.server
        settings_path = server.ANTIGRAVITY_CLI_SETTINGS
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({
            "colorScheme": "dark",
            "model": "Gemini 3.1 Pro (Low)",
        }))
        proc = mock.Mock(pid=4246)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.spawn_session_antigravity(
                    "hello from agy",
                    name="agy model",
                    repo_path=str(self.repo),
                    model="gemini-3.5-flash-high",
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "Gemini 3.5 Flash (High)")
        self.assertEqual(result["engine"], "antigravity")
        self.assertEqual(result["repo_path"], str(self.repo))
        self.assertEqual(result["cwd"], str(self.repo))
        self.assertRegex(result["session_id"], r"^[0-9a-f-]{36}$")
        self.assertFalse(result["session_id_pending"])
        settings = json.loads(settings_path.read_text())
        self.assertEqual(settings["model"], "Gemini 3.5 Flash (High)")
        self.assertEqual(settings["colorScheme"], "dark")
        cmd = popen.call_args.args[0]
        self.assertNotIn("--model", cmd)
        self.assertIn("-p", cmd)

    def test_resume_antigravity_queues_when_resume_already_running(self):
        """A second AGY follow-up should queue instead of spawning parallel resumes."""
        server = self.server
        sid = "00000000-0000-4000-8000-000000000004"
        conv = pathlib.Path(self.tmp_home) / "ag.pb"
        conv.write_bytes(b"pb")
        original_spawns = list(server._spawned_sessions)
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        server._spawned_sessions[:] = [{
            "engine": "antigravity",
            "resumed_sid": sid,
            "pid": 4245,
        }]
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server, "_antigravity_cli_conversation_path", return_value=conv), \
                 mock.patch.object(server, "_poll_spawn_entry", return_value=None), \
                 mock.patch.object(server.subprocess, "Popen") as popen:
                result = server.resume_session_antigravity(sid, "second")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "antigravity-resume-queued")
        popen.assert_not_called()
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["second"])
            server._pending_resume_queue.clear()
            server._pending_resume_queue.update(original_queue)

    def test_open_target_allows_executable_session_cwd_files(self):
        """Post-sandbox-removal: scripts in the session cwd resolve cleanly."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            script = session_cwd / "run.sh"
            script.write_text("#!/bin/sh\nexit 0\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "run.sh",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_markdown_path_links_request_external_open(self):
        """The transcript click handler asks /api/open to launch markdown."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function _isMarkdownPath", js)
        self.assertIn("function normalizeMarkdownLinkTarget", js)
        self.assertIn("payload.launch = true", js)

    def test_absolute_folder_path_links_are_not_web_routes(self):
        """Extensionless /Users/... folders should still go through /api/open."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function _isAbsoluteFilesystemPath", js)
        self.assertIn(
            "Users|Volumes|Applications|Library|System|private|tmp|var|etc|opt|usr|bin|sbin|home",
            js,
        )
        self.assertIn("if (_isAbsoluteFilesystemPath(p)) return false;", js)

    def test_inline_code_skips_placeholder_and_api_path_links(self):
        """Auto-linking should avoid shortened paths and internal API mentions."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function _shouldLinkifyInlineCodePath", js)
        self.assertIn("function _isPlaceholderPathToken", js)
        self.assertIn("function _isInternalApiPathToken", js)
        self.assertIn("if (_shouldLinkifyInlineCodePath(inner))", js)

    def test_archive_progress_does_not_replace_search_empty_state(self):
        """Background archive refresh must not clobber no-match search results."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn(".archive-loading-placeholder, .archive-loading-stages", js)
        self.assertIn("archive-empty-state archive-loading-placeholder", js)
        self.assertIn("No conversations match your filter.", js)
        self.assertNotIn(".archive-empty-state, .archive-loading-stages", js)

    def test_original_ask_renders_pasted_images_inline(self):
        """Pasted-image references should become images in the ask panels."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("PASTED_IMG_MD_LINK_RE", js)
        self.assertIn("function pastedImageTag", js)
        self.assertIn("/api/pasted-image?path=", js)
        self.assertIn("const imagesHtml = renderImageDescriptors(ev.images);", js)
        self.assertIn("h += imagesHtml;", js)

    def test_archive_search_refresh_preserves_scroll(self):
        """Periodic archive refreshes should not snap active search results."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("let _lastArchiveRenderFilter = null;", js)
        self.assertIn("function _captureArchiveListScroll", js)
        self.assertIn("function _restoreArchiveListScroll", js)
        self.assertIn("_lastArchiveRenderFilter = q;", js)

    def test_files_endpoint_route_registered(self):
        """Smoke check: GET /api/conversations/<id>/files dispatcher
        branch must be present in the do_GET source. Route registration
        in this codebase is by literal regex string, so a substring grep
        is the cheapest assertion."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn("/api/conversations/(?:[a-f0-9-]+|ses_[A-Za-z0-9]+)/files", src)

    def test_session_initial_scan_keeps_recent_and_live_rows(self):
        """Initial /api/sessions scans should avoid cold history while keeping
        live sessions even when their transcript mtime is old."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            files = []
            for name, age in (
                ("old-cold", 20_000),
                ("old-live", 20_000),
                ("recent-newest", 10),
                ("recent-older", 20),
            ):
                p = root / f"{name}.jsonl"
                p.write_text("{}\n")
                os.utime(p, (now - age, now - age))
                files.append(p)

            selected, meta = server._filter_conversation_jsonls(
                files,
                include_old=False,
                always_include_sids={"old-live"},
                cutoff_ts=now - 1000,
                max_files=2,
            )

        stems = [p.stem for p in selected]
        self.assertEqual(len(stems), 2)
        self.assertIn("old-live", stems)
        self.assertIn("recent-newest", stems)
        self.assertNotIn("old-cold", stems)
        self.assertTrue(meta["limited"])

    def test_session_usage_falls_back_to_diagnostic_context_sample(self):
        """Newer Claude transcripts can omit `message.usage` while still
        carrying a diagnostic context-size hint. The footer should use that
        instead of showing no context data at all."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        event = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "diagnostics": {
                    "cache_miss_reason": {
                        "type": "tools_changed",
                        "cache_missed_input_tokens": 57261,
                    },
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(json.dumps(event) + "\n")
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(usage["latest_input_tokens"], 57261)
        self.assertEqual(usage["peak_input_tokens"], 57261)
        self.assertEqual(usage["model"], "claude-opus-4-7")

    def test_tail_meta_extracts_assistant_model(self):
        """Conversation rows should carry the model even before the usage
        endpoint finishes, so the footer has a stable fallback."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        event = {
            "type": "assistant",
            "timestamp": "2026-05-03T12:00:00.000Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "done"}],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "s.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = server._extract_tail_meta(path)

        self.assertEqual(meta["model"], "claude-sonnet-4-6")


    def test_coordinate_sessions_helper_exists(self):
        """_coordinate_sessions must exist and reject empty topic."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_coordinate_sessions"))
        result = server._coordinate_sessions({"session_ids": ["abc"], "topic": ""})
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_group_chat_read_helper_exists_and_rejects_traversal(self):
        """_group_chat_read must exist and block path traversal outside group-chats/."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_group_chat_read"))
        result, forbidden = server._group_chat_read("/etc/passwd")
        self.assertIsNone(result)
        self.assertEqual(forbidden, "forbidden")

    def test_group_chat_post_helper_exists_and_rejects_traversal(self):
        """_group_chat_post must exist and block writes outside group-chats/."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_group_chat_post"))
        result = server._group_chat_post("/etc/passwd", "hacked")
        self.assertFalse(result["ok"])
        self.assertIn("forbidden", result.get("error", ""))

    def test_group_chat_reader_restores_composer_for_new_session(self):
        """New session must clear group-chat reader chrome before showing composer."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function stopGroupChatReader", js)
        self.assertIn("stopGroupChatReader({ rerenderSidebar: true });", js)
        self.assertIn("function enterNewSessionMode()", js)
        self.assertIn("currentConversation = '__new__';", js)

    def test_group_chat_reader_has_tts_and_conversation_typography(self):
        """Group-chat reader should expose TTS and reuse assistant markdown styling."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text()
        self.assertIn("function renderGroupChatMarkdown", js)
        self.assertIn("gc-message-body assistant-text", js)
        self.assertIn('id="gcTtsBtn"', js)
        self.assertIn(".conv-input-bar .tts-btn, .gc-reader .tts-btn", js)
        self.assertIn(".conversations-view .gc-message-body.assistant-text", css)
        self.assertIn(".gc-reader-input-row .tts-btn", css)


class TestModelPicker(unittest.TestCase):
    def test_short_model_alias_strips_claude_prefix_and_1m_suffix(self):
        """`/model` slash command takes the alias form, not the full id —
        the helper has to round-trip both shapes consistently."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        self.assertEqual(server._short_model_alias("claude-sonnet-4-6"), "sonnet-4-6")
        self.assertEqual(server._short_model_alias("claude-sonnet-4-6[1m]"), "sonnet-4-6")
        self.assertEqual(server._short_model_alias("opus-4-7"), "opus-4-7")
        self.assertEqual(server._short_model_alias("sonnet"), "sonnet")
        self.assertEqual(server._short_model_alias(""), "")
        self.assertEqual(server._short_model_alias(None), "")

    def test_build_slash_model_command_appends_1m_suffix_when_requested(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        self.assertEqual(server._build_slash_model_command("opus-4-7", False), "/model opus-4-7")
        self.assertEqual(server._build_slash_model_command("opus-4-7", True), "/model opus-4-7[1m]")
        self.assertEqual(server._build_slash_model_command("claude-sonnet-4-6", True), "/model sonnet-4-6[1m]")
        self.assertEqual(server._build_slash_model_command("", True), "")

    def test_session_override_roundtrip_through_sidecar(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "session-overrides.json"
            orig = server.SESSION_OVERRIDES_FILE
            server.SESSION_OVERRIDES_FILE = path
            try:
                self.assertIsNone(server._get_session_override("sid-1"))
                server._set_session_override("sid-1", "claude-sonnet-4-6", True, "claude")
                got = server._get_session_override("sid-1")
                self.assertEqual(got["model"], "claude-sonnet-4-6")
                self.assertTrue(got["context_1m"])
                self.assertEqual(got["engine"], "claude")
                server._clear_session_override("sid-1")
                self.assertIsNone(server._get_session_override("sid-1"))
            finally:
                server.SESSION_OVERRIDES_FILE = orig

    def test_pinned_conversations_roundtrip_and_sort_first(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp)
            path = state_dir / "pinned-conversations.json"
            orig_file = server.PINNED_CONVERSATIONS_FILE
            orig_state = server.LOG_VIEWER_STATE_DIR
            server.PINNED_CONVERSATIONS_FILE = path
            server.LOG_VIEWER_STATE_DIR = state_dir
            try:
                server._save_pinned_conversations(["sid-2", "sid-1"])
                self.assertEqual(server._load_pinned_conversations(), ["sid-2", "sid-1"])
                rows = [
                    {"session_id": "sid-3", "modified": 30},
                    {"session_id": "sid-1", "modified": 10},
                    {"session_id": "sid-2", "modified": 20},
                ]
                server._apply_pinned_conversation_fields(rows)
                server._sort_pinned_conversations_first(rows)
                self.assertEqual([r["session_id"] for r in rows], ["sid-2", "sid-1", "sid-3"])
                self.assertTrue(rows[0]["pinned"])
                self.assertEqual(rows[0]["pin_rank"], 0)
            finally:
                server.PINNED_CONVERSATIONS_FILE = orig_file
                server.LOG_VIEWER_STATE_DIR = orig_state

    def test_pin_route_and_row_action_hooks_are_registered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text()
        self.assertIn("/api/conversations/(?:[a-f0-9-]+|ses_[A-Za-z0-9]+)/files", src)
        self.assertIn("class=\"conv-pin-btn", js)
        self.assertIn("mergeBtn + startBtn + pinBtn + archiveBtn", js)
        self.assertIn("Pinned to top", js)
        self.assertIn("_minPinnedRank", js)
        self.assertNotIn("conv-pinned-section", js)
        self.assertIn("if (c.pinned) return true", js)
        self.assertIn("c.pinned ||", js)
        self.assertIn("applyOptimisticOverrides(rowsForRender)", js)
        self.assertIn("function _restoreConversationListScrollTop", js)
        self.assertIn("const pinScrollTop = $convList ? $convList.scrollTop : null", js)
        self.assertIn("_restoreConversationListScrollTop($convList, pinScrollTop)", js)
        self.assertNotIn("scrollConversationRowIntoView(convId, data.pinned ? 'start' : 'nearest')", js)
        self.assertIn(".conv-item .conv-pin-btn", css)
        self.assertIn(".conv-item.is-pinned:not(:hover):not(:focus-within) .conv-row-actions:not(:empty)", css)
        self.assertIn(".conv-item.is-pinned:not(:hover):not(:focus-within) .conv-pin-btn.is-unpin", css)
        self.assertIn(".conv-item .conv-pin-btn.is-unpin:hover .conv-pin-glyph::before", css)
        self.assertIn(".conv-item .conv-pin-btn.is-unpin:hover .conv-pin-glyph::after", css)
        self.assertNotIn("#convList .conv-pinned-section", css)

    def test_session_model_route_registered_and_check_same_origin_gates_post(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        # Routes registered
        self.assertIn("/api/session/[a-zA-Z0-9-]+/model", src)
        self.assertIn("/api/session/[a-zA-Z0-9-]+/model/clear", src)
        # do_POST gates everything through _check_same_origin first
        post_idx = src.find("def do_POST")
        self.assertGreater(post_idx, 0)
        self.assertIn("_check_same_origin", src[post_idx:post_idx + 200])

    def test_extract_session_slash_commands_from_init_event(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps({
                    "type": "system",
                    "subtype": "init",
                    "slash_commands": [
                        "/compact",
                        {"name": "project:ship", "description": "Ship this repo"},
                        {"command": "/review", "purpose": "Review changes"},
                    ],
                }) + "\n"
            )
            orig = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                result = server.extract_session_slash_commands(sid)
            finally:
                server.PROJECTS_ROOT = orig

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "transcript")
        commands = {c["name"]: c.get("description", "") for c in result["commands"]}
        self.assertIn("/compact", commands)
        self.assertIn("/mcp", commands)
        self.assertEqual(commands["/project:ship"], "Ship this repo")
        self.assertEqual(commands["/review"], "Review changes")

    def test_slash_command_files_and_skills_are_discovered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            command_dir = root / "commands"
            command_dir.mkdir()
            (command_dir / "ship.md").write_text("# Ship\n\nRun the release flow.\n")
            nested = command_dir / "commit-commands"
            nested.mkdir()
            (nested / "commit.md").write_text("---\ndescription: Commit current work\n---\n")
            (command_dir / "old.md.bak").write_text("# Ignore\n")

            skill_dir = root / "skills"
            skill = skill_dir / "screenshot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\ndescription: Inspect screenshots\n---\n")

            commands = server._merge_slash_commands(
                server._slash_commands_from_command_dir(command_dir),
                server._slash_commands_from_command_dir(command_dir, prefix="plugin-name"),
                server._slash_commands_from_skill_dir(skill_dir),
            )

        names = {c["name"]: c.get("description", "") for c in commands}
        self.assertEqual(names["/ship"], "Ship")
        self.assertEqual(names["/commit-commands:commit"], "Commit current work")
        self.assertEqual(names["/plugin-name:ship"], "Ship")
        self.assertEqual(names["/screenshot"], "Inspect screenshots")
        self.assertNotIn("/old.md", names)

    def test_session_slash_commands_route_registered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn("/api/session/[a-zA-Z0-9-]+/slash-commands", src)

    def test_extract_session_usage_resets_at_compact_boundary(self):
        """`/compact` emits a `compact_boundary` system event; assistant
        turns before that boundary no longer contribute to the live
        context window. The pre-fix behavior accumulated peak across
        the whole file, overstating usage."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-666666666666"
        big_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "before compact"}],
                "usage": {
                    "input_tokens": 80_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 100_000,
                    "output_tokens": 500,
                },
            },
        }
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "manual", "preTokens": 180_500},
        }
        small_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "after compact"}],
                "usage": {
                    "input_tokens": 1_200,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 9_000,
                    "output_tokens": 80,
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps(big_turn) + "\n"
                + json.dumps(boundary) + "\n"
                + json.dumps(small_turn) + "\n"
            )
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root
        # latest = post-compact small turn's window
        self.assertEqual(usage["latest_input_tokens"], 1_200 + 9_000)
        # peak resets at the boundary, so it's the post-compact peak — NOT the big pre-compact value
        self.assertEqual(usage["peak_input_tokens"], 1_200 + 9_000)
        self.assertEqual(usage["compact_count"], 1)

    def test_extract_session_usage_uses_compact_post_tokens_until_next_turn(self):
        """A compact boundary is the first reliable post-compact signal.
        Use its postTokens count immediately instead of leaving the footer
        pinned to the pre-compact peak until another assistant turn lands."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-777777777777"
        big_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "before compact"}],
                "usage": {
                    "input_tokens": 260_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 210_000,
                    "output_tokens": 500,
                },
            },
        }
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": sid,
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 470_054,
                "postTokens": 13_781,
                "durationMs": 130_502,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact-posttokens"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps(big_turn) + "\n"
                + json.dumps(boundary) + "\n"
            )
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(usage["latest_input_tokens"], 13_781)
        self.assertEqual(usage["peak_input_tokens"], 13_781)
        self.assertEqual(usage["compact_count"], 1)
        self.assertEqual(usage["context_limit"], 1_000_000)

    def test_extract_session_usage_captures_slash_context_output(self):
        """The footer can show Claude's live `/context` count separately
        from CCC's post-compact transcript estimate."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-999999999999"
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": sid,
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 773_985,
                "postTokens": 12_673,
            },
        }
        context_output = {
            "type": "system",
            "subtype": "local_command",
            "sessionId": sid,
            "timestamp": "2026-05-26T19:52:30.316Z",
            "content": (
                "<local-command-stdout>## Context Usage\n\n"
                "**Model:** claude-opus-4-7  \n"
                "**Tokens:** 47.3k / 1m (5%)\n\n"
                "### Estimated usage by category\n"
                "| Category | Tokens | Percentage |\n"
                "| Messages | 21.5k | 2.2% |\n"
                "</local-command-stdout>"
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-context-output"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps(boundary) + "\n"
                + json.dumps(context_output) + "\n"
            )
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(usage["latest_input_tokens"], 12_673)
        self.assertEqual(usage["live_context_tokens"], 47_300)
        self.assertEqual(usage["live_context_limit"], 1_000_000)
        self.assertEqual(usage["live_context_percent"], 5)
        self.assertEqual(usage["live_context_source"], "/context")
        self.assertEqual(usage["live_context_timestamp"], "2026-05-26T19:52:30.316Z")
        self.assertEqual(usage["model"], "claude-opus-4-7")

    def test_context_footer_renders_calc_and_slash_context_values(self):
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("Calculated estimate:", js)
        self.assertIn("Latest /context output:", js)
        self.assertIn("'calc'", js)
        self.assertIn("' · /ctx '", js)

    def test_truncate_session_name_clamps_long_pastes(self):
        """A row title that's a full annotation context blob would stretch
        the sidebar and bloat /api/sessions responses; clamp it instead."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        self.assertIsNone(server._truncate_session_name(None))
        self.assertEqual(server._truncate_session_name(""), "")
        self.assertEqual(server._truncate_session_name("   "), "")
        self.assertEqual(server._truncate_session_name("Short title"), "Short title")
        self.assertEqual(
            server._truncate_session_name("hello\n\n   world"),
            "hello world",
        )
        long = "Annotation note: " + ("blah " * 4000)
        clipped = server._truncate_session_name(long)
        self.assertLessEqual(len(clipped), server.SESSION_NAME_MAX_CHARS)
        self.assertTrue(clipped.endswith("…"))

    def test_parse_conversation_surfaces_compact_boundary(self):
        """The transcript pane should show feedback when `/compact` finishes."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-888888888888"
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": sid,
            "timestamp": "2026-05-25T01:54:30.071Z",
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 470_054,
                "postTokens": 13_781,
                "durationMs": 130_502,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact-event"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(json.dumps(boundary) + "\n")
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False):
                    result = server.parse_conversation(sid, use_cache=False)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["type"], "system")
        self.assertEqual(event["subtype"], "compact_boundary")
        self.assertEqual(event["session"], sid)
        self.assertEqual(event["compact"]["trigger"], "manual")
        self.assertEqual(event["compact"]["pre_tokens"], 470_054)
        self.assertEqual(event["compact"]["post_tokens"], 13_781)
        self.assertEqual(event["compact"]["duration_ms"], 130_502)

    def test_extract_antigravity_usage_rpc(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "22222222-3333-4444-5555-777777777777"

        # Test case 1: RPC succeeds and returns usage metrics
        fake_response = {
            "trajectory": {
                "steps": [
                    {
                        "metadata": {
                            "modelUsage": {
                                "model": "gemini-1.5-pro",
                                "inputTokens": "5000",
                                "outputTokens": "150",
                                "cacheReadTokens": "1000",
                                "thinkingTokens": "400",
                            }
                        }
                    },
                    {
                        "metadata": {
                            "modelUsage": {
                                "model": "gemini-1.5-pro",
                                "inputTokens": "6000",
                                "outputTokens": "200",
                                "cacheReadTokens": "1200",
                                "cacheCreationTokens": 100,
                                "thinkingTokens": 600,
                            }
                        }
                    }
                ]
            }
        }

        with mock.patch.object(server, "_antigravity_app_rpc", return_value={"ok": True, "response": fake_response}), \
             mock.patch.object(server, "_is_antigravity_session", return_value=True), \
             mock.patch.object(server, "_antigravity_transcript_path", return_value=None), \
             mock.patch.object(server, "_get_session_override", return_value=None):

            usage = server.extract_session_usage(sid)

            # check stats for second step: input (6000) + cacheRead (1200) + cacheCreation (100) = 7300
            self.assertEqual(usage["latest_input_tokens"], 7300)
            self.assertEqual(usage["peak_input_tokens"], 7300)
            self.assertEqual(usage["total_input_tokens"], 5000 + 6000)
            self.assertEqual(usage["total_cache_read_tokens"], 1000 + 1200)
            self.assertEqual(usage["total_cache_creation_tokens"], 100)
            self.assertEqual(usage["total_output_tokens"], 150 + 200)
            # Per-turn thinking tokens are summed for the bottom-bar totals.
            self.assertEqual(usage["total_thinking_tokens"], 400 + 600)
            self.assertEqual(usage["model"], "gemini-1.5-pro")
            self.assertEqual(usage["engine"], "antigravity")
            self.assertEqual(usage["context_limit"], 1_000_000)

        # Test case 2: RPC fails, it should fall back to empty defaults
        with mock.patch.object(server, "_antigravity_app_rpc", return_value={"ok": False}), \
             mock.patch.object(server, "_is_antigravity_session", return_value=True), \
             mock.patch.object(server, "_antigravity_transcript_path", return_value=None), \
             mock.patch.object(server, "_get_session_override", return_value=None):

            usage = server.extract_session_usage(sid)
            self.assertEqual(usage["latest_input_tokens"], 0)
            self.assertEqual(usage["peak_input_tokens"], 0)
            self.assertEqual(usage["total_thinking_tokens"], 0)
            self.assertEqual(usage["model"], "")
            self.assertEqual(usage["engine"], "antigravity")

    def test_parse_antigravity_event_attaches_per_turn_tokens(self):
        """Assistant events should carry tokens_in/out/thinking when the
        trajectory's modelUsage covers the event's step_index. This is what
        feeds the per-turn chips in the conversation pane."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        usage_map = {
            13: {"in": 11200, "out": 2600, "thinking": 1000,
                 "cache_read": 0, "cache_create": 0, "model": "agy-1"},
        }
        ev_with_step = {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "step_index": 13,
            "created_at": "2026-05-22T10:00:00Z",
            "content": "Here is the plan.",
            "tool_calls": [],
        }
        out = server._parse_antigravity_event(ev_with_step, 99, usage_map=usage_map)
        self.assertIsNotNone(out)
        self.assertEqual(out["type"], "assistant")
        self.assertEqual(out["tokens_in"], 11200)
        self.assertEqual(out["tokens_out"], 2600)
        self.assertEqual(out["tokens_thinking"], 1000)

        # Step index with no matching trajectory entry → no token fields,
        # so the frontend falls back to the no-chip render path.
        ev_no_match = {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "step_index": 999,
            "created_at": "2026-05-22T10:00:01Z",
            "content": "Another step.",
            "tool_calls": [],
        }
        out2 = server._parse_antigravity_event(ev_no_match, 100, usage_map=usage_map)
        self.assertIsNotNone(out2)
        self.assertNotIn("tokens_in", out2)
        self.assertNotIn("tokens_out", out2)
        self.assertNotIn("tokens_thinking", out2)

        # Old call shape (no usage_map kw) must still work.
        out3 = server._parse_antigravity_event(ev_with_step, 101)
        self.assertIsNotNone(out3)
        self.assertNotIn("tokens_in", out3)



class TestGroupChatSidecarHelpers(unittest.TestCase):
    """Cover the small helpers that load/merge sidecar JSON, list chats,
    and flip the archived flag. Uses a tempdir-backed fake group-chats dir
    so we don't touch the user's real ~/.claude/group-chats."""

    def _setup_fake_dir(self, server, tmpdir):
        """Patch the helpers to look at a tempdir instead of ~/.claude/group-chats."""
        gcd = pathlib.Path(tmpdir) / "group-chats"
        gcd.mkdir()
        # Patch os.path.expanduser via monkeypatching os.path.expanduser only
        # for the specific path. Simpler: just write into the real ~ via a
        # subdirectory the helpers don't know about. The helpers all derive
        # the dir from os.path.expanduser("~/.claude/group-chats") — so
        # monkey-patch that lookup.
        return gcd

    def test_sidecar_round_trip(self):
        """_load_group_chat_sidecar / _update_group_chat_sidecar must merge
        fields atomically and survive missing files."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "demo.md")
            with open(md, "w") as fh:
                fh.write("# demo\n")
            # No sidecar yet → load returns {}
            self.assertEqual(server._load_group_chat_sidecar(md), {})
            # Update creates the sidecar
            ok = server._update_group_chat_sidecar(
                md, archived=True, archived_at=1234.5, topic="hi"
            )
            self.assertTrue(ok)
            data = server._load_group_chat_sidecar(md)
            self.assertEqual(data.get("topic"), "hi")
            self.assertIs(data.get("archived"), True)
            self.assertEqual(data.get("archived_at"), 1234.5)
            # Subsequent merge preserves prior fields
            server._update_group_chat_sidecar(md, archived=False)
            data2 = server._load_group_chat_sidecar(md)
            self.assertEqual(data2.get("topic"), "hi")
            self.assertIs(data2.get("archived"), False)

    def test_list_group_chats_backfills_uuid_identity(self):
        """Legacy path-keyed group chats should gain stable UUIDs."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            gcd = pathlib.Path(tmp) / "group-chats"
            gcd.mkdir()
            md = gcd / "demo.md"
            md.write_text("# Group Chat — Demo\n", encoding="utf-8")
            (gcd / "demo.json").write_text(json.dumps({
                "session_ids": [],
                "topic": "Demo",
                "mode": "topic",
                "name_map": {},
                "archived": False,
            }), encoding="utf-8")

            orig_expanduser = server.os.path.expanduser

            def fake_expanduser(path):
                if path == "~/.claude/group-chats":
                    return str(gcd)
                return orig_expanduser(path)

            with mock.patch.object(server.os.path, "expanduser", side_effect=fake_expanduser):
                chats = server._list_group_chats(include_archived=False)

            self.assertEqual(len(chats), 1)
            self.assertRegex(chats[0]["uuid"], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
            self.assertEqual(chats[0]["id"], chats[0]["uuid"])
            sidecar = json.loads((gcd / "demo.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["uuid"], chats[0]["uuid"])

    def test_group_chat_header_syncs_sidecar_topic_and_participants(self):
        """Reader refresh should repair stale markdown headers without
        touching the message history.
        """
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            sid = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
            md.write_text(
                "# Group Chat — empty chat\n"
                "**Started:** 2026-05-27 Wednesday 11:58:58 PDT\n"
                "**Mode:** topic\n"
                "**Participants:** `human`\n"
                "**Wake-status:**\n"
                "- (no participants)\n"
                "---\n\n"
                "## 2026-05-27 Wednesday 12:07:59 PDT — Human\n\n"
                "please sync\n",
                encoding="utf-8",
            )
            (pathlib.Path(tmp) / "chat.json").write_text(json.dumps({
                "session_ids": [sid],
                "topic": "APIFY sync",
                "mode": "topic",
                "name_map": {sid: "Agent One"},
                "include_human": True,
            }), encoding="utf-8")

            with mock.patch.object(
                server,
                "_group_chat_participant_meta",
                return_value={"is_live": False, "last_activity": 0},
            ):
                server._group_chat_update_header_if_changed(str(md), force_write=True)

            updated = md.read_text(encoding="utf-8")
            self.assertIn("# Group Chat — APIFY sync", updated)
            self.assertIn("**Participants:** `Agent One`, `human`", updated)
            self.assertIn("- `Agent One` (aaaaaaaa): offline", updated)
            self.assertIn("## 2026-05-27 Wednesday 12:07:59 PDT — Human", updated)
            self.assertIn("please sync", updated)

    def test_group_chat_header_rewrite_preserves_pre_boundary_history(self):
        """Header repair must preserve legacy text before the first separator."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            sid = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
            md.write_text(
                "# Group Chat — empty chat\n"
                "**Started:** 2026-05-27 Wednesday 11:58:58 PDT\n"
                "**Mode:** topic\n"
                "**Participants:** `human`\n"
                "**Wake-status:**\n"
                "- (no participants)\n"
                "Agent pre-history line one\n"
                "- Agent pre-history bullet\n"
                "> _2026-05-27 12:00:00 PDT — system: created chat_\n"
                "---\n\n"
                "## 2026-05-27 Wednesday 12:07:59 PDT — Human\n\n"
                "please sync\n",
                encoding="utf-8",
            )
            (pathlib.Path(tmp) / "chat.json").write_text(json.dumps({
                "session_ids": [sid],
                "topic": "APIFY sync",
                "mode": "topic",
                "name_map": {sid: "Agent One"},
                "include_human": True,
            }), encoding="utf-8")

            with mock.patch.object(
                server,
                "_group_chat_participant_meta",
                return_value={"is_live": False, "last_activity": 0},
            ):
                server._group_chat_update_header_if_changed(str(md), force_write=True)

            updated = md.read_text(encoding="utf-8")
            self.assertIn("# Group Chat — APIFY sync", updated)
            self.assertIn("Agent pre-history line one", updated)
            self.assertIn("- Agent pre-history bullet", updated)
            self.assertIn("system: created chat", updated)
            self.assertIn("## 2026-05-27 Wednesday 12:07:59 PDT — Human", updated)
            self.assertIn("please sync", updated)

    def test_group_chat_header_rewrite_preserves_system_log_without_boundary(self):
        """Creation logs can exist before any message separator is present."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            md.write_text(
                "# Group Chat — empty chat\n"
                "**Started:** 2026-05-27 Wednesday 11:58:58 PDT\n"
                "**Mode:** topic\n"
                "**Participants:** `human`\n"
                "> _2026-05-27 12:00:00 PDT — system: created empty chat_\n",
                encoding="utf-8",
            )
            (pathlib.Path(tmp) / "chat.json").write_text(json.dumps({
                "session_ids": [],
                "topic": "APIFY sync",
                "mode": "topic",
                "name_map": {},
                "include_human": True,
            }), encoding="utf-8")

            server._group_chat_update_header_if_changed(str(md), force_write=True)

            updated = md.read_text(encoding="utf-8")
            self.assertIn("**Wake-status:**", updated)
            self.assertIn("- (no participants)", updated)
            self.assertIn("system: created empty chat", updated)

    def test_group_chat_nudge_only_reminds_once_per_latest_post(self):
        """Repeated nudges for one chat turn must not flood recipients."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = pathlib.Path(tmp) / "chat.md"
            sid_a = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
            sid_b = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
            md.write_text(
                "# Group Chat - Demo\n"
                "## 2026-05-27 Wednesday 12:07:59 PDT — aaaaaaaa: Agent A\n\n"
                "I need another agent to review this.\n",
                encoding="utf-8",
            )
            (pathlib.Path(tmp) / "chat.json").write_text(json.dumps({
                "session_ids": [sid_a, sid_b],
                "topic": "Demo",
                "mode": "topic",
                "name_map": {sid_a: "Agent A", sid_b: "Agent B"},
                "include_human": True,
            }), encoding="utf-8")

            with mock.patch.object(server, "_resolve_group_chat_ref", return_value=str(md)), \
                    mock.patch.object(server, "_inject_text_into_session", return_value={"ok": True}) as inject:
                first = server._group_chat_nudge(str(md))
                second = server._group_chat_nudge(str(md))
                md.write_text(
                    md.read_text(encoding="utf-8")
                    + "## 2026-05-27 Wednesday 12:08:30 PDT — Human\n\n"
                    + "Agent A, please follow up.\n",
                    encoding="utf-8",
                )
                third = server._group_chat_nudge(str(md))

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual(second.get("skipped"), "already reminded")
            self.assertTrue(third["ok"])
            self.assertEqual(inject.call_count, 2)
            self.assertEqual(inject.call_args_list[0].args[0], sid_b)
            self.assertEqual(inject.call_args_list[1].args[0], sid_a)

    def test_message_count_counts_h2_lines(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write("# header\n## one\nbody\n## two\n## three — author\n")
            self.assertEqual(server._group_chat_message_count(md), 3)

    def test_latest_message_snapshot_uses_latest_post(self):
        """The injected wake-up hint should show the latest post only."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write(
                    "# header\n"
                    "## 2026-05-13 10:00 PDT — aaaaaaaa: ALPHA\n\n"
                    "old body\n\n"
                    "## 2026-05-13 10:01 PDT — Human\n\n"
                    "new body\n"
                    "## markdown subheading inside the message\n"
                    "more detail\n"
                    "> _2026-05-13 10:02:00 PDT — system: pinged `ALPHA`_\n"
                )
            snapshot = server._group_chat_latest_message_snapshot(md)
            self.assertIn("Human", snapshot)
            self.assertIn("new body", snapshot)
            self.assertIn("markdown subheading inside the message", snapshot)
            self.assertNotIn("old body", snapshot)
            self.assertNotIn("system: pinged", snapshot)

    def test_group_chat_inject_text_includes_latest_snapshot(self):
        """Participants get a bounded advisory snapshot in the injection."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write(
                    "# header\n"
                    "## 2026-05-13 10:01 PDT — Human\n\n"
                    "please respond\n"
                )
            text = server._group_chat_inject_text(
                md, 'topic with "quotes"', "topic", "abc12345-session"
            )
            # CCC-108: no leading "/" — slash-form only dispatches in a live
            # Claude TUI; Codex / headless Claude need an instruction.
            self.assertFalse(text.startswith("/"))
            self.assertIn("group-chat-checkin skill", text)
            self.assertIn(f'chat="{md}"', text)
            self.assertIn('topic="topic with \\"quotes\\""', text)
            self.assertIn('sid="abc12345-session"', text)
            self.assertIn("CCC pointer: a new post just landed", text)
            self.assertIn("## 2026-05-13 10:01 PDT — Human", text)

    def test_resolve_group_chat_path_rejects_outside_dir(self):
        """The path validator must clamp to ~/.claude/group-chats/ and
        reject anything outside (no path traversal)."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        # Outside the group-chats dir → ""
        self.assertEqual(server._resolve_group_chat_path("/etc/passwd"), "")
        self.assertEqual(server._resolve_group_chat_path(""), "")
        self.assertEqual(server._resolve_group_chat_path("../../../tmp/x.md"), "")


class TestTemplateGallery(unittest.TestCase):
    def test_templates_json_parses_and_has_required_shape(self):
        """The New Session modal's template gallery is driven by
        static/templates.json. Every template must carry the fields the
        UI binds to — id, name, description, engine, worktree, prompt —
        and the JSON must be valid so the gallery doesn't render blank."""
        path = pathlib.Path(PROJECT_ROOT, "static", "templates.json")
        self.assertTrue(path.is_file(), "static/templates.json missing")
        data = json.loads(path.read_text(encoding="utf-8"))
        templates = data.get("templates")
        self.assertIsInstance(templates, list)
        self.assertGreaterEqual(
            len(templates), 5,
            "issue #46 ships with at least five starter templates",
        )
        seen_ids = set()
        for t in templates:
            for key in ("id", "name", "description", "engine", "worktree", "prompt"):
                self.assertIn(key, t, f"template missing {key!r}: {t.get('id')}")
            self.assertIsInstance(t["id"], str)
            self.assertNotIn(t["id"], seen_ids, "duplicate template id")
            seen_ids.add(t["id"])
            self.assertIn(t["engine"], ("claude", "codex", "gemini"))
            self.assertIsInstance(t["worktree"], bool)
            self.assertIsInstance(t["prompt"], str)
            self.assertGreater(len(t["prompt"].strip()), 0)


class TestHealthcheck(unittest.TestCase):
    def test_healthcheck_returns_structured_result(self):
        """_run_healthcheck must always return a dict with 'checks' and
        'overall' keys, even on a fresh install with nothing configured."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        result = server._run_healthcheck()
        self.assertIn("checks", result)
        self.assertIn("overall", result)
        self.assertIn(result["overall"], ("ok", "warn", "error"))
        self.assertIsInstance(result["checks"], list)
        self.assertGreater(len(result["checks"]), 0)


class TestPendingInputs(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-pending-inputs-")
        self.server.PENDING_INPUTS_FILE = pathlib.Path(self.tmp_dir) / "pending-inputs.json"
        
        # Clear locks/queues
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def test_save_and_load_pending_inputs(self):
        sid = "test-session-id"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["hello resume"]
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue[sid] = ["hello term"]

        # Save to disk
        self.server._save_pending_inputs()
        self.assertTrue(self.server.PENDING_INPUTS_FILE.is_file())

        # Clear memory queues
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

        # Load from disk
        self.server._load_pending_inputs()

        # Verify loaded correctly
        with self.server._pending_resume_lock:
            self.assertEqual(self.server._pending_resume_queue.get(sid), ["hello resume"])
        with self.server._pending_terminal_input_lock:
            self.assertEqual(self.server._pending_terminal_input_queue.get(sid), ["hello term"])

    def test_get_queued_events_for_session(self):
        sid = "test-session-id"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["r1", "r2"]
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue[sid] = ["t1"]

        events = self.server._get_queued_events_for_session(sid)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["text"], "r1")
        self.assertTrue(events[0]["pending"])
        self.assertEqual(events[1]["text"], "r2")
        self.assertEqual(events[2]["text"], "t1")
        self.assertTrue(events[2]["pending"])

    def test_conv_bytes_cache_misses_when_pending_input_queued(self):
        """Pre-serialized /api/conversations bodies must not hide queued injects."""
        sid = "cache-pending-test-session"
        # Mock PROJECTS_ROOT to a tmp dir so the test fixture doesn't leak
        # into the user's real `~/.claude/projects` and surface as a ghost
        # session row in the live CCC UI. The previous version of this test
        # only mocked PENDING_INPUTS_FILE in setUp and used the real
        # PROJECTS_ROOT here, which left
        # `~/.claude/projects/-cache-pending/cache-pending-test-session.jsonl`
        # on disk after every run.
        tmp_projects = tempfile.mkdtemp(prefix="ccc-cache-pending-proj-")
        prev_projects_root = self.server.PROJECTS_ROOT
        self.server.PROJECTS_ROOT = pathlib.Path(tmp_projects)
        try:
            proj = self.server.PROJECTS_ROOT / "-cache-pending"
            proj.mkdir(parents=True, exist_ok=True)
            jsonl = proj / f"{sid}.jsonl"
            jsonl.write_text(
                json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n",
                encoding="utf-8",
            )
            result = self.server.parse_conversation(sid, after_line=0, use_cache=False)
            raw = json.dumps(result).encode()
            self.server._conv_response_bytes_put(sid, 0, raw, None)
            self.assertIsNotNone(self.server._conv_response_bytes_get(sid, 0))
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue[sid] = ["still waiting"]
            self.assertIsNone(self.server._conv_response_bytes_get(sid, 0))
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()
        finally:
            self.server.PROJECTS_ROOT = prev_projects_root
            shutil.rmtree(tmp_projects, ignore_errors=True)


class TestSessionUsageDedup(unittest.TestCase):
    """Claude Code's JSONL re-records the same API response (same
    `message.id`) under fresh event UUIDs whenever a session is resumed
    or forked. Cost/token totals must dedupe by `message.id` so a session
    that resumed 4 times doesn't show 4x the real cost — see issue #60."""

    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp = tempfile.mkdtemp(prefix="ccc-usage-")
        self.prev_root = self.server.PROJECTS_ROOT
        self.server.PROJECTS_ROOT = pathlib.Path(self.tmp)

    def tearDown(self):
        self.server.PROJECTS_ROOT = self.prev_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, events):
        proj = pathlib.Path(self.tmp) / "-some-project"
        proj.mkdir(parents=True, exist_ok=True)
        jsonl = proj / f"{sid}.jsonl"
        with jsonl.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        return jsonl

    def _assistant(self, uuid, msg_id, usage, model="claude-opus-4-7"):
        return {
            "type": "assistant",
            "uuid": uuid,
            "sessionId": "any",
            "message": {
                "id": msg_id,
                "role": "assistant",
                "model": model,
                "usage": usage,
                "content": [{"type": "text", "text": "ok"}],
            },
        }

    def test_duplicate_message_ids_counted_once(self):
        """Two assistant events carrying the same `message.id` come from
        one Anthropic API response replayed by a session resume — totals
        and cost must count them exactly once."""
        sid = "00000000-0000-4000-8000-000000000abc"
        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 1_000,
            "cache_read_input_tokens": 10_000,
            "output_tokens": 200,
        }
        # Same msg_id replayed 4 times under different event uuids — the
        # exact pattern observed in real ~/.claude/projects/*.jsonl files
        # after multiple resumes.
        events = [
            self._assistant(f"uuid-{i}", "msg_unique", usage)
            for i in range(4)
        ]
        # Plus one genuinely-different turn.
        other = {
            "input_tokens": 50,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 5_000,
            "output_tokens": 100,
        }
        events.append(self._assistant("uuid-other", "msg_other", other))
        self._write_session(sid, events)

        result = self.server.extract_session_usage(sid)

        # Each input bucket should be counted ONCE for msg_unique plus
        # ONCE for msg_other — not 4x + 1x = 5x.
        self.assertEqual(result["total_input_tokens"], 150)
        self.assertEqual(result["total_cache_creation_tokens"], 1_500)
        self.assertEqual(result["total_cache_read_tokens"], 15_000)
        self.assertEqual(result["total_output_tokens"], 300)

        # Current Opus 4.7 rates: 5 / 6.25 / 0.50 / 25 per Mtok.
        expected = (150 * 5 + 1_500 * 6.25
                    + 15_000 * 0.50 + 300 * 25) / 1_000_000
        self.assertAlmostEqual(result["cost_usd"], round(expected, 4), places=4)

    def test_events_without_message_id_still_summed(self):
        """Defensive: if the JSONL ever lacks `message.id` we must still
        count usage — falling back to a per-event identity rather than
        silently dropping the turn."""
        sid = "00000000-0000-4000-8000-000000000abd"
        usage = {
            "input_tokens": 10,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 20,
        }
        events = [
            {
                "type": "assistant",
                "uuid": f"u-{i}",
                "sessionId": "any",
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "usage": usage,
                    "content": [{"type": "text", "text": "ok"}],
                },
            }
            for i in range(3)
        ]
        self._write_session(sid, events)

        result = self.server.extract_session_usage(sid)
        # Three distinct events, none deduped (no shared id to dedupe by).
        self.assertEqual(result["total_input_tokens"], 30)
        self.assertEqual(result["total_output_tokens"], 60)


class TestThroughputCacheAdjusted(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp = tempfile.mkdtemp(prefix="ccc-throughput-")
        self.prev_root = self.server.PROJECTS_ROOT
        self.server.PROJECTS_ROOT = pathlib.Path(self.tmp)
        self.server._ENGINE_DETECT_CACHE.clear()

    def tearDown(self):
        self.server.PROJECTS_ROOT = self.prev_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_session(self, sid, events):
        proj = pathlib.Path(self.tmp) / "-throughput-project"
        proj.mkdir(parents=True, exist_ok=True)
        path = proj / f"{sid}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        return path

    def test_claude_throughput_uses_cache_adjusted_input(self):
        sid = "00000000-0000-4000-8000-000000000abe"
        events = [
            {
                "type": "user",
                "timestamp": "2026-06-12T17:00:00.000Z",
                "sessionId": sid,
                "message": {"role": "user", "content": "measure this"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-06-12T17:00:30.000Z",
                "sessionId": sid,
                "message": {
                    "id": "msg-throughput-1",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "done"}],
                    "usage": {
                        "input_tokens": 1_000,
                        "cache_creation_input_tokens": 200,
                        "cache_read_input_tokens": 5_000,
                        "output_tokens": 300,
                    },
                },
            },
        ]
        self._write_session(sid, events)

        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "_is_cursor_session", return_value=False), \
             mock.patch.object(self.server, "_is_antigravity_session", return_value=False), \
             mock.patch.object(self.server, "_is_kilo_session", return_value=False), \
             mock.patch.object(self.server, "_load_desktop_app_metadata", return_value={}):
            payload, status = self.server._throughput_payload(sid)

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["turns"]), 1)
        turn = payload["turns"][0]
        self.assertEqual(turn["tokens_in"], 6_200)
        self.assertEqual(turn["fresh_input_tokens"], 1_000)
        self.assertEqual(turn["cache_write_tokens"], 200)
        self.assertEqual(turn["cache_read_tokens"], 5_000)
        # Sonnet cache math: 1000 + 200*1.25 + 5000*0.10.
        self.assertEqual(turn["effective_input_tokens"], 1_750)
        self.assertEqual(turn["effective_input_tpm"], 3_500)

        summary = payload["summary"]
        self.assertEqual(summary["total_raw_context_tokens"], 6_200)
        self.assertEqual(summary["total_effective_input_tokens"], 1_750)
        self.assertEqual(summary["avg_input_tpm"], 12_400)
        self.assertEqual(summary["avg_effective_input_tpm"], 3_500)
        self.assertAlmostEqual(summary["cache_hit_ratio"], 5_000 / 6_200, places=4)
        self.assertGreater(summary["cost_usd"], 0)

    def test_codex_cached_input_is_subset_of_input_tokens(self):
        usage = self.server._throughput_normalize_usage(
            {
                "input_tokens": 1_000,
                "cached_input_tokens": 800,
                "output_tokens": 50,
            },
            engine="codex",
            model="gpt-5.5",
        )

        self.assertEqual(usage["raw_context_tokens"], 1_000)
        self.assertEqual(usage["fresh_input_tokens"], 200)
        self.assertEqual(usage["cache_read_tokens"], 800)
        self.assertEqual(usage["effective_input_tokens"], 280)
        self.assertTrue(usage["cost_available"])
        self.assertGreater(usage["cost_usd"], 0)

    def test_claude_cache_creation_duration_changes_effective_burn(self):
        usage = self.server._throughput_normalize_usage(
            {
                "input_tokens": 100,
                "cache_creation_input_tokens": 300,
                "cache_creation_5m_input_tokens": 100,
                "cache_creation_1h_input_tokens": 200,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
            engine="claude",
            model="claude-sonnet-4-6",
        )

        self.assertEqual(usage["cache_write_tokens"], 300)
        self.assertEqual(usage["cache_write_5m_tokens"], 100)
        self.assertEqual(usage["cache_write_1h_tokens"], 200)
        # 5m cache writes use 1.25x input; 1h cache writes use 2.0x input.
        self.assertEqual(usage["effective_input_tokens"], 625)
        expected_cost = (100 * 3 + 100 * 3.75 + 200 * 6) / 1_000_000
        self.assertAlmostEqual(usage["cost_usd"], expected_cost)

    def test_codex_throughput_uses_each_token_count_event(self):
        sid = "codex-throughput-session"
        path = pathlib.Path(self.tmp) / "rollout-codex.jsonl"
        events = [
            {
                "type": "turn_context",
                "timestamp": "2026-06-12T17:00:00.000Z",
                "payload": {"model": "gpt-5.5"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-12T17:00:01.000Z",
                "payload": {"type": "user_message", "message": "measure codex"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-12T17:00:11.000Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1_000,
                            "cached_input_tokens": 800,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 10,
                            "total_tokens": 1_050,
                        },
                        "total_token_usage": {
                            "input_tokens": 1_000,
                            "cached_input_tokens": 800,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 10,
                            "total_tokens": 1_050,
                        },
                    },
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-06-12T17:00:21.000Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 2_000,
                            "cached_input_tokens": 1_900,
                            "output_tokens": 100,
                            "reasoning_output_tokens": 0,
                            "total_tokens": 2_100,
                        },
                        "total_token_usage": {
                            "input_tokens": 3_000,
                            "cached_input_tokens": 2_700,
                            "output_tokens": 150,
                            "reasoning_output_tokens": 10,
                            "total_tokens": 3_150,
                        },
                    },
                },
            },
        ]
        with path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        with mock.patch.object(self.server, "_resolve_codex_rollout_path", return_value=path):
            turns = self.server._throughput_codex_turns_from_file(sid, model_hint="gpt-5.5")

        self.assertEqual(len(turns), 2)
        summary = self.server._throughput_summary(turns)
        self.assertEqual(summary["total_raw_context_tokens"], 3_000)
        self.assertEqual(summary["total_fresh_input_tokens"], 300)
        self.assertEqual(summary["total_cache_read_tokens"], 2_700)
        self.assertEqual(summary["total_output_tokens"], 160)
        self.assertEqual(summary["total_effective_input_tokens"], 570)
        self.assertGreater(summary["cost_usd"], 0)

    def test_claude_throughput_dedupes_message_snapshots(self):
        sid = "00000000-0000-4000-8000-000000000abf"
        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 1_000,
            "output_tokens": 50,
        }
        events = [
            {
                "type": "user",
                "timestamp": "2026-06-12T17:00:00.000Z",
                "sessionId": sid,
                "message": {"role": "user", "content": "fan out"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-06-12T17:00:05.000Z",
                "sessionId": sid,
                "requestId": "req-throughput-1",
                "message": {
                    "id": "msg-throughput-dup",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "thinking"}],
                    "usage": usage,
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-06-12T17:00:20.000Z",
                "sessionId": sid,
                "requestId": "req-throughput-1",
                "message": {
                    "id": "msg-throughput-dup",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "README.md"}}],
                    "usage": usage,
                },
            },
        ]
        self._write_session(sid, events)

        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "_is_cursor_session", return_value=False), \
             mock.patch.object(self.server, "_is_antigravity_session", return_value=False), \
             mock.patch.object(self.server, "_is_kilo_session", return_value=False), \
             mock.patch.object(self.server, "_load_desktop_app_metadata", return_value={}):
            payload, status = self.server._throughput_payload(sid)

        self.assertEqual(status, 200)
        self.assertEqual(len(payload["turns"]), 1)
        self.assertEqual(payload["turns"][0]["message_id"], "msg-throughput-dup")
        self.assertEqual(payload["turns"][0]["request_id"], "req-throughput-1")
        self.assertEqual(payload["turns"][0]["dur_sec"], 20)
        self.assertEqual(payload["summary"]["total_raw_context_tokens"], 1_120)
        self.assertEqual(payload["summary"]["total_output_tokens"], 50)


class TestCodexEsc(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def test_interrupt_codex_session_sends_sigint(self):
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(self.server, "session_live_status") as mock_status, \
             mock.patch.object(self.server.os, "kill") as mock_kill:

            mock_status.return_value = {
                "live": True,
                "pid": 12345,
                "tty": None,
                "terminal_app": None,
            }

            res = self.server._interrupt_session("some-codex-session-id")
            self.assertTrue(res["ok"])
            self.assertEqual(res["via"], "spawn-sigint")
            self.assertEqual(res["pid"], 12345)
            mock_kill.assert_called_once_with(12345, self.server.signal.SIGINT)

    def test_interrupt_non_live_codex_session(self):
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(self.server, "session_live_status") as mock_status:

            mock_status.return_value = {
                "live": False,
                "pid": None,
                "tty": None,
                "terminal_app": None,
            }

            res = self.server._interrupt_session("some-codex-session-id")
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "Codex session is not live — nothing to interrupt")

    def test_codex_liveness_fallback_to_spawned_sessions(self):
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "find_session_cwd", return_value="/tmp"), \
             mock.patch.object(self.server, "_resolve_codex_rollout_path", return_value=None), \
             mock.patch.object(self.server, "_spawn_registry_has_session", return_value=False), \
             mock.patch.object(self.server, "_live_spawn_registry_entry_for_session", return_value=None), \
             mock.patch.object(self.server, "_find_live_spawn_entry_for_session") as mock_find_spawn, \
             mock.patch.object(self.server, "_process_tty", return_value=None), \
             mock.patch.object(self.server, "_proc_cwd", return_value="/tmp"), \
             mock.patch.object(self.server, "_proc_ancestor_terminal", return_value=(None, None)):

            mock_find_spawn.return_value = {
                "pid": 12345,
                "engine": "codex",
                "cwd": "/tmp",
            }

            res = self.server.session_live_status("some-codex-session-id", "/tmp")
            self.assertTrue(res["live"])
            self.assertEqual(res["pid"], 12345)
            mock_find_spawn.assert_called_once_with("some-codex-session-id")

    def test_live_engine_session_ids_includes_memory_spawns(self):
        fake_spawn = {
            "pid": 12345,
            "engine": "codex",
            "log": "/tmp/spawn-codex-foo.log",
            "session_id": None,
        }
        with mock.patch.object(self.server, "_spawned_sessions", [fake_spawn]), \
             mock.patch.object(self.server, "_poll_spawn_entry", return_value=None), \
             mock.patch.object(self.server, "_extract_codex_thread_id_from_log", return_value="dynamic-codex-sid"), \
             mock.patch.object(self.server, "find_live_codex_processes", return_value=[]), \
             mock.patch.object(self.server, "find_live_gemini_processes", return_value=[]), \
             mock.patch.object(self.server, "find_live_cursor_processes", return_value=[]):
            
            self.server._engine_live_sids_cache = {"ts": 0.0, "sids": frozenset()}
            sids = self.server._live_engine_session_ids()
            self.assertIn("dynamic-codex-sid", sids)


class TestQuestionRelay(unittest.TestCase):
    """AskUserQuestion relay: dashboard answers a blocked headless session."""

    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="ccc-question-relay-home-")
        self._prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(pathlib.Path(self.tmp_home).resolve())
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._prev_home
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def _write_request(self, sid, nonce="N1"):
        self.server.QUESTION_RELAY_DIR.mkdir(parents=True, exist_ok=True)
        (self.server.QUESTION_RELAY_DIR / f"{sid}.request.json").write_text(json.dumps({
            "nonce": nonce,
            "session_id": sid,
            "questions": [{
                "header": "Color",
                "question": "Pick a color",
                "multiSelect": False,
                "options": [{"label": "Red", "description": ""},
                            {"label": "Blue", "description": ""}],
            }],
        }))

    def test_relay_env_opts_in(self):
        env = self.server._question_relay_env()
        self.assertEqual(env.get(self.server.QUESTION_RELAY_ENV), "1")

    def test_read_request_none_when_absent(self):
        self.assertIsNone(self.server._read_question_request("missing-sid"))

    def test_answer_roundtrip_indexed(self):
        sid = "relay-sid-1"
        self._write_request(sid, nonce="abc")
        req = self.server._read_question_request(sid)
        self.assertEqual(req["nonce"], "abc")

        result = self.server._write_question_answer(sid, [{"index": 1, "text": ""}])
        self.assertTrue(result["ok"])
        ans = json.loads(
            (self.server.QUESTION_RELAY_DIR / f"{sid}.answer.json").read_text()
        )
        self.assertEqual(ans["nonce"], "abc")
        self.assertEqual(ans["answers"], [{"index": 1, "text": ""}])

    def test_answer_without_pending_question_fails(self):
        result = self.server._write_question_answer("no-such-sid", [{"index": 0}])
        self.assertFalse(result["ok"])
        self.assertIn("no pending", result["error"])

    def test_answer_rejects_empty_list(self):
        sid = "relay-sid-2"
        self._write_request(sid)
        self.assertFalse(self.server._write_question_answer(sid, [])["ok"])

    def test_hook_process_not_counted_as_active_tool_child(self):
        # Regression: the blocking PreToolUse hook is a child process of the
        # spawn; if treated as a running "Bash" tool it clobbers the
        # AskUserQuestion sidecar and suppresses the answer modal.
        self.assertTrue(self.server._is_ccc_hook_command(
            "python3 /Users/x/.claude/command-center/hooks/pre-tool-use.py"))
        self.assertFalse(self.server._is_ccc_hook_command("bash -c 'npm test'"))


class TestQuestionRelayHook(unittest.TestCase):
    """The PreToolUse hook's answer-rendering logic (hooks/pre-tool-use.py)."""

    def setUp(self):
        import importlib.util
        repo_root = pathlib.Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "ccc_pre_tool_use_hook", str(repo_root / "hooks" / "pre-tool-use.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.hook = mod

    def test_full_questions_preserves_all(self):
        qs = self.hook.full_questions({"questions": [
            {"header": "A", "question": "q1", "options": [{"label": "x"}]},
            {"header": "B", "question": "q2", "options": [{"label": "y"}, {"label": "z"}]},
        ]})
        self.assertEqual(len(qs), 2)
        self.assertEqual(qs[1]["options"][1]["label"], "z")

    def test_build_reason_maps_index_to_label(self):
        questions = [{"question": "Pick a color",
                      "options": [{"label": "Red"}, {"label": "Blue"}]}]
        reason = self.hook.build_answer_reason(questions, [{"index": 1, "text": ""}])
        self.assertIn('"Pick a color" = "Blue"', reason)
        self.assertIn("do not ask again", reason)

    def test_build_reason_uses_free_text_when_no_index(self):
        questions = [{"question": "Pick a color",
                      "options": [{"label": "Red"}, {"label": "Blue"}]}]
        reason = self.hook.build_answer_reason(questions, [{"index": -1, "text": "Teal"}])
        self.assertIn('"Pick a color" = "Teal"', reason)

    def test_build_reason_multi_question_plural(self):
        questions = [
            {"question": "q1", "options": [{"label": "a"}]},
            {"question": "q2", "options": [{"label": "b"}]},
        ]
        reason = self.hook.build_answer_reason(
            questions, [{"index": 0, "text": ""}, {"index": 0, "text": ""}]
        )
        self.assertIn("answered the questions", reason)
        self.assertIn("these answers", reason)


class TestCodexStateWiring(unittest.TestCase):
    def test_server_exposes_codex_state_helpers(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        for name in ("_codex_row_state", "_codex_state_fields", "_codex_pool_alive"):
            self.assertTrue(hasattr(server, name), name)

    def test_static_renders_codex_state(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        js = (root / "static" / "app.js").read_text()
        css = (root / "static" / "app.css").read_text()
        self.assertIn("codex_state", js)
        self.assertIn("updateCodexStateBadge", js)
        self.assertIn("flow-chip.offline", css)
        self.assertIn("conv-codex-state", css)


class TestSpawnReturnAddress(unittest.TestCase):
    """The 'return address' lets a spawned session report back to its
    dispatcher on completion. See /api/sessions/spawn report_to field."""

    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")

    def test_normalize_accepts_canonical_and_aliases(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        for key in ("report_to", "return_to", "reply_to"):
            val, err = self.server._normalize_return_address({key: sid})
            self.assertIsNone(err, key)
            self.assertEqual(val, sid, key)

    def test_normalize_none_when_absent(self):
        self.assertEqual(self.server._normalize_return_address({}), (None, None))

    def test_normalize_rejects_shell_metachars(self):
        val, err = self.server._normalize_return_address({"report_to": "x; rm -rf /"})
        self.assertIsNone(val)
        self.assertTrue(err)

    def test_normalize_rejects_too_short(self):
        val, err = self.server._normalize_return_address({"report_to": "abc"})
        self.assertIsNone(val)
        self.assertTrue(err)

    def test_wrap_is_noop_without_address(self):
        self.assertEqual(
            self.server._wrap_prompt_with_return_address("do x", None), "do x"
        )

    def test_wrap_embeds_address_and_inject_api(self):
        sid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        out = self.server._wrap_prompt_with_return_address("do x", sid, port=8090)
        self.assertIn("do x", out)
        self.assertIn(sid, out)
        self.assertIn("/api/inject-input", out)
        self.assertIn("STATUS", out)


if __name__ == "__main__":
    unittest.main()
