"""Regression checks for the compact Queue panel header."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueHeaderLayout(unittest.TestCase):
    def test_queue_search_shares_the_header_row_and_scope_is_prominent(self):
        """The queue trigger and search stay visible without a third toolbar row.

        The native <select> is gone (replaced by #queueScopeTrigger pill). The
        trigger lives in the field row (.fq-field-row); the search input moved
        to the ticket-filter row below the WORKING NOW strip. Both are visible
        without a third toolbar row.
        """
        index_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app_css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")

        queue_start = index_html.index('id="queuePanel"')
        header_start = index_html.index('class="files-header fq-field-row"', queue_start)
        header_end = index_html.index('<div class="files-queue-list"', header_start)
        header = index_html[header_start:header_end]

        # The trigger replaced the native <select>.
        self.assertIn('id="queueScopeTrigger"', header)
        self.assertNotIn('id="queueScopeSelect"', header)
        # The search input is now in the ticket-filter row, which sits between
        # the field row and the ticket list (still inside the panel header area,
        # before the ticket list).
        self.assertIn('id="queueSearchInput"', header)
        self.assertNotIn('<div class="fq-search-row">', header)

        # Trigger pill CSS — the new design's values (mono 13px, weight 600,
        # #161b22 fill, #2b323b border, 6px radius).
        trigger_css = app_css[
            app_css.index('.fq-trigger {'):
            app_css.index('.fq-trigger:hover', app_css.index('.fq-trigger {'))
        ]
        self.assertIn('flex: 1 1 auto', trigger_css)
        self.assertIn('background: #161b22', trigger_css)
        self.assertIn('border: 1px solid #2b323b', trigger_css)
        self.assertIn('border-radius: 6px', trigger_css)

        # The field row does not wrap — the trigger truncates, not the controls.
        self.assertIn('.files-queue-panel .fq-field-row {', app_css)

    def test_secondary_queue_actions_live_in_the_more_menu(self):
        """Board, import, and wrapping should not crowd the Queue header.

        The queue-first board was removed entirely, so nothing populates the
        leftover #queueBoardMenuSlot placeholder any more. The remaining
        secondary actions (import doc, wrap toggle) live in the "more" menu
        and dismiss it via _closeQueueMoreMenu() after use.
        """
        index_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        queue_start = index_html.index('id="queuePanel"')
        header_end = index_html.index('<div class="files-queue-list"', queue_start)
        header = index_html[queue_start:header_end]

        more_start = header.index('id="queueMoreBtn"')
        more_menu = header[more_start:]
        self.assertIn('id="queueMoreMenu"', more_menu)
        self.assertIn('id="queueBoardMenuSlot"', more_menu)
        self.assertIn('id="queueImportDoc"', more_menu)
        self.assertIn('id="queueWrapToggle"', more_menu)

        # The old queue-first chrome (_qfEnsureChrome/_qfInit) is gone and
        # nothing fills the board slot from app.js any more.
        self.assertNotIn('function _qfEnsureChrome(', app_js)
        self.assertNotIn("getElementById('queueBoardMenuSlot')", app_js)

        # The more-menu open/close chrome still exists.
        more = app_js[
            app_js.index("const $queueMoreBtn = document.getElementById('queueMoreBtn');"):
            app_js.index('function openQueueTicketComposer()', app_js.index("const $queueMoreBtn = document.getElementById('queueMoreBtn');"))
        ]
        self.assertIn('function _closeQueueMoreMenu()', more)
        self.assertIn("$queueMoreMenu.classList.add('open');", more)

        # Both remaining secondary actions dismiss the more menu after use.
        import_block = app_js[
            app_js.index("const $queueImport = document.getElementById('queueImportDoc');"):
            app_js.index("// Status filter toggle (All | Open).", app_js.index("const $queueImport = document.getElementById('queueImportDoc');"))
        ]
        self.assertIn('_closeQueueMoreMenu();', import_block)
        wrap_block = app_js[
            app_js.index("const queueWrapToggle = document.getElementById('queueWrapToggle');"):
            app_js.index("const $queueSearch = document.getElementById('queueSearchInput');", app_js.index("const queueWrapToggle = document.getElementById('queueWrapToggle');"))
        ]
        self.assertIn('_closeQueueMoreMenu();', wrap_block)

    def test_scope_picker_nests_sub_queues_under_a_derived_family_root(self):
        """"FOO-BAR" queues group under "FOO" instead of stacking flat.

        The native <optgroup> is gone (replaced by the custom picker card).
        Sub-queue nesting is now handled by _uxqPickerParentOf, which resolves
        a sub-queue to the LONGEST existing root queue whose name is a prefix.
        The ALL QUEUES group renders children with a └ glyph immediately
        under their parent; NEEDS YOU / RECENT / filtered results stay flat.
        """
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        # Roots are derived from the live queue names, never hardcoded.
        derive = app_js[
            app_js.index('function _uxqRefreshFamilyRoots('):
            app_js.index('function _projectForRepoPath(')
        ]
        self.assertIn('_UXQ_FAMILY_ROOTS.clear()', derive)
        self.assertIn('count >= 2 || names.has(cand)', derive)
        self.assertIn('_uxqHealthCache', derive)

        # The available-scopes pass must NOT collapse children away — the
        # picker still needs them to build its groups.
        scopes = app_js[
            app_js.index('function _uxqAvailableScopes('):
            app_js.index('function _uxqRenderScopeSelect(')
        ]
        self.assertNotIn('_UXQ_FAMILY_ROOTS', scopes)

        # The new picker resolves parents via _uxqPickerParentOf (longest
        # root prefix), not the old <optgroup> nesting.
        self.assertIn('function _uxqPickerParentOf(', app_js)
        self.assertIn("n.startsWith(root + '-')", app_js)

        # The picker groups render the └ glyph for sub-queues in ALL QUEUES.
        render = app_js[
            app_js.index('function _uxqRenderScopeSelect('):
            app_js.index('function _uxqSetScopeLoading(')
        ]
        self.assertIn('_uxqRenderQueueTrigger', render)
        self.assertIn('_uxqRenderWorkingNow', render)

        # The picker row builder emits the └ tree glyph for children.
        self.assertIn("r.child ? '<span class=\"fq-qp-tree\">└</span>'", app_js)

        # Families must be recomputed before anything asks whether a sub-queue
        # rolls up, otherwise the first paint renders a flat list.
        panel_start = app_js.index('function _renderQueuePanel(')
        panel = app_js[
            panel_start:
            app_js.index('_uxqResolvePanelProject(items, requestedProject)', panel_start)
        ]
        self.assertIn('_uxqRefreshFamilyRoots(items)', panel)
