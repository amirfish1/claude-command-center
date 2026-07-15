import pathlib
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueuePanelLayout(unittest.TestCase):
    def test_main_sidebar_replaces_merge_with_shared_queues_tab(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        tab_block = app_js[
            app_js.index("const _sidebarTab = (() => {"):
            app_js.index("const _tabEmpty =", app_js.index("const _sidebarTab = (() => {"))
        ]
        self.assertIn("t === 'queues'", tab_block)
        self.assertNotIn("t === 'merge'", tab_block)
        self.assertIn("['queues', 'Queues'", tab_block)
        self.assertNotIn("['merge', 'Merge'", tab_block)
        self.assertIn('id="sidebarQueueHost"', app_js)
        self.assertNotIn("_sidebarTab === 'merge'", tab_block)

    def test_queue_panel_has_one_node_and_two_mount_points(self):
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertEqual(index_html.count('id="queuePanel"'), 1)
        self.assertEqual(index_html.count('id="statusRailQueueHost"'), 1)
        self.assertIn('id="sidebarQueueHost"', app_js)
        self.assertIn("function _setSharedQueuePanelHost(hostName)", app_js)
        self.assertIn("function _parkSharedQueuePanelForSidebarRender()", app_js)
        self.assertIn("function _mountSharedQueuePanel()", app_js)
        self.assertIn(
            "_parkSharedQueuePanelForSidebarRender();\n    $convList.innerHTML = _convListHtml;",
            app_js,
        )
        self.assertIn(
            "$convList.innerHTML = _convListHtml;\n    _mountSharedQueuePanel();",
            app_js,
        )
        self.assertIn("if (next === 'queue' && queuePane) {", app_js)
        self.assertIn("_setSharedQueuePanelHost('rail');", app_js)

    def test_queue_splitter_exposes_watchtower_log_without_starting_a_drag(self):
        """The Queue tab should expose the existing activity log at its section boundary."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        health_pos = index_html.index('id="queueHealthStrip"')
        log_pos = index_html.index('id="queueHealthLogBtn"')
        tickets_pos = index_html.index('<div class="files-header">', log_pos)
        self.assertLess(health_pos, log_pos)
        self.assertLess(log_pos, tickets_pos)
        self.assertIn('data-role="evergreen-log-btn"', index_html[health_pos:tickets_pos])

        resize_js = app_js[
            app_js.index("const $health = document.getElementById('queueHealthStrip');"):
            app_js.index("function relativeTime(ts)")
        ]
        self.assertIn("const $logBtn = document.getElementById('queueHealthLogBtn');", resize_js)
        self.assertIn("if (e.target.closest('[data-role=\"evergreen-log-btn\"]')) return;", resize_js)
        self.assertIn("if (document.getElementById('wtLogPanel')) _closeWtLogPanel();", resize_js)
        self.assertIn("else _openWtLogPanel();", resize_js)

    def test_shared_queue_host_can_shrink_to_the_status_rail(self):
        """Long queue rows must not expand the fixed-width status rail."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        rail_host_css = app_css[
            app_css.index(".shared-queue-host-rail {"):
            app_css.index("body.status-pos-right .shared-queue-host-rail > .files-queue-panel", app_css.index(".shared-queue-host-rail {"))
        ]
        rail_panel_css = app_css[
            app_css.index("body.status-pos-right .shared-queue-host-rail > .files-queue-panel {"):
            app_css.index("body.status-pos-right .status-rail-pane > .files-panel .files-resize-handle", app_css.index("body.status-pos-right .shared-queue-host-rail > .files-queue-panel {"))
        ]

        self.assertIn("min-width: 0;", rail_host_css)
        self.assertIn("min-width: 0;", rail_panel_css)

    def test_shared_queue_host_can_shrink_to_the_sidebar_tab(self):
        """Long queue rows must not expand the user-resized sidebar tab."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        sidebar_host_css = app_css[
            app_css.index(".shared-queue-host-sidebar {"):
            app_css.index(".shared-queue-host-sidebar > .files-queue-panel", app_css.index(".shared-queue-host-sidebar {"))
        ]
        sidebar_panel_css = app_css[
            app_css.index(".shared-queue-host-sidebar > .files-queue-panel {"):
            app_css.index(".conv-all-hermes-tabs", app_css.index(".shared-queue-host-sidebar > .files-queue-panel {"))
        ]

        self.assertIn("min-width: 0;", sidebar_host_css)
        self.assertIn("min-width: 0;", sidebar_panel_css)

    def test_queue_panel_note_text_expands_with_rail_width(self):
        """Queue rows should not pre-truncate notes before CSS can size them."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        queue_js = app_js[
            app_js.index("function _renderQueuePanel()"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel()"))
        ]
        self.assertNotIn("noteFull.length > 30", queue_js)
        self.assertIn("+ '<span class=\"fq-note\">' + escapeHtml(noteShown) + '</span>'", queue_js)

        note_css = app_css[
            app_css.index(".fq-note {"):
            app_css.index(".fq-status {", app_css.index(".fq-note {"))
        ]
        self.assertIn("flex: 1 1 0;", note_css)
        self.assertIn("min-width: 0;", note_css)
        self.assertIn("overflow: hidden;", note_css)
        self.assertIn("text-overflow: ellipsis;", note_css)
        self.assertIn("white-space: nowrap;", note_css)

    def test_queue_panel_empty_state_explains_project_scope(self):
        """An empty scoped Queue tab should say why it is empty."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        queue_js = app_js[
            app_js.index("function _renderQueuePanel()"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel()"))
        ]
        self.assertIn("function _uxqEmptyHtml(project, totalCount)", app_js)
        self.assertIn("No tickets for ' + escapeHtml(project)", app_js)
        self.assertIn("' tickets in other projects.'", app_js)
        self.assertIn("_uxqEmptyHtml(proj, items.length)", queue_js)
        self.assertNotIn("Queue is empty.</div>", queue_js)
        self.assertIn(".fq-empty-sub", app_css)

    def test_claimed_queue_items_do_not_render_as_open_green(self):
        """Claim metadata should force the row out of the open/green state."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        queue_js = app_js[
            app_js.index("function _renderQueuePanel()"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel()"))
        ]

        self.assertIn("const _effectiveStatus = it =>", queue_js)
        self.assertIn("it.claimed_by || it.claimed_at || it.claimed_session_id", queue_js)
        self.assertIn("const status = _effectiveStatus(it);", queue_js)
        self.assertIn("const rawStatus = it.status || 'open';", queue_js)
        self.assertNotIn("const status = it.status || 'open';", queue_js)

    def test_queue_panel_can_filter_tickets_by_type(self):
        """Type controls keep bugs and features independently scannable."""
        index_html = pathlib.Path(PROJECT_ROOT, "static", "index.html").read_text(encoding="utf-8")
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        self.assertIn('id="queueTypeFilterToggle"', index_html)
        self.assertIn('data-uxq-type-filter="all"', index_html)
        self.assertIn('data-uxq-type-filter="bug"', index_html)
        self.assertIn('data-uxq-type-filter="feature"', index_html)
        self.assertIn("const _UXQ_TYPE_FILTER_LS = 'ccc-uxq-type-filter';", app_js)
        self.assertIn("function _uxqGetTypeFilter()", app_js)
        self.assertIn("typeScoped = _uxqGetTypeFilter() === 'all'", app_js)
        self.assertIn("[data-uxq-type-filter]", app_js)

    def test_right_rail_queue_items_use_larger_type(self):
        """Queue ticket rows in the right rail should be readable at a glance."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        row_css = app_css[
            app_css.index(".fq-row {"):
            app_css.index(".fq-row:hover", app_css.index(".fq-row {"))
        ]
        ref_css = app_css[
            app_css.index(".fq-ref {"):
            app_css.index(".fq-note {", app_css.index(".fq-ref {"))
        ]
        status_css = app_css[
            app_css.index(".fq-status {"):
            app_css.index(".fq-row.is-open", app_css.index(".fq-status {"))
        ]

        empty_css = app_css[
            app_css.index(".fq-empty {"):
            app_css.index(".fq-empty-sub {", app_css.index(".fq-empty {"))
        ]
        empty_sub_css = app_css[
            app_css.index(".fq-empty-sub {"):
            app_css.index("/* Draggable object-group", app_css.index(".fq-empty-sub {"))
        ]

        self.assertIn("font-size: 14px;", row_css)
        self.assertIn("line-height: 1.35;", row_css)
        self.assertIn("font-size: 12.5px;", ref_css)
        self.assertIn("font-size: 0;", status_css)
        self.assertIn("font-size: 13px;", empty_css)
        self.assertIn("font-size: 12px;", empty_sub_css)


if __name__ == "__main__":
    unittest.main()
