// Copyright (c) 2026 Amir Fish. All rights reserved.
// SPDX-License-Identifier: LicenseRef-CCC-Software-License
/**
 * Sidebar "Jobs" tab: scheduled jobs with outcomes (hermes-gcp systemd timers +
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
  const _expanded = new Set(); // row keys (job id, or job id + '#' + slot in the Day view)
  const _collapsed = new Set((function () { try { return JSON.parse(localStorage.getItem('ccc-jobs-collapsed') || '[]'); } catch (_) { return []; } })());
  const _logs = new Map(); // job id -> text | null (loading)
  const SORT_KEY = 'ccc-jobs-sort';
  let _sort = (function () {
    try { const v = localStorage.getItem(SORT_KEY); if (v === 'project' || v === 'recent' || v === 'day') return v; } catch (_) {}
    return 'project';
  })();
  // Live tail of a running job's current invocation: job id -> {cursor, running, done}
  const _live = new Map();
  const _stick = new Map(); // row key -> false when the user scrolled the log up
  const LIVE_MS = 4000;
  let _lastMountAt = 0;
  let _needDayScroll = true;

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
    return String(n || '').replace(/^com\.amirfish\./, '').replace(/^com\.amir\./, '');
  }

  function isActive() {
    let t = null;
    try { t = localStorage.getItem('ccc-sidebar-tab'); } catch (_) {}
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
  // timers on Linux, everything (laptop + hermes-gcp) on a laptop. The Laptop
  // view is always empty on Linux, so it is never offered or honored there.
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

  function hueOf(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 360;
    return h;
  }

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

  function headerHtml() {
    const hosts = _data.hosts || {};
    const h = hosts.hermes || {};
    const host = activeHost();
    const sum = (_data.summary || {})[host] || {};
    const parts = [];
    if (host !== 'laptop') {
      if (h.status === 'online') parts.push('hermes-gcp online');
      else if (h.status === 'loading') parts.push('hermes-gcp loading');
      else {
        const age = h.age_s != null ? ' (data ' + rel(new Date(Date.now() - h.age_s * 1000).toISOString()).replace(' ago', '') + ' old)' : '';
        parts.push('hermes-gcp offline' + age);
      }
    } else {
      parts.push('Laptop');
    }
    const active = (sum.total || 0) - (sum.disabled || 0);
    parts.push(active + ' job' + (active === 1 ? '' : 's'));
    if (sum.failed) parts.push(sum.failed + ' failed');
    if (sum.stale) parts.push(sum.stale + ' stale');
    if (sum.disabled) parts.push(sum.disabled + ' disabled');
    const bad = (h.status === 'offline' && host !== 'laptop') || sum.failed;
    const segKeys = _data.local_host === 'hermes' ? ['hermes'] : ['hermes', 'laptop', 'all'];
    const seg = segKeys.map(k => {
      const label = k === 'all' ? 'All' : k === 'hermes' ? 'hermes-gcp' : 'Laptop';
      return '<button type="button" class="jobs-seg-btn' + (k === host ? ' is-active' : '') + '" data-jobs-host="' + k + '">' + label + '</button>';
    }).join('');
    const sortOpts = [['project', 'project'], ['recent', 'recent'], ['day', 'day']].map(x =>
      '<span class="grouping-opt' + (_sort === x[0] ? ' is-active' : '') + '" data-jobs-sort="' + x[0] + '">' + x[1] + '</span>').join('');
    return '<div class="jobs-head"><div class="jobs-seg">' + seg
      + '<button type="button" class="jobs-add-btn" data-jobs-add title="Create a scheduled job: an agent session writes and enables it">+ Add</button></div>'
      + '<div class="jobs-sub"><div class="jobs-summary' + (bad ? ' is-bad' : '') + '">' + esc(parts.join(' · ')) + '</div>'
      + '<span class="conv-grouping-toggle jobs-sort-toggle" title="Group by project, most recent run first, or the day\'s launches in clock order">' + sortOpts + '</span></div></div>';
  }

  // 24h strip: ticks at launch times (local), a "now" marker, weekday label for
  // weekly jobs, and a filled band for short intervals.
  function stripHtml(j) {
    const tl = j.timeline || {};
    let ticks = '';
    let label = '';
    if (tl.kind === 'interval' && tl.interval_s) {
      if (tl.interval_s <= 3600) {
        ticks = '<i class="job-band' + (tl.interval_s <= 900 ? ' dense' : '') + '"></i>';
      } else if (tl.interval_s >= 86400) {
        ticks = '<i class="job-tick" style="left:0"></i>';
        label = 'every ' + Math.round(tl.interval_s / 86400) + 'd';
      } else {
        for (let m = 0; m < 1440; m += tl.interval_s / 60) ticks += '<i class="job-tick" style="left:' + (m / 14.4).toFixed(2) + '%"></i>';
      }
    } else if (tl.kind === 'times') {
      ticks = (tl.minutes || []).map(m => '<i class="job-tick" style="left:' + (m / 14.4).toFixed(2) + '%"></i>').join('');
      if (tl.weekdays && tl.weekdays.length) label = tl.weekdays.length > 2 ? tl.weekdays[0] + '+' : tl.weekdays.join(',');
    }
    const n = new Date();
    const nowPct = ((n.getHours() * 60 + n.getMinutes()) / 14.4).toFixed(2);
    const tip = j.schedule + (j.next_run_at ? '\nNext: ' + fmtLocal(j.next_run_at) + ' (' + rel(j.next_run_at) + ')' : '');
    return '<span class="job-strip" title="' + esc(tip) + '"><span class="job-track">' + ticks
      + '<i class="job-now" style="left:' + nowPct + '%"></i></span>'
      + '<span class="job-wd">' + esc(label) + '</span></span>';
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

  function outcomeHtml(j) {
    if (!j.outcome) return '';
    const text = nodash(j.outcome);
    return '<span class="job-outcome' + (j.outcome_kind === 'output' ? ' is-raw' : '') + '" title="' + esc(text) + '">'
      + (j.outcome_kind === 'output' ? '<span class="job-out-label">output </span>' : '') + esc(text) + '</span>';
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
    const tipBits = ['Status: ' + j.status];
    if (j.last_run_at) tipBits.push('Last run: ' + fmtLocal(j.last_run_at));
    if (j.last_duration_s != null) tipBits.push('Duration: ' + dur(j.last_duration_s || 0.4));
    if (j.exit_code) tipBits.push('Exit code: ' + j.exit_code);
    const mid = (j.outcome_kind === 'summary' || !(j.tickets && j.tickets.length))
      ? outcomeHtml(j) : '<span class="job-chips">' + chipsHtml(j.tickets, 5) + '</span>';
    const slotCls = opt.state ? ' slot-' + opt.state : '';
    let h = '<div class="job-row st-' + esc(j.status) + (open ? ' is-open' : '') + slotCls + '" data-job-id="' + esc(j.id) + '" data-row-key="' + esc(key) + '">'
      + '<div class="job-line1">'
      + (opt.label ? '<span class="job-slot">' + esc(opt.label) + '</span>' : '')
      + '<span class="job-dot" title="' + esc(j.status) + '"></span>'
      + '<span class="job-name" title="' + esc(j.name) + '">' + esc(shortName(j.name)) + '</span>'
      + (running ? liveHtml(j) : '')
      + (activeHost() === 'all' ? '<span class="job-host">' + (j.host === 'hermes' ? 'hermes-gcp' : 'Laptop') + '</span>' : '')
      + '<span class="job-desc-inline" title="' + esc(nodash(j.description)) + '">' + esc(nodash(j.description)) + '</span>'
      + (_sort !== 'day' && j.next_run_at && j.enabled !== false ? '<span class="job-next" title="Next run: ' + esc(fmtLocal(j.next_run_at)) + ' (' + esc(rel(j.next_run_at)) + ')">Next ' + esc(nextLabel(j.next_run_at)) + '</span>' : '')
      + '</div>'
      + '<div class="job-line2">' + stripHtml(j) + '<span class="job-mid">' + mid + '</span>'
      + '<span class="job-when" title="' + esc(tipBits.join('\n')) + '">' + esc(j.last_run_at ? rel(j.last_run_at) : '') + '</span></div>';
    if (open) {
      const log = _logs.get(j.id);
      const lv = _live.get(j.id);
      const facts = [];
      facts.push(j.schedule);
      if (j.next_run_at) facts.push('next ' + rel(j.next_run_at));
      if (j.last_run_at) facts.push('last ' + fmtLocal(j.last_run_at));
      if (j.last_duration_s != null) facts.push('took ' + dur(j.last_duration_s || 0.4));
      if (j.exit_code) facts.push('exit ' + j.exit_code);
      const liveNote = running && lv && !lv.done ? '<div class="job-facts job-livenote"><i></i>Live, updating every few seconds</div>'
        : (lv && lv.done ? '<div class="job-facts job-livenote is-done">Finished. Showing the final output.</div>' : '');
      h += '<div class="job-detail">'
        + (j.description ? '<div class="job-desc">' + esc(nodash(j.description)) + '</div>' : '')
        + '<div class="job-facts">' + esc(facts.join(' · ')) + '</div>'
        + (j.repo_path ? '<div class="job-facts">' + esc(j.repo_path) + '</div>' : '')
        + (j.history && j.history.length ? '<div class="job-facts">7d ' + historyHtml(j.history) + '</div>' : '')
        + (j.outcome ? '<div class="job-outcome-full">' + (j.outcome_kind === 'output' ? '<span class="job-out-label">last output </span>' : '') + esc(nodash(j.outcome)) + '</div>' : '')
        + (j.tickets && j.tickets.length ? '<div class="job-chips">' + chipsHtml(j.tickets, 50) + '</div>' : '')
        + liveNote
        + '<pre class="job-log">' + (log == null ? 'Loading log...' : esc(log)) + '</pre></div>';
    }
    return h + '</div>';
  }

  function historyHtml(hist) {
    return '<span class="jobs-hist">' + hist.slice(-14).map(x => '<i class="' + (x.ok ? 'ok' : 'bad') + '" title="' + esc(x.at) + '"></i>').join('') + '</span>';
  }

  function lastRunMs(j) { const t = Date.parse(j.last_run_at || ''); return isNaN(t) ? 0 : t; }

  // Recent: flat, running first, then newest last run first.
  function recentHtml(jobs) {
    const list = jobs.slice().sort((a, b) => {
      const ra = a.status === 'running' ? 1 : 0, rb = b.status === 'running' ? 1 : 0;
      if (ra !== rb) return rb - ra;
      return lastRunMs(b) - lastRunMs(a);
    });
    return list.map(j => rowHtml(j)).join('');
  }

  // ── Day view ────────────────────────────────────────────────────────────
  const _WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  function firesToday(weekdays, dow) {
    if (!weekdays || !weekdays.length) return true;
    const name = _WD[dow];
    return weekdays.some(w => String(w).split(',').some(part => {
      part = part.trim();
      const r = part.split('-');
      if (r.length === 2 && _WD.indexOf(r[0]) >= 0 && _WD.indexOf(r[1]) >= 0) {
        const a = _WD.indexOf(r[0]), b = _WD.indexOf(r[1]);
        return a <= b ? (dow >= a && dow <= b) : (dow >= a || dow <= b);
      }
      return part === name;
    }));
  }

  function hhmm(min) { return String(Math.floor(min / 60)).padStart(2, '0') + ':' + String(min % 60).padStart(2, '0'); }
  function minuteOf(d) { return d.getHours() * 60 + d.getMinutes(); }
  function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }

  // Runs we know about (history + last run), as local Date + ok flag.
  function runsOf(j) {
    const out = (j.history || []).map(x => ({ d: new Date(x.at), ok: !!x.ok }));
    if (j.last_run_at) {
      const d = new Date(j.last_run_at);
      if (!out.some(x => Math.abs(x.d - d) < 120000)) out.push({ d: d, ok: j.status !== 'failed', running: j.status === 'running' });
    }
    return out.filter(x => !isNaN(x.d));
  }

  function slotState(j, slot, nowMin, today) {
    if (slot > nowMin) return 'future';
    // A run today that began at or after this slot and before the next 45 minutes.
    const hit = runsOf(j).find(r => sameDay(r.d, today) && minuteOf(r.d) >= slot && minuteOf(r.d) < slot + 45);
    if (!hit) return 'muted';
    return hit.running ? 'run' : (hit.ok ? 'ok' : 'bad');
  }

  function repeatLabel(j) {
    const sch = String(j.schedule || '');
    const m = sch.match(/^Hourly\s*:(\d\d)/i);
    if (m) return 'hourly at :' + m[1];
    return sch ? sch.charAt(0).toLowerCase() + sch.slice(1) : 'repeating';
  }

  function dayHtml(jobs) {
    const now = new Date();
    const nowMin = minuteOf(now), dow = now.getDay();
    const repeating = [], slots = [];
    jobs.forEach(j => {
      if (j.enabled === false) return;
      const tl = j.timeline || {};
      if (tl.kind === 'interval' && tl.interval_s && tl.interval_s < 86400) {
        repeating.push(j);
      } else if (tl.kind === 'interval' && j.next_run_at && sameDay(new Date(j.next_run_at), now)) {
        slots.push({ j: j, min: minuteOf(new Date(j.next_run_at)) });
      } else if (tl.kind === 'times' && firesToday(tl.weekdays, dow)) {
        (tl.minutes || []).forEach(m => slots.push({ j: j, min: m }));
      }
    });
    slots.sort((a, b) => a.min - b.min || String(a.j.name).localeCompare(String(b.j.name)));
    let h = '';
    if (repeating.length) {
      h += '<div class="jobs-day-band">Repeating</div>'
        + repeating.map(j => rowHtml(j, { key: j.id + '#rep', label: repeatLabel(j), state: 'rep' })).join('');
    }
    h += '<div class="jobs-day-band">Today, ' + esc(now.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })) + '</div>';
    let marked = false;
    const marker = '<div class="jobs-now" id="jobsNowMarker"><span class="jobs-now-arrow">&#9654;</span> now ' + esc(hhmm(nowMin)) + '<i></i></div>';
    slots.forEach(sl => {
      if (!marked && sl.min > nowMin) { h += marker; marked = true; }
      h += rowHtml(sl.j, { key: sl.j.id + '#' + sl.min, label: hhmm(sl.min), state: slotState(sl.j, sl.min, nowMin, now) });
    });
    if (!marked) h += marker;
    if (!slots.length) h += '<div class="jobs-empty">Nothing else scheduled today.</div>';
    return h;
  }

  function groupsHtml(jobs) {
    const groups = new Map(); // server order = most recent run first, so groups inherit that order
    jobs.forEach(j => {
      const k = j.project || 'Other';
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(j);
    });
    return Array.from(groups.entries()).map(([name, list]) => {
      const collapsed = _collapsed.has(name);
      return '<div class="conv-folder-group jobs-group">'
        + '<div class="conv-folder-group-header" style="--chip-hue:' + hueOf(name) + ';" role="button" tabindex="0" data-jobs-group="' + esc(name) + '">'
        + '<button type="button" class="conv-folder-group-arrow" tabindex="-1">' + (collapsed ? '▸' : '▾') + '</button>'
        + '<span class="conv-folder-group-chip">' + esc(name) + '</span>'
        + '<span class="conv-folder-group-count">' + list.length + '</span></div>'
        + (collapsed ? '' : list.map(rowHtml).join('')) + '</div>';
    }).join('');
  }

  function render() {
    const el = document.getElementById('sidebarJobsHost');
    if (!el) return;
    let html;
    if (!_data) {
      html = '<div class="jobs-empty">Loading jobs...</div>';
    } else {
      const jobs = visibleJobs();
      html = headerHtml()
        + (jobs.length ? '<div class="jobs-list">' + (_sort === 'day' ? dayHtml(jobs) : _sort === 'recent' ? recentHtml(jobs) : groupsHtml(jobs)) + '</div>'
          : '<div class="jobs-empty">No scheduled jobs' + (activeHost() === 'hermes' && (_data.hosts.hermes || {}).status !== 'online' ? ' (hermes-gcp unreachable)' : '') + '.</div>');
    }
    if (html !== _lastHtml || !el.firstChild) {
      const scroll = el.scrollTop;
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
      + '<option value="hermes"' + (host === 'hermes' ? ' selected' : '') + '>hermes-gcp</option>'
      + '<option value="laptop"' + (host === 'laptop' ? ' selected' : '') + '>Laptop</option></select></label>'
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
    if (ev.target.closest('[data-jobs-add]')) { openAddDialog(); return; }
    const liveEl = ev.target.closest('[data-job-live]');
    if (liveEl) { ev.stopPropagation(); focusLive(liveEl.getAttribute('data-job-live')); return; }
    if (ev.target.closest('a.job-live')) return; // WatchTower ticket link: the app's own handler opens it
    const sortEl = ev.target.closest('[data-jobs-sort]');
    if (sortEl) {
      _sort = sortEl.getAttribute('data-jobs-sort');
      try { localStorage.setItem(SORT_KEY, _sort); } catch (_) {}
      if (_sort === 'day') _needDayScroll = true;
      render();
      dayScroll();
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
    const grp = ev.target.closest('[data-jobs-group]');
    if (grp) {
      const g = grp.getAttribute('data-jobs-group');
      if (_collapsed.has(g)) _collapsed.delete(g); else _collapsed.add(g);
      try { localStorage.setItem('ccc-jobs-collapsed', JSON.stringify(Array.from(_collapsed))); } catch (_) {}
      render();
      return;
    }
    if (ev.target.closest('.job-detail') || ev.target.closest('a.job-chip')) return;
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
      if (d && d.ok) {
        _data = d;
        _lastFetch = Date.now();
        // A finished live tail is dropped once the feed agrees the job is no longer running.
        _live.forEach((lv, id) => { const j = jobById(id); if (lv.done && (!j || j.status !== 'running')) _live.delete(id); });
        updateBadge();
        render();
      }
    } catch (_) {
      // keep last-good data; retry next tick
    } finally {
      _inflight = false;
    }
  }

  // Day view: bring the "now" marker into view once per tab open, not on every
  // sidebar rebuild (that would fight the user's own scrolling).
  function dayScroll() {
    if (_sort !== 'day' || !_needDayScroll) return;
    const host = document.getElementById('sidebarJobsHost');
    const m = document.getElementById('jobsNowMarker');
    if (!host || !m) return;
    _needDayScroll = false;
    const head = host.querySelector('.jobs-head');
    const off = m.getBoundingClientRect().top - host.getBoundingClientRect().top - (head ? head.offsetHeight : 0) - 90;
    host.scrollTop = Math.max(0, host.scrollTop + off);
  }

  function mount() {
    const el = document.getElementById('sidebarJobsHost');
    if (!el) return;
    if (Date.now() - _lastMountAt > 8000) _needDayScroll = true;
    _lastMountAt = Date.now();
    _lastHtml = '';
    if (!el._jobsWired) { el.addEventListener('click', onClick); el._jobsWired = true; }
    render();
    dayScroll();
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
    else render(); // keep relative times / the Day view's now marker fresh
  }, 5000);
  setInterval(liveTick, LIVE_MS);

  window.CCCJobsTab = { mount: mount, attentionCount: attentionCount, openAddDialog: openAddDialog };
})();
