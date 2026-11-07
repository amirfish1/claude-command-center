// Copyright (c) 2026 Amir Fish. All rights reserved.
// SPDX-License-Identifier: LicenseRef-CCC-Software-License
/**
 * Sidebar "Jobs" tab: scheduled jobs with outcomes (server systemd timers +
 * scheduled laptop launchd agents). Data: GET /api/jobs (server-cached, refreshed
 * in the background). The sidebar list re-renders often and replaces the host
 * element, so app.js calls CCCJobsTab.mount() after each rebuild; mount()
 * refills the fresh host from cached data without a fetch.
 */
(function () {
  'use strict';

  const HOST_KEY = 'ccc-jobs-host';
  const POLL_MS = 60000;
  let _data = null;
  let _lastFetch = 0;
  let _inflight = false;
  let _lastHtml = '';
  let _host = (function () {
    try { const v = localStorage.getItem(HOST_KEY); if (v === 'all' || v === 'hermes' || v === 'laptop') return v; } catch (_) {}
    return null;
  })();
  const _expanded = new Set(); // expanded job IDs
  const _logs = new Map(); // job id -> text | null (loading)
  const SORT_KEY = 'ccc-jobs-view-v2';
  let _sort = 'overview';
  try { const v = localStorage.getItem(SORT_KEY); if (['overview', 'upcoming', 'recent', 'all'].includes(v)) _sort = v; } catch (_) {}
  let _query = '';
  let _project = '';
  let _filter = 'all';
  let _fetchError = '';
  // Live tail of a running job's current invocation: job id -> {cursor, running, done}
  const _live = new Map();
  const _stick = new Map(); // row key -> false when the user scrolled the log up
  const LIVE_MS = 4000;

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function rel(iso, future) {
    if (!iso) return '';
    const ms = Date.parse(iso);
    if (isNaN(ms)) return '';
    let sec = Math.round((ms - Date.now()) / 1000);
    const isFuture = sec > 0;
    sec = Math.abs(sec);
    let t;
    if (sec < 60) t = sec + 's';
    else if (sec < 3600) t = Math.round(sec / 60) + 'm';
    else if (sec < 86400) t = Math.round(sec / 3600) + 'h';
    else t = Math.round(sec / 86400) + 'd';
    if (future !== undefined && isFuture !== future) return isFuture ? 'in ' + t : t + ' ago';
    return isFuture ? 'in ' + t : t + ' ago';
  }

  function dur(s) {
    if (s == null) return '';
    s = Math.round(s);
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60), r = s % 60;
    if (m < 60) return m + 'm' + (r ? r + 's' : '');
    return Math.floor(m / 60) + 'h' + (m % 60 ? (m % 60) + 'm' : '');
  }

  function shortName(n) {
    return String(n || '').replace(/^com\.(?!apple\.)[^.]+\./, '');
  }

  function isActive() {
    let t = null;
    try { t = window.CCCNavigation.getTab(); } catch (_) {}
    return t === 'jobs';
  }

  function attentionCount() {
    if (!_data || !_data.summary) return 0;
    const sm = _data.summary[activeHost()];
    return (sm && sm.attention) || 0;
  }

  function updateBadge() {
    const btn = document.querySelector('[data-conv-tab="jobs"]');
    if (!btn) return;
    const n = attentionCount();
    let span = btn.querySelector('.conv-tab-count');
    if (n && !span) { span = document.createElement('span'); span.className = 'conv-tab-count'; btn.appendChild(span); }
    if (span) { if (n) span.textContent = String(n); else span.remove(); }
  }

  // The host view in effect. Unset means "this machine's view": the VM's own
  // timers on Linux, everything (laptop + server) on a laptop. The Laptop
  // view is always empty on Linux, so it is never offered or honored there.
  // A host's own name as the feed reports it; generic until it has reported.
  function hostLabel(k) {
    const h = (_data && _data.hosts && _data.hosts[k]) || {};
    return h.label || (k === 'hermes' ? 'Server' : 'Laptop');
  }

  function activeHost() {
    const local = _data && _data.local_host;
    if (local === 'hermes' && (_host === 'laptop' || !_host)) return 'hermes';
    return _host || (local === 'laptop' ? 'all' : 'hermes');
  }

  function visibleJobs() {
    const jobs = (_data && _data.jobs) || [];
    const host = activeHost();
    return host === 'all' ? jobs : jobs.filter(j => j.host === host);
  }

  // No em-dashes in user copy.
  function nodash(t) { return String(t == null ? '' : t).replace(/ — /g, ': ').replace(/—/g, ':'); }

  function fmtLocal(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  // "9:00pm" today, "tmrw 2:00am", "Sun 7:30am" within a week, else "Oct 8".
  function nextLabel(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const t = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }).replace(' ', '').toLowerCase();
    const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((day(d) - day(new Date())) / 86400000);
    if (days <= 0) return t;
    if (days === 1) return 'tmrw ' + t;
    if (days < 7) return d.toLocaleDateString('en-US', { weekday: 'short' }) + ' ' + t;
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }

  function needsAttention(j) { return j.enabled !== false && ['failed', 'stale'].includes(j.status); }
  function statusLabel(j) {
    if (j.enabled === false) return 'Disabled';
    return {ok: 'Succeeded', failed: 'Failed', stale: 'Overdue', running: 'Running', unknown: 'Unverified', disabled: 'Disabled'}[j.status] || 'Unverified';
  }
  function headerHtml() {
    const jobs = visibleJobs();
    const host = activeHost();
    const hosts = _data.hosts || {};
    const selected = host === 'all' ? Object.values(hosts) : [hosts[host] || {}];
    const unavailable = selected.some(h => h.status === 'offline' || h.stale_data);
    const loading = selected.some(h => h.status === 'loading');
    const attention = jobs.filter(needsAttention).length;
    const running = jobs.filter(j => j.status === 'running').length;
    const disabled = jobs.filter(j => j.enabled === false).length;
    const hostKeys = _data.local_host === 'hermes' ? ['hermes'] : ['all', 'hermes', 'laptop'];
    const hostOptions = hostKeys.map(k => '<option value="' + k + '"' + (host === k ? ' selected' : '') + '>' + (k === 'all' ? 'All machines' : esc(hostLabel(k))) + '</option>').join('');
    const views = [['overview', 'Overview'], ['upcoming', 'Upcoming'], ['recent', 'Recent runs'], ['all', 'All jobs']];
    const projects = [...new Set(jobs.map(j => j.project || 'Other'))].sort();
    return '<div class="jobs-head">'
      + '<div class="jobs-titlebar"><h2>Scheduled jobs</h2><button type="button" class="jobs-add-btn" data-jobs-add>New job</button></div>'
      + '<div class="jobs-source"><label>Machine <select data-jobs-machine aria-label="Machine">' + hostOptions + '</select></label><span>' + jobs.length + ' jobs · ' + (unavailable ? 'Offline / cached data' : loading ? 'Syncing…' : 'Connected') + '</span><button type="button" class="jobs-refresh" data-jobs-refresh>Refresh</button></div>'
      + ((_fetchError || unavailable) ? '<div class="jobs-notice" role="status">' + esc(_fetchError || 'Machine unavailable. These are the last known results; schedules may have changed.') + '</div>' : '')
      + '<div class="jobs-health"><span class="' + (attention ? 'needs-attention' : '') + '">' + (attention ? attention + (attention === 1 ? ' needs attention' : ' need attention') : 'No reported issues') + '</span><span>' + running + ' running · ' + disabled + ' disabled</span></div>'
      + '<nav class="jobs-views" aria-label="Job views">' + views.map(([k, label]) => '<button type="button" data-jobs-sort="' + k + '" aria-pressed="' + (_sort === k) + '" class="' + (_sort === k ? 'is-active' : '') + '">' + label + '</button>').join('') + '</nav>'
      + '<div class="jobs-tools"><input type="search" data-jobs-search aria-label="Search jobs" placeholder="Search jobs…" value="' + esc(_query) + '"><select data-jobs-project aria-label="Filter by project"><option value="">All projects</option>' + projects.map(k => '<option' + (_project === k ? ' selected' : '') + ' value="' + esc(k) + '">' + esc(k) + '</option>').join('') + '</select>'
      + (_sort === 'all' ? '<select data-jobs-filter aria-label="Filter by status">' + [['all','Any status'],['attention','Needs attention'],['running','Running'],['disabled','Disabled'],['unknown','Unverified']].map(([k,v]) => '<option value="' + k + '"' + (_filter === k ? ' selected' : '') + '>' + v + '</option>').join('') + '</select>' : '') + '</div></div>';
  }

  function sectionHtml(title, jobs, hint, limit) {
    const shown = limit ? jobs.slice(0, limit) : jobs;
    return '<section class="jobs-section"><div class="jobs-section-heading"><h3>' + esc(title) + '</h3><span>' + jobs.length + '</span></div>'
      + (hint ? '<p class="jobs-section-hint">' + esc(hint) + '</p>' : '')
      + shown.map(j => rowHtml(j)).join('') + '</section>';
  }

  function nextTime(j) { return Date.parse(j.next_run_at || '') || Infinity; }
  function upcomingHtml(jobs, preview) {
    const enabled = jobs.filter(j => j.enabled !== false);
    const repeating = enabled.filter(j => j.timeline && j.timeline.kind === 'interval' && j.timeline.interval_s < 3600);
    const scheduled = enabled.filter(j => !repeating.includes(j) && Number.isFinite(nextTime(j))).sort((a,b) => nextTime(a) - nextTime(b));
    const unknown = enabled.filter(j => !repeating.includes(j) && !Number.isFinite(nextTime(j)));
    return sectionHtml('Next scheduled', scheduled, 'Next run per job · ' + Intl.DateTimeFormat().resolvedOptions().timeZone + '. Estimates are marked.', preview ? 5 : 0)
      + (!scheduled.length ? '<p class="jobs-section-hint">No upcoming run times available.</p>' : '')
      + (!preview && repeating.length ? sectionHtml('Repeats throughout the day', repeating, 'Frequent jobs, separate from the daily schedule.') : '')
      + (!preview && unknown.length ? sectionHtml('Schedule not available', unknown, 'Open a job to inspect its schedule and last result.') : '');
  }

  function overviewHtml(jobs) {
    const attention = jobs.filter(needsAttention);
    const running = jobs.filter(j => j.status === 'running');
    return (attention.length ? sectionHtml('Needs attention', attention, 'Failed or overdue. Open a job for its last result and logs.')
      : '<div class="jobs-clear"><strong>' + (_query.trim() || _project ? 'No matching jobs need attention' : 'No jobs need attention') + '</strong><span>Based on the latest reported status.</span></div>')
      + (running.length ? sectionHtml('Running now', running, 'Open a job to follow its output live.') : '')
      + upcomingHtml(jobs, true)
      + '<button type="button" class="jobs-more" data-jobs-sort="upcoming">See the full schedule</button>'
      + sectionHtml('Latest results', jobs.filter(j => j.last_run_at && j.status !== 'running').sort((a,b) => lastRunMs(b)-lastRunMs(a)).slice(0, 4), 'Latest reported run per job.')
      + '<button type="button" class="jobs-more" data-jobs-sort="recent">See all recent runs</button>';
  }

  function chipHtml(t) {
    const label = esc(t.ref);
    if (t.kind === 'watchtower') {
      const q = String(t.ref).replace(/-\d+$/, '');
      return '<a role="button" tabindex="0" class="job-chip watchtower-ticket-link" data-watchtower-ticket="' + esc(t.ref)
        + '" data-watchtower-queue="' + esc(q) + '">' + label + '</a>';
    }
    if (t.url) return '<a class="job-chip" href="' + esc(t.url) + '" target="_blank" rel="noopener" title="' + esc(t.repo || '') + '">' + label + '</a>';
    return '<span class="job-chip">' + label + '</span>';
  }

  function chipsHtml(tickets, max) {
    const list = tickets || [];
    const shown = list.slice(0, max);
    return shown.map(chipHtml).join('') + (list.length > max ? '<span class="job-chip-more">+' + (list.length - max) + '</span>' : '');
  }

  // Where the running indicator goes. A WatchTower ticket the current run
  // printed is the only agent-session link we can resolve from data we already
  // collect (same chip idiom as the rest of the app); anything else is a plain
  // process, so the indicator opens the live log.
  function runTicket(j) {
    return (j.tickets || []).find(t => t.kind === 'watchtower') || null;
  }

  function liveHtml(j) {
    const t = runTicket(j);
    if (t) {
      const q = String(t.ref).replace(/-\d+$/, '');
      return '<a role="button" tabindex="0" class="job-live watchtower-ticket-link" data-watchtower-ticket="' + esc(t.ref)
        + '" data-watchtower-queue="' + esc(q) + '" title="Running now: open ' + esc(t.ref) + '"><i></i>live ' + esc(t.ref) + '</a>';
    }
    return '<span role="button" tabindex="0" class="job-live" data-job-live="' + esc(j.id) + '" title="Running now: watch the live log"><i></i>live</span>';
  }

  function rowHtml(j, opt) {
    opt = opt || {};
    const key = opt.key || j.id;
    const open = _expanded.has(key);
    const running = j.status === 'running';
    const label = statusLabel(j);
    const name = shortName(j.name).replace(/[-_]+/g, ' ').replace(/\b\w/, c => c.toUpperCase());
    const next = j.enabled === false ? 'Not scheduled' : j.next_run_at
      ? (nextTime(j) < Date.now() ? 'Awaiting schedule update' : (j.next_run_estimated ? 'Est. next ' : 'Next ') + nextLabel(j.next_run_at))
      : 'Next run unavailable';
    const result = j.outcome_kind === 'summary' && j.outcome ? nodash(j.outcome) : '';
    let h = '<article class="job-row st-' + esc(j.enabled === false ? 'disabled' : j.status) + (open ? ' is-open' : '') + '" data-job-id="' + esc(j.id) + '" data-row-key="' + esc(key) + '">'
      + '<button type="button" class="job-toggle" data-job-toggle aria-expanded="' + open + '" aria-label="' + esc(name + ': ' + label + '. ' + (open ? 'Hide' : 'Show') + ' details') + '">'
      + '<span class="job-line1"><span class="job-name">' + esc(name) + '</span><span class="job-status">' + esc(label) + '</span><span class="job-chevron" aria-hidden="true">' + (open ? '−' : '+') + '</span></span>'
      + '<span class="job-purpose">' + esc(nodash(j.description) || shortName(j.name)) + '</span>'
      + '<span class="job-meta"><span>' + esc(j.project || 'Other') + '</span><span>' + esc(j.schedule || 'Schedule unknown') + '</span>' + (activeHost() === 'all' ? '<span>' + esc(hostLabel(j.host === 'hermes' ? 'hermes' : 'laptop')) + '</span>' : '') + '</span>'
      + '<span class="job-timing"><span title="' + esc(j.last_run_at ? fmtLocal(j.last_run_at) : '') + '">' + esc(j.last_run_at ? (running ? 'Started ' : 'Last run ') + rel(j.last_run_at) : 'No recorded run') + '</span><span title="' + esc(j.next_run_at ? fmtLocal(j.next_run_at) : '') + '">' + esc(next) + '</span></span>'
      + (result ? '<span class="job-result">' + esc(result) + '</span>' : '') + '</button>';
    if (open) {
      const log = _logs.get(j.id);
      const lv = _live.get(j.id);
      const facts = [];
      facts.push('Schedule: ' + (j.schedule || 'Unknown'));
      facts.push('Job ID: ' + j.name);
      if (j.next_run_at && j.enabled !== false) facts.push((j.next_run_estimated ? 'Estimated next: ' : 'Next: ') + fmtLocal(j.next_run_at));
      if (j.last_run_at) facts.push('last ' + fmtLocal(j.last_run_at));
      if (j.last_duration_s != null) facts.push('took ' + dur(j.last_duration_s));
      if (j.exit_code) facts.push('exit ' + j.exit_code);
      const liveNote = running && lv && !lv.done ? '<div class="job-facts job-livenote"><i></i>Live, updating every few seconds</div>'
        : (lv && lv.done ? '<div class="job-facts job-livenote is-done">Finished. Showing the final output.</div>' : '');
      h += '<div class="job-detail">'
        + (j.description ? '<div class="job-desc">' + esc(nodash(j.description)) + '</div>' : '')
        + '<div class="job-facts">' + esc(facts.join('\n')) + '</div>'
        + (j.repo_path ? '<div class="job-facts">' + esc(j.repo_path) + '</div>' : '')
        + (j.history && j.history.length ? '<div class="job-facts">Recent recorded runs ' + historyHtml(j.history) + '</div>' : '')
        + (j.outcome ? '<div class="job-outcome-full">' + (j.outcome_kind === 'output' ? '<span class="job-out-label">last output </span>' : '') + esc(nodash(j.outcome)) + '</div>' : '')
        + (j.events && j.events.length ? '<div class="job-events"><h4 class="job-log-title">Recent activity</h4>' + j.events.slice(-8).reverse().map(event => '<div class="job-event"><time>' + esc(fmtLocal(event.at)) + '</time> ' + esc(nodash(event.text)) + '</div>').join('') + '</div>' : '')
        + (j.tickets && j.tickets.length ? '<div class="job-chips">' + chipsHtml(j.tickets, 50) + '</div>' : '')
        + (running ? liveHtml(j) : '') + liveNote
        + '<h4 class="job-log-title">Run output</h4><pre class="job-log">' + (log == null ? 'Loading log...' : esc(log)) + '</pre></div>';
    }
    return h + '</article>';
  }

  function historyHtml(hist) {
    return '<span class="jobs-hist">' + hist.slice(-14).map(x => '<i class="' + (x.ok ? 'ok' : 'bad') + '" title="' + esc(x.at) + '"></i>').join('') + '</span>';
  }

  function lastRunMs(j) { const t = Date.parse(j.last_run_at || ''); return isNaN(t) ? 0 : t; }

  function render() {
    const el = document.getElementById('sidebarJobsHost');
    if (!el) return;
    let html;
    if (!_data) {
      html = '<div class="jobs-empty" role="status">' + (_fetchError ? 'Could not load jobs. <button type="button" class="jobs-more" data-jobs-refresh>Try again</button>' : 'Loading scheduled jobs…') + '</div>';
    } else {
      const all = visibleJobs();
      const query = _query.trim().toLowerCase();
      const jobs = all.filter(j => (!_project || (j.project || 'Other') === _project)
        && (!query || [j.name, j.description, j.project, j.outcome].join(' ').toLowerCase().includes(query))
        && (_sort !== 'all' || _filter === 'all' || (_filter === 'attention' ? needsAttention(j) : _filter === 'disabled' ? j.enabled === false : j.status === _filter)));
      html = headerHtml() + '<div class="jobs-list">'
        + (!jobs.length ? '<div class="jobs-empty"><strong>' + (all.length ? 'No matching jobs' : 'No scheduled jobs') + '</strong><p>' + (all.length ? 'Try a different search or project.' : 'Create a job to automate recurring work.') + '</p>' + (all.length ? '<button type="button" class="jobs-more" data-jobs-clear>Clear filters</button>' : '') + '</div>'
          : _sort === 'overview' ? overviewHtml(jobs) : _sort === 'upcoming' ? upcomingHtml(jobs) : _sort === 'recent' ? sectionHtml('Recent runs', jobs.slice().sort((a,b) => lastRunMs(b)-lastRunMs(a)), 'Latest reported run per job. Open one for recorded history and output.') : sectionHtml('All jobs', jobs.slice().sort((a,b) => String(a.name).localeCompare(String(b.name))), 'Search, inspect, and check the schedule of any job.'))
        + '</div>';

    }
    if (html !== _lastHtml || !el.firstChild) {
      const scroll = el.scrollTop;
      const searchFocused = document.activeElement && document.activeElement.matches('[data-jobs-search]');
      const caret = searchFocused ? document.activeElement.selectionStart : null;
      const focusedRow = document.activeElement && document.activeElement.closest('[data-job-toggle]');
      const focusedKey = focusedRow ? focusedRow.closest('[data-row-key]').getAttribute('data-row-key') : null;
      const controlAttr = ['data-jobs-project', 'data-jobs-filter', 'data-jobs-machine', 'data-jobs-sort', 'data-jobs-refresh', 'data-jobs-add'].find(a => document.activeElement && document.activeElement.hasAttribute(a));
      const controlValue = controlAttr ? document.activeElement.getAttribute(controlAttr) : null;
      // Remember whether each open log was pinned to the bottom; the rebuild
      // resets it, and a user who scrolled up must not be yanked down.
      const tops = new Map();
      el.querySelectorAll('[data-row-key] .job-log').forEach(pre => {
        const k = pre.closest('[data-row-key]').getAttribute('data-row-key');
        const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
        _stick.set(k, atBottom);
        tops.set(k, pre.scrollTop);
      });
      el.innerHTML = html;
      if (searchFocused) { const input = el.querySelector('[data-jobs-search]'); if (input) { input.focus({preventScroll:true}); if (caret !== null) input.setSelectionRange(caret, caret); } }
      if (controlAttr) { const control = [...el.querySelectorAll('[' + controlAttr + ']')].find(e => e.getAttribute(controlAttr) === controlValue); if (control) control.focus({preventScroll:true}); }
      if (focusedKey) { const row = [...el.querySelectorAll('[data-row-key]')].find(r => r.getAttribute('data-row-key') === focusedKey); if (row) row.querySelector('[data-job-toggle]').focus({preventScroll:true}); }
      el.scrollTop = scroll;
      el.querySelectorAll('[data-row-key] .job-log').forEach(pre => {
        const k = pre.closest('[data-row-key]').getAttribute('data-row-key');
        if (_stick.get(k) !== false) pre.scrollTop = pre.scrollHeight;
        else if (tops.has(k)) pre.scrollTop = tops.get(k);
      });
      _lastHtml = html;
    }
  }

  function scrollLogs(id) {
    document.querySelectorAll('#sidebarJobsHost [data-job-id]').forEach(function (r) {
      if (r.getAttribute('data-job-id') !== id) return;
      const pre = r.querySelector('.job-log');
      if (pre && _stick.get(r.getAttribute('data-row-key')) !== false) pre.scrollTop = pre.scrollHeight;
    });
  }

  function jobById(id) { return ((_data && _data.jobs) || []).find(j => j.id === id); }

  async function loadLog(id) {
    const j = jobById(id);
    if (j && j.status === 'running' && id.indexOf('hermes:') === 0) { return liveStart(id); }
    try {
      const res = await (window.__cccBackgroundApiFetch || fetch)('/api/system/scheduled-jobs/log?lines=50&id=' + encodeURIComponent(id), { cache: 'no-store' });
      const d = await res.json();
      _logs.set(id, (d && d.log) ? d.log : '(no log output)');
    } catch (e) {
      _logs.set(id, 'Failed to load log: ' + e);
    }
    render();
    scrollLogs(id);
  }

  // Live tail: first call loads the current invocation's output, later calls
  // ask only for lines after the returned journal cursor (one ssh per call).
  async function liveFetch(id) {
    const lv = _live.get(id) || { cursor: '', running: true, done: false, busy: false };
    if (lv.busy) return;
    lv.busy = true; _live.set(id, lv);
    try {
      const url = '/api/system/scheduled-jobs/log?live=1&lines=200&id=' + encodeURIComponent(id)
        + (lv.cursor ? '&cursor=' + encodeURIComponent(lv.cursor) : '');
      const res = await (window.__cccBackgroundApiFetch || fetch)(url, { cache: 'no-store' });
      const d = await res.json();
      if (d && d.ok) {
        const prev = _logs.get(id);
        const add = d.log || '';
        let text = (lv.cursor && typeof prev === 'string') ? (prev + (add ? (prev ? '\n' : '') + add : '')) : (add || '(waiting for output)');
        if (text.length > 60000) text = text.slice(-60000);
        _logs.set(id, text);
        if (d.cursor) lv.cursor = d.cursor;
        lv.running = !!d.running;
        if (!d.running) { lv.done = true; }
      } else if (typeof _logs.get(id) !== 'string') {
        _logs.set(id, 'Could not load live log: ' + ((d && d.error) || 'unknown error'));
      }
    } catch (e) {
      if (typeof _logs.get(id) !== 'string') _logs.set(id, 'Failed to load live log: ' + e);
    }
    lv.busy = false;
    render();
    scrollLogs(id);
    if (lv.done) {
      // Finished: pick up the final outcome from the feed now.
      poll();
    }
  }

  function liveStart(id) {
    _live.set(id, { cursor: '', running: true, done: false, busy: false });
    return liveFetch(id);
  }

  function openIds() {
    const ids = new Set();
    _expanded.forEach(k => ids.add(k.split('#')[0]));
    return ids;
  }

  // Poll only while: row expanded, job running, tab visible and showing.
  function liveTick() {
    if (document.hidden || !isActive() || !document.getElementById('sidebarJobsHost')) return;
    openIds().forEach(id => {
      const lv = _live.get(id);
      const j = jobById(id);
      if (lv && !lv.done && lv.running) liveFetch(id);
      else if (j && j.status === 'running' && id.indexOf('hermes:') === 0 && !lv) liveStart(id);
    });
  }

  function focusLive(id) {
    const j = jobById(id);
    const keys = [];
    document.querySelectorAll('#sidebarJobsHost [data-job-id]').forEach(r => { if (r.getAttribute('data-job-id') === id) keys.push(r.getAttribute('data-row-key')); });
    const key = keys[0] || id;
    if (!_expanded.has(key)) { _expanded.add(key); _logs.set(id, null); loadLog(id); }
    render();
    const row = document.querySelector('#sidebarJobsHost [data-row-key="' + key.replace(/"/g, '\\"') + '"]');
    if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
    void j;
  }

  // ── "+ Add": create a job through an agent session ──────────────────────
  // CCC never writes systemd units or LaunchAgents itself. The dialog sends
  // the fields to POST /api/jobs/add (via window.cccSpawnPromptSession, so the
  // composer's engine/model and spawn placeholder apply); the server composes
  // the prompt (ccc_server/jobs_add.py) and spawns a normal session that
  // creates the job and checks it shows up here. `ccc jobs add` uses the same
  // endpoint.
  function closeAddDialog() {
    const d = document.getElementById('jobsAddDialog');
    if (d) d.remove();
  }

  async function openAddDialog() {
    closeAddDialog();
    const host = activeHost() === 'laptop' ? 'laptop' : 'hermes';
    const wrap = document.createElement('div');
    wrap.id = 'jobsAddDialog';
    wrap.className = 'jobs-add-overlay';
    wrap.innerHTML = '<form class="jobs-add-card" role="dialog" aria-modal="true" aria-label="Add a scheduled job" novalidate>'
      + '<div class="jobs-add-title">Add a scheduled job</div>'
      + '<div class="jobs-add-sub">An agent session creates it and checks it appears here.</div>'
      + '<label>Host<select name="host">'
      + '<option value="hermes"' + (host === 'hermes' ? ' selected' : '') + '>' + esc(hostLabel('hermes')) + '</option>'
      + '<option value="laptop"' + (host === 'laptop' ? ' selected' : '') + '>' + esc(hostLabel('laptop')) + '</option></select></label>'
      + '<label>Repo / project folder<input name="repo" list="jobsAddRepos" autocomplete="off" placeholder="/path/to/repo"></label>'
      + '<datalist id="jobsAddRepos"></datalist>'
      + '<label>What should it do<textarea name="what" rows="3" placeholder="e.g. Summarize yesterday\'s failed CI runs and file tickets"></textarea></label>'
      + '<label>When<input name="when" autocomplete="off" placeholder="e.g. daily 7am, every 2h, Sundays 7:30am"></label>'
      + '<div class="jobs-add-error" role="alert" hidden></div>'
      + '<div class="jobs-add-actions"><button type="button" class="jobs-add-cancel">Cancel</button>'
      + '<button type="submit" class="jobs-add-submit">Start agent</button></div>'
      + '</form>';
    document.body.appendChild(wrap);
    const form = wrap.querySelector('form');
    const err = wrap.querySelector('.jobs-add-error');
    wrap.addEventListener('mousedown', e => { if (e.target === wrap) closeAddDialog(); });
    wrap.querySelector('.jobs-add-cancel').addEventListener('click', closeAddDialog);
    wrap.addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); closeAddDialog(); } });
    form.elements.what.focus();
    form.addEventListener('input', e => { if (e.target.classList) e.target.classList.remove('is-invalid'); });
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const f = {
        host: form.elements.host.value,
        repo: form.elements.repo.value.trim(),
        what: form.elements.what.value.trim(),
        when: form.elements.when.value.trim(),
      };
      const missing = [['repo', 'a repo or folder'], ['what', 'what it should do'], ['when', 'when it runs']]
        .filter(x => !f[x[0]]);
      form.querySelectorAll('.is-invalid').forEach(el => el.classList.remove('is-invalid'));
      if (missing.length) {
        missing.forEach(x => form.elements[x[0]].classList.add('is-invalid'));
        err.textContent = 'Fill in ' + missing.map(x => x[1]).join(', ') + '.';
        err.hidden = false;
        form.elements[missing[0][0]].focus();
        return;
      }
      if (typeof window.cccSpawnPromptSession !== 'function') {
        err.textContent = 'Session spawning is not available on this page.';
        err.hidden = false;
        return;
      }
      const btn = form.querySelector('.jobs-add-submit');
      btn.disabled = true; btn.textContent = 'Starting...';
      // Spawn where the job lives when that folder is on this machine.
      const local = _repoPaths.indexOf(f.repo) !== -1;
      const res = await window.cccSpawnPromptSession({
        job: f,
        name: 'New job: ' + f.what.split('\n')[0].slice(0, 60),
        repoPath: local ? f.repo : '',
      });
      if (res && res.ok) { closeAddDialog(); return; }
      btn.disabled = false; btn.textContent = 'Start agent';
      err.textContent = 'Could not start the session: ' + ((res && res.error) || 'unknown error');
      err.hidden = false;
    });
    // Repo suggestions: the same list the composer's folder picker uses.
    try {
      const r = await fetch('/api/repo/list', { cache: 'no-store' });
      const d = await r.json();
      const repos = (d && Array.isArray(d.repos)) ? d.repos : [];
      _repoPaths = repos.map(x => x && x.path).filter(Boolean);
      const dl = document.getElementById('jobsAddRepos');
      if (dl) dl.innerHTML = _repoPaths.map(p => '<option value="' + esc(p) + '"></option>').join('');
      const input = form.elements.repo;
      if (input && !input.value && d && d.current) input.value = d.current;
    } catch (_) {}
  }
  let _repoPaths = [];

  function onClick(ev) {
    if (ev.target.closest('[data-jobs-refresh]')) { poll(); return; }
    if (ev.target.closest('[data-jobs-clear]')) { _query = ''; _project = ''; _filter = 'all'; render(); return; }
    if (ev.target.closest('[data-jobs-add]')) { openAddDialog(); return; }
    const liveEl = ev.target.closest('[data-job-live]');
    if (liveEl) { ev.stopPropagation(); focusLive(liveEl.getAttribute('data-job-live')); return; }
    if (ev.target.closest('a.job-live')) return; // WatchTower ticket link: the app's own handler opens it
    const sortEl = ev.target.closest('[data-jobs-sort]');
    if (sortEl) {
      _sort = sortEl.getAttribute('data-jobs-sort');
      try { localStorage.setItem(SORT_KEY, _sort); } catch (_) {}
      render();
      const host = document.getElementById('sidebarJobsHost'); if (host) host.scrollTop = 0;
      return;
    }
    const seg = ev.target.closest('[data-jobs-host]');
    if (seg) {
      _host = seg.getAttribute('data-jobs-host');
      try { localStorage.setItem(HOST_KEY, _host); } catch (_) {}
      updateBadge();
      render();
      return;
    }
    if (ev.target.closest('.job-detail') || ev.target.closest('a.job-chip')) return;
    if (!ev.target.closest('[data-job-toggle]')) return;
    const row = ev.target.closest('[data-job-id]');
    if (!row) return;
    const id = row.getAttribute('data-job-id');
    const key = row.getAttribute('data-row-key') || id;
    if (_expanded.has(key)) {
      _expanded.delete(key);
      if (!openIds().has(id)) { _live.delete(id); }
    } else { _expanded.add(key); _logs.set(id, null); _stick.set(key, true); loadLog(id); }
    render();
  }

  async function poll() {
    if (_inflight) return;
    _inflight = true;
    try {
      const res = await (window.__cccBackgroundApiFetch || fetch)('/api/jobs', { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const d = await res.json();
      if (!d || !d.ok) throw new Error('Jobs feed unavailable');
      if (d.ok) {
        _fetchError = '';
        _data = d;
        _lastFetch = Date.now();
        // A finished live tail is dropped once the feed agrees the job is no longer running.
        _live.forEach((lv, id) => { const j = jobById(id); if (lv.done && (!j || j.status !== 'running')) _live.delete(id); });
        updateBadge();
        render();
      }
    } catch (_) {
      _fetchError = 'Could not refresh jobs. Showing the last known results. Try Refresh.';
      render();
    } finally {
      _inflight = false;
    }
  }

  function mount() {
    const el = document.getElementById('sidebarJobsHost');
    if (!el) return;
    _lastHtml = '';
    if (!el._jobsWired) {
      el.addEventListener('click', onClick);
      el.addEventListener('input', e => { if (e.target.matches('[data-jobs-search]')) { _query = e.target.value; render(); } });
      el.addEventListener('change', e => {
        if (e.target.matches('[data-jobs-project]')) _project = e.target.value;
        else if (e.target.matches('[data-jobs-filter]')) _filter = e.target.value;
        else if (e.target.matches('[data-jobs-machine]')) { _host = e.target.value; _project = ''; try { localStorage.setItem(HOST_KEY, _host); } catch (_) {} updateBadge(); }
        else return;
        render();
      });
      el._jobsWired = true;
    }
    render();
    if (Date.now() - _lastFetch > POLL_MS) poll();
  }

  // One early fetch so the tab badge is populated; then poll only while the
  // Jobs tab is showing and the document is visible.
  setTimeout(poll, 4000);
  setInterval(function () {
    if (document.hidden || !isActive() || !document.getElementById('sidebarJobsHost')) return;
    const loading = _data && _data.hosts && _data.hosts.hermes && _data.hosts.hermes.status === 'loading';
    const anyRunning = visibleJobs().some(j => j.status === 'running');
    if (Date.now() - _lastFetch >= (loading ? 5000 : anyRunning ? 10000 : POLL_MS)) poll();
    else render(); // keep relative times fresh
  }, 5000);
  setInterval(liveTick, LIVE_MS);

  window.CCCJobsTab = { mount: mount, attentionCount: attentionCount, openAddDialog: openAddDialog };
})();
