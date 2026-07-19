import json
import pathlib
import subprocess
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
            app_js.index("function _renderQueuePanel(options)"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel(options)"))
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

    def test_queue_rows_compact_metadata_and_move_play_to_status_dot(self):
        """Issue names keep room by compacting row-only metadata and actions."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        queue_js = app_js[
            app_js.index("function _renderQueuePanel(options)"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel(options)"))
        ]

        self.assertIn("const _typeShort = { 'feature': 'FR', 'bug': 'BUG' };", queue_js)
        self.assertIn("typeLabel + '/' + it.priority", queue_js)
        self.assertIn("timeAgo(ageMs).replace(/\\s+ago$/, '')", queue_js)
        self.assertIn('class="fq-status fq-status-action fq-run"', queue_js)
        self.assertIn('class="fq-status fq-status-action fq-run-once"', queue_js)
        self.assertIn(".fq-status-action:hover", app_css)

    def test_queue_panel_empty_state_explains_project_scope(self):
        """An empty scoped Queue tab should say why it is empty."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        queue_js = app_js[
            app_js.index("function _renderQueuePanel(options)"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel(options)"))
        ]
        self.assertIn("function _uxqEmptyHtml(project, totalCount)", app_js)
        self.assertIn("No tickets for ' + escapeHtml(project)", app_js)
        self.assertIn("' tickets in other projects.'", app_js)
        self.assertIn("_uxqEmptyHtml(proj, items.length)", queue_js)
        self.assertNotIn("Queue is empty.</div>", queue_js)
        self.assertIn(".fq-empty-sub", app_css)

    def test_auto_queue_scope_names_the_repo_selected_queue(self):
        """Auto scope remains explicit about the queue it resolves to."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        scope_render = app_js[
            app_js.index("function _uxqRenderScopeSelect(items, currentScope)"):
            app_js.index("function _uxqEmptyHtml", app_js.index("function _uxqRenderScopeSelect(items, currentScope)"))
        ]

        self.assertIn("Auto: ' + escapeHtml(currentScope || 'all')", scope_render)

    def test_claimed_queue_items_do_not_render_as_open_green(self):
        """Claim metadata should force the row out of the open/green state."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        queue_js = app_js[
            app_js.index("function _renderQueuePanel(options)"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel(options)"))
        ]

        self.assertIn("const _effectiveStatus = it =>", queue_js)
        self.assertIn("it.claimed_by || it.claimed_at || it.claimed_session_id", queue_js)
        self.assertIn("const status = _effectiveStatus(it);", queue_js)
        self.assertIn("const rawStatus = it.status || 'open';", queue_js)
        self.assertNotIn("const status = it.status || 'open';", queue_js)

    def test_live_queue_refreshes_when_mounted_in_sidebar_tab(self):
        """A worker claim must repaint the shared Queue panel outside the rail."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")

        visible = app_js[
            app_js.index("function _queuePanelIsVisible()"):
            app_js.index("let _uxqItemsCache", app_js.index("function _queuePanelIsVisible()"))
        ]
        self.assertIn("panel.parentElement === sidebarHost", visible)
        self.assertIn("queuePane.classList.contains('is-active')", visible)

        refresh_block = app_js[
            app_js.index("setInterval(_gated('uxFixesQueueMeta'"):
            app_js.index("// Queue board push channel", app_js.index("setInterval(_gated('uxFixesQueueMeta'"))
        ]
        self.assertIn("_queuePanelIsVisible()", refresh_block)

        stream_block = app_js[
            app_js.index("const invalidateAndRender = () =>"):
            app_js.index("const schedule = () =>", app_js.index("const invalidateAndRender = () =>"))
        ]
        self.assertIn("_queuePanelIsVisible()", stream_block)

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
        self.assertIn("function _uxqFilterItems(items, statusFilter, typeFilter)", app_js)
        self.assertIn("const typeScoped = _uxqFilterItems(inScope, _uxqGetFilter(), _uxqGetTypeFilter());", app_js)
        self.assertIn("[data-uxq-type-filter]", app_js)

    def test_all_queue_status_and_type_filters_cover_every_combination(self):
        """All-queue filtering must intersect status and type on every rerender."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("function _uxqFilterItems(items, statusFilter, typeFilter)", app_js)
        helper_start = app_js.index("function _uxqFilterItems(items, statusFilter, typeFilter)")
        helper_end = app_js.index("function _renderQueuePanel(options)", helper_start)
        helper = app_js[helper_start:helper_end]
        node_program = """
const vm = require('vm');
const context = {};
vm.createContext(context);
vm.runInContext(%s, context);
const items = [
  { id: 'open-bug', status: 'open', type: 'bug' },
  { id: 'closed-bug', status: 'closed', type: 'bug' },
  { id: 'open-feature', status: 'open', type: 'feature' },
  { id: 'closed-feature', status: 'closed', type: 'feature' },
];
const cases = {
  'all/all': ['open-bug', 'closed-bug', 'open-feature', 'closed-feature'],
  'all/bug': ['open-bug', 'closed-bug'],
  'all/feature': ['open-feature', 'closed-feature'],
  'open/all': ['open-bug', 'open-feature'],
  'open/bug': ['open-bug'],
  'open/feature': ['open-feature'],
};
for (const [key, expected] of Object.entries(cases)) {
  const [status, type] = key.split('/');
  const got = context._uxqFilterItems(items, status, type).map(item => item.id);
  if (JSON.stringify(got) !== JSON.stringify(expected)) throw new Error(key + ': ' + JSON.stringify(got));
}
""" % json.dumps(helper)
        subprocess.run(["node", "-e", node_program], cwd=PROJECT_ROOT, check=True)

    def test_wrap_mode_pins_queue_age_and_status_to_the_right_rail(self):
        """Wrapping a title must not let it displace the row's right-edge signals."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        start = app_css.index(".files-queue-panel.queue-wrap-titles .fq-row {")
        end = app_css.index("/* Status as a compact colored dot", start)
        wrap_css = app_css[start:end]

        self.assertIn('class="fq-row-signals"', app_js)
        self.assertIn(".fq-row-signals {", app_css)
        self.assertIn(".files-queue-panel.queue-wrap-titles .fq-row-signals {", wrap_css)
        self.assertIn("margin-left: auto;", wrap_css)
        self.assertIn("flex: 0 0 auto;", wrap_css)

    def test_queue_health_rows_include_live_and_recent_worker_sessions(self):
        """Queue health should carry Triggered Workers' live and Past 24h sessions inline."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        health_js = app_js[
            app_js.index("async function _renderQueueHealthStrip"):
            app_js.index("const _REPO_PROJECT_MAP", app_js.index("async function _renderQueueHealthStrip"))
        ]

        self.assertIn("const _liveWorkersByQueue = _uxqWorkersByQueue(health.wt_workers);", health_js)
        self.assertIn("const _pastWorkersByQueue = _uxqWorkersByQueue(health.past_workers);", health_js)
        self.assertIn("_renderWtWorkerCompactRow", health_js)
        self.assertIn("_renderWtPastWorkers", health_js)
        self.assertIn('class="fq-health-group"', health_js)
        self.assertIn("data-fq-worker-sid", app_js)
        self.assertIn("selectConversation(sid);", app_js)
        self.assertIn(".fq-health-worker-list", app_css)

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

    def test_all_queue_rows_prefix_compact_refs_with_the_queue_name(self):
        """All-queue rows need a compact queue cue before their local number."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

        queue_js = app_js[
            app_js.index("function _renderQueuePanel(options)"):
            app_js.index("// Jump the conversation pane", app_js.index("function _renderQueuePanel(options)"))
        ]

        self.assertIn("const allQueues = _uxqProjectKey(requestedProject) === 'ALL';", queue_js)
        self.assertIn("const compactRef = ref.replace(/^.*-/, '#');", queue_js)
        self.assertIn("const queuePrefix = String(it.project || ref.split('-')[0] || '').slice(0, 4);", queue_js)
        self.assertIn("const displayRef = allQueues && queuePrefix ? queuePrefix + compactRef : compactRef;", queue_js)
        self.assertIn("escapeHtml(displayRef)", queue_js)
        self.assertIn("fq-priority-chip", queue_js)
        self.assertIn("_uxqChips(it, priorityBumpHtml)", queue_js)
        self.assertIn(".fq-priority-chip .fq-prio-bump", app_css)
        self.assertIn("position: absolute;", app_css)


if __name__ == "__main__":
    unittest.main()
