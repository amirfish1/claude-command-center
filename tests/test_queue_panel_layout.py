import importlib
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestQueuePanelLayout(unittest.TestCase):
    def test_standalone_queue_uses_single_panel_master_detail_on_phone(self):
        """A phone swaps one active Q2 pane at a time instead of panning columns.

        Ticket detail is a popup on every viewport now (CCC-904), so mobile
        routing only toggles between the 'queues' and 'tickets' panels —
        there is no third 'detail' panel to route to.
        """
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
        q2_css = (PROJECT_ROOT / "static" / "q2.css").read_text(encoding="utf-8")
        q2_html = (PROJECT_ROOT / "static" / "q2.html").read_text(encoding="utf-8")

        self.assertIn("function showMobileColumn(column)", q2_js)
        self.assertIn("showMobileColumn('tickets');", q2_js)
        # 'detail' was dropped from the mobile router: only queues/tickets
        # panels are valid routing targets.
        self.assertIn("['queues', 'tickets'].indexOf(column) === -1", q2_js)
        # Picking a ticket opens the detail popup instead of a third pane.
        self.assertIn("openDetailModal();", q2_js)
        self.assertIn("data-mobile-panel", q2_js)
        self.assertIn("@media (max-width: 700px)", q2_css)
        self.assertIn('data-mobile-panel="queues"', q2_css)
        self.assertIn('data-mobile-panel="tickets"', q2_css)
        self.assertNotIn('data-mobile-panel="detail"', q2_css)
        self.assertNotIn("scroll-snap-type: x mandatory;", q2_css)
        self.assertIn('class="q2-shell" data-mobile-panel="queues"', q2_html)

    def test_phone_queue_navigation_has_explicit_back_controls(self):
        """Swiping advances the master/detail panes, but phone users can also
        return from tickets to queues and from a ticket back to its list.

        Ticket detail is a popup on every viewport now (CCC-904), so the
        ticket→list direction is the popup's explicit close control (× button
        or Escape), not a second back button.
        """
        q2_html = (PROJECT_ROOT / "static" / "q2.html").read_text(encoding="utf-8")
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
        q2_css = (PROJECT_ROOT / "static" / "q2.css").read_text(encoding="utf-8")

        self.assertIn('data-q2-mobile-back="queues"', q2_html)
        self.assertIn("showMobileColumn(back.getAttribute('data-q2-mobile-back'));", q2_js)
        self.assertIn(".q2-mobile-back", q2_css)

        # Ticket → list: the detail popup carries an explicit × close control
        # and Escape also closes it (via modalKey → closeModal()).
        self.assertIn('function openDetailModal()', q2_js)
        self.assertIn('q2-modal-detail-close', q2_js)
        self.assertIn('data-q2-modal-close', q2_js)
        self.assertIn("e.key === 'Escape'", q2_js)
        self.assertIn('function closeModal()', q2_js)

    def test_standalone_queue_keeps_recent_closed_tickets_visible(self):
        """Recent closes stay in context without expanding full history."""
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")

        self.assertIn("var RECENT_CLOSED_WINDOW_MS = 12 * 60 * 60 * 1000;", q2_js)
        # Recent closed also keeps tickets with unresolved notes, and the
        # all-time closure history stays behind the explicit "Show closed"
        # control (state.showClosed).
        self.assertIn(
            "var recentClosed = closed.filter(function (it) { return isRecentClosed(it) || unresolvedNotes(it).length > 0; });",
            q2_js,
        )
        self.assertIn("state.showClosed ? closed.slice(0, state.closedCap) : recentClosed", q2_js)
        self.assertIn("Recent closed", q2_js)

    def test_standalone_queue_glows_a_newly_filed_ticket(self):
        """The new ticket remains easy to locate after the queue refreshes."""
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
        q2_css = (PROJECT_ROOT / "static" / "q2.css").read_text(encoding="utf-8")

        self.assertIn("var NEW_TICKET_GLOW_MS = 4500;", q2_js)
        self.assertIn("function markNewTicket(ref)", q2_js)
        self.assertIn("markNewTicket(ref);", q2_js)
        self.assertIn("q2-new-ticket", q2_js)
        self.assertIn(".q2-trow.q2-new-ticket", q2_css)
        self.assertIn("@keyframes q2-new-ticket-glow", q2_css)

    def test_ticket_status_dot_has_a_matching_text_label(self):
        """Queue status colour is accompanied by its readable meaning."""
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
        q2_css = (PROJECT_ROOT / "static" / "q2.css").read_text(encoding="utf-8")

        self.assertIn('class="q2-tstatus"', q2_js)
        self.assertIn(".q2-tstatus", q2_css)

    def test_all_queue_view_is_a_read_first_global_inbox(self):
        """ALL combines live work without pretending to be a real queue."""
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
        q2_css = (PROJECT_ROOT / "static" / "q2.css").read_text(encoding="utf-8")

        self.assertIn("viewAll: false", q2_js)
        self.assertIn("function selectAllQueues()", q2_js)
        self.assertIn("All queues", q2_js)
        self.assertIn("function renderLiveWorkersStrip(host)", q2_js)
        self.assertIn("statusOf(it) !== 'closed'", q2_js)
        self.assertIn("q2-tqueue", q2_js)
        self.assertIn("Select a ticket to triage it across queues.", q2_js)
        self.assertIn(".q2-tqueue", q2_css)

    def test_all_queue_ticket_list_is_newest_first(self):
        """ALL orders its non-closed tickets by reverse chronological touch."""
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")

        self.assertIn(
            "openish.sort(function (a, b) { return touchedAt(b) - touchedAt(a); });",
            q2_js,
        )

    def test_selected_queue_shows_its_learnings_when_no_ticket_is_selected(self):
        """The detail pane makes queue guidance available before ticket work."""
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")

        self.assertIn("function queueLearningsPath(queue)", q2_js)
        self.assertIn("/api/queue/learnings?queue=", q2_js)
        self.assertIn("data-q2-learnings-open", q2_js)
        self.assertIn("Queue learnings", q2_js)

    def test_queue_learnings_path_is_confined_to_watchtower_learnings(self):
        """A queue name cannot turn the learnings viewer into a path reader."""
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(server, "_WT_HOME", pathlib.Path(temp_dir)):
                self.assertEqual(
                    server._wt_queue_learnings_path("CCC"),
                    pathlib.Path(temp_dir, "learnings", "CCC.md"),
                )
                self.assertIsNone(server._wt_queue_learnings_path("../../private"))

    def test_past_codex_worker_chip_gets_its_session_id(self):
        """Codex exec logs use a plain session header, not stream-json."""
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as temp_dir:
            logs_dir = pathlib.Path(temp_dir, "logs")
            logs_dir.mkdir()
            pathlib.Path(logs_dir, "ccc-deadbeef.log").write_text(
                "OpenAI Codex v1.0.0\n"
                "provider: openai\n"
                "session id: 019f9fc4-c7dc-7133-a73e-d7d6df2bec22\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(server, "_WT_HOME", pathlib.Path(temp_dir)),
                mock.patch.object(server, "_wt_read_workers", return_value=[]),
            ):
                rows = server._wt_past_workers(hours=1)

            self.assertEqual(
                rows[0]["session_id"],
                "019f9fc4-c7dc-7133-a73e-d7d6df2bec22",
            )

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
            "$convList.innerHTML = _convListHtml;\n"
            "    _updateConvTabBarHeightVar($convList);\n"
            "    _mountSharedQueuePanel();",
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
        tickets_pos = index_html.index('<div class="files-header fq-field-row">', log_pos)
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
            app_css.index("/* Section headers double as collapse toggles", app_css.index(".shared-queue-host-sidebar > .files-queue-panel {"))
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
        # One play control on the status dot, not two: the fq-run/fq-run-once
        # pair collapsed into a single "run this ticket" button (see
        # tests/test_queue_run_button.py for the behaviour it now carries).
        self.assertIn('class="fq-status fq-status-action fq-run', queue_js)
        self.assertNotIn("fq-run-once", queue_js)
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
        """The trigger shows the resolved queue name (override or repo-derived).

        The old native <select> had an "Auto: <scope>" option string. The new
        trigger pill shows the resolved queue name directly — no "Auto" label.
        The override/resolve logic (_uxqGetScopeOverride) is unchanged.
        """
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        scope_render = app_js[
            app_js.index("function _uxqRenderScopeSelect(items, currentScope)"):
            app_js.index("function _uxqSetScopeLoading(", app_js.index("function _uxqRenderScopeSelect(items, currentScope)"))
        ]

        # The new trigger renders the resolved queue name, not "Auto: …".
        self.assertIn("_uxqRenderQueueTrigger(items, currentScope)", scope_render)
        self.assertNotIn("Auto: ' + escapeHtml(currentScope || 'all')", scope_render)

        # The trigger renderer reads the override + resolved scope.
        trigger_fn = app_js[
            app_js.index("function _uxqRenderQueueTrigger(items, currentScope)"):
            app_js.index("function _uxqRenderWorkingNow()", app_js.index("function _uxqRenderQueueTrigger(items, currentScope)"))
        ]
        self.assertIn("_uxqGetScopeOverride()", trigger_fn)
        self.assertIn("_uxqProjectKey(currentScope)", trigger_fn)

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

    def test_closed_unresolved_rows_use_an_amber_attention_state(self):
        """Unresolved follow-up is attention-worthy without implying a hard block."""
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")
        unresolved_css = app_css[
            app_css.index(".fq-row.is-closed.has-unresolved .fq-status {"):
            app_css.index("/* Triage chips", app_css.index(".fq-row.is-closed.has-unresolved .fq-status {"))
        ]

        self.assertIn("background: var(--orange, #d29922);", unresolved_css)
        self.assertIn("color: var(--orange, #d29922);", unresolved_css)
        self.assertNotIn("var(--red, #f85149)", unresolved_css)
        self.assertNotIn("#ff7b72", unresolved_css)
        self.assertNotIn("rgba(248,81,73", unresolved_css)

    def test_live_queue_refreshes_when_mounted_in_sidebar_tab(self):
        """A worker claim refreshes sidebar rows and ends the Play spinner."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        app_css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text(encoding="utf-8")

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

        # The Play control's busy state now lasts exactly as long as its POST
        # (see tests/test_queue_run_button.py). The old pending-run set cleared
        # only when a ticket left `open`, so with drain off the spinner latched
        # forever and hid the button; the spinner itself stays, for the
        # optimistic "adding ticket" row.
        self.assertIn("const _uxqRunBusyRefs = new Set();", app_js)
        self.assertIn("_uxqRunBusyRefs.add(ref);", app_js)
        self.assertIn("_uxqRunBusyRefs.delete(ref);", app_js)
        self.assertIn("fq-status-pending", app_js)
        self.assertIn(".fq-status.fq-status-pending", app_css)
        self.assertIn("@keyframes fq-status-pending-spin", app_css)

    def test_play_handler_sets_busy_state_after_it_reads_the_ticket_ref(self):
        """The busy marker must be set inside, not before, the click handler."""
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        queue_clicks = app_js[
            app_js.index("const $queueList = document.getElementById('sidebarQueueList')"):
            app_js.index("// Priority bump button", app_js.index("const $queueList = document.getElementById('sidebarQueueList')"))
        ]

        self.assertIn(
            "const ref = runBtn.getAttribute('data-ref');\n"
            "          if (_uxqRunBusyRefs.has(ref)) return;\n",
            queue_clicks,
        )
        self.assertIn(
            "          _uxqRunBusyRefs.add(ref);\n"
            "          runBtn.disabled = true;",
            queue_clicks,
        )

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
  { id: 'closed-recent-bug', status: 'closed', type: 'bug', closed_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() },
  { id: 'closed-old-bug', status: 'closed', type: 'bug', closed_at: new Date(Date.now() - 13 * 60 * 60 * 1000).toISOString() },
  { id: 'open-feature', status: 'open', type: 'feature' },
  { id: 'closed-recent-feature', status: 'closed', type: 'feature', closed_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() },
  { id: 'closed-old-feature', status: 'closed', type: 'feature', closed_at: new Date(Date.now() - 13 * 60 * 60 * 1000).toISOString() },
];
const cases = {
  'all/all': ['open-bug', 'closed-recent-bug', 'closed-old-bug', 'open-feature', 'closed-recent-feature', 'closed-old-feature'],
  'all/bug': ['open-bug', 'closed-recent-bug', 'closed-old-bug'],
  'all/feature': ['open-feature', 'closed-recent-feature', 'closed-old-feature'],
  'open/all': ['open-bug', 'closed-recent-bug', 'open-feature', 'closed-recent-feature'],
  'open/bug': ['open-bug', 'closed-recent-bug'],
  'open/feature': ['open-feature', 'closed-recent-feature'],
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
