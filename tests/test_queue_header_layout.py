"""Regression checks for the compact Queue panel header."""

import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueueHeaderLayout(unittest.TestCase):
    def test_queue_search_shares_the_header_row_and_scope_is_prominent(self):
        """The queue selector and search stay visible without a third toolbar row."""
        index_html = (PROJECT_ROOT / "static" / "index.html").read_text(encoding="utf-8")
        app_css = (PROJECT_ROOT / "static" / "app.css").read_text(encoding="utf-8")

        queue_start = index_html.index('id="queuePanel"')
        header_start = index_html.index('<div class="files-header">', queue_start)
        header_end = index_html.index('<div class="files-queue-list"', header_start)
        header = index_html[header_start:header_end]

        self.assertIn('id="queueScopeSelect"', header)
        self.assertIn('id="queueSearchInput"', header)
        self.assertNotIn('<div class="fq-search-row">', header)

        scope_css = app_css[
            app_css.index('.fq-scope-select {'):
            app_css.index('.fq-scope-select:hover', app_css.index('.fq-scope-select {'))
        ]
        self.assertIn('font-size: 14px;', scope_css)
        self.assertIn('font-weight: 800;', scope_css)
        self.assertIn('max-width: 220px;', scope_css)
        self.assertIn('color: var(--text);', scope_css)

        self.assertIn(
            '.files-queue-panel .files-header { flex-wrap: wrap; gap: 4px; }',
            app_css,
        )
        self.assertIn(
            'flex: 1 1 80px; min-width: 80px; max-width: none; margin-left: 0;',
            app_css,
        )

    def test_secondary_queue_actions_live_in_the_more_menu(self):
        """Board, import, and wrapping should not crowd the Queue header."""
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

        chrome = app_js[
            app_js.index('function _qfEnsureChrome()'):
            app_js.index('function _qfInit()', app_js.index('function _qfEnsureChrome()'))
        ]
        self.assertIn("document.getElementById('queueBoardMenuSlot')", chrome)

    def test_scope_picker_nests_sub_queues_under_a_derived_family_root(self):
        """"FOO-BAR" queues group under "FOO" instead of stacking flat."""
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        # Roots are derived from the live queue names, never hardcoded.
        derive = app_js[
            app_js.index('function _uxqRefreshFamilyRoots('):
            app_js.index('function _projectForRepoPath(')
        ]
        self.assertIn('_UXQ_FAMILY_ROOTS.clear()', derive)
        self.assertIn('count >= 2 || names.has(cand)', derive)
        self.assertIn('_uxqHealthCache', derive)

        # The available-scopes pass must NOT collapse children away — the picker
        # needs them to build the <optgroup>.
        scopes = app_js[
            app_js.index('function _uxqAvailableScopes('):
            app_js.index('function _uxqRenderScopeSelect(')
        ]
        self.assertNotIn('_UXQ_FAMILY_ROOTS', scopes)

        render = app_js[
            app_js.index('function _uxqRenderScopeSelect('):
            app_js.index('function _uxqSetScopeLoading(')
        ]
        self.assertIn('<optgroup label="', render)
        self.assertIn("'All ' + name + ' (+'", render)
        self.assertIn('_uxqFamilyCandidate(s)', render)

        # Families must be recomputed before anything asks whether a sub-queue
        # rolls up, otherwise the first paint renders a flat list.
        panel_start = app_js.index('function _renderQueuePanel(')
        panel = app_js[
            panel_start:
            app_js.index('_uxqResolvePanelProject(items, requestedProject)', panel_start)
        ]
        self.assertIn('_uxqRefreshFamilyRoots(items)', panel)
