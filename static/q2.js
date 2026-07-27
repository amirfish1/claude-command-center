/* q2 — standalone three-column queue board (queues | tickets | ticket).
 *
 * Take 1 is read-only on purpose: it exists to validate the master/detail
 * layout before any mutating action is wired up. It shares no code with
 * app.js, so nothing here can regress the main dashboard. It only reads
 * three existing endpoints:
 *
 *   GET /api/queue/status        per-queue depth / state / repo
 *   GET /api/queue/list          every ticket, all queues
 *   GET /api/ux-fixes/item?ref=  one ticket, with its timeline
 */
(function () {
  'use strict';

  var POLL_MS = 5000;
  var CLOSED_CAP = 50;

  var state = {
    queues: [],
    items: [],
    queue: '',
    ref: '',
    detail: null,       // full item payload for state.ref
    showClosed: false,
    search: '',
    offline: false,
  };

  // ── helpers ──────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function $(id) { return document.getElementById(id); }

  function relTime(iso) {
    if (!iso) return '';
    var diff = Date.now() - Date.parse(iso);
    if (!isFinite(diff)) return '';
    if (diff < 0) return 'just now';
    var s = Math.floor(diff / 1000);
    if (s < 60) return s + 's ago';
    var m = Math.floor(s / 60);
    if (m < 60) return m + 'm ago';
    var h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }

  function projectKey(v) { return String(v || '').trim().toUpperCase(); }

  // Effective status, matching the main dashboard's collapse: a claimed but
  // still-"open" ticket reads as in progress, and needs_input outranks both.
  function statusOf(it) {
    if (it && it.needs_input) return 'blocked';
    var raw = String((it && it.status) || 'open');
    if (raw === 'open' && it && (it.claimed_by || it.claimed_at || it.claimed_session_id)) return 'in_progress';
    return raw;
  }

  // Ticket title: the note/text with the annotation boilerplate stripped, so
  // rows show the human sentence instead of "Fix the following UX issue…".
  function titleOf(item) {
    if (!item) return '';
    var candidates = [item.note, item.text, item.title];
    for (var i = 0; i < candidates.length; i++) {
      var lines = String(candidates[i] || '').split(/\r?\n/);
      var kept = [];
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j].trim();
        if (!line) continue;
        if (line === 'Fix the following UX issue based on this annotation:') continue;
        if (line.indexOf('Annotation:') === 0) line = line.slice('Annotation:'.length).trim();
        if (line) kept.push(line);
      }
      if (kept.length) return kept.join('\n');
    }
    return item.ref || '';
  }

  function sessionOf(item) {
    if (!item) return '';
    if (item.claimed_session_id) return String(item.claimed_session_id);
    var tl = Array.isArray(item.timeline) ? item.timeline : [];
    for (var i = tl.length - 1; i >= 0; i--) {
      var by = tl[i] && tl[i].by;
      if (by && typeof by === 'object' && by.session_id) return String(by.session_id);
    }
    return '';
  }

  // The server already classifies every queue (ccc_server/queue_events.py:422)
  // into stuck / draining / backlog. Read that instead of re-deriving a second
  // opinion here. The only thing added on top is splitting the server's
  // "draining" by whether a worker is on it *right now*: "auto-drain is armed"
  // and "a worker is running" are different facts and the old single label
  // conflated them, which is why queues read as draining while nothing ran.
  function queueState(q) {
    var srv = String(q.state || '');
    var workers = Number(q.workers || 0);
    var claimable = Number(q.claimable != null ? q.claimable : (q.depth || 0));
    if (srv === 'stuck') {
      return { k: 'stuck', label: 'stuck',
        tip: 'Auto-drain is on and there is claimable work, but no worker is running. This one needs a look.' };
    }
    if (workers > 0) {
      return { k: 'working', label: 'working',
        tip: workers + ' worker' + (workers === 1 ? '' : 's') + ' draining this queue right now.' };
    }
    if (srv === 'draining') {
      return claimable > 0
        ? { k: 'ready', label: 'ready',
            tip: 'Auto-drain is on and ' + claimable + ' ticket' + (claimable === 1 ? ' is' : 's are')
                 + ' claimable. A worker picks them up on the next sweep.' }
        : { k: 'clear', label: 'clear',
            tip: 'Auto-drain is on and there is nothing left to claim.' };
    }
    if (q.auto_drain) {
      return { k: 'parked', label: 'parked',
        tip: 'Auto-drain is on, but every open ticket is filtered out by this queue’s claim types. Nothing is claimable.' };
    }
    return { k: 'manual', label: 'manual',
      tip: 'Auto-drain is off. This queue is a parking lot — nothing runs until you start a worker.' };
  }

  function itemsForQueue(queue) {
    var key = projectKey(queue);
    if (!key) return [];
    return state.items.filter(function (it) { return projectKey(it && it.project) === key; });
  }

  // ── fetch ────────────────────────────────────────────────────────────────
  async function getJson(url) {
    var res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  async function refresh() {
    try {
      var results = await Promise.all([
        getJson('/api/queue/status'),
        getJson('/api/queue/list'),
      ]);
      state.queues = (results[0] && results[0].queues) || [];
      state.items = (results[1] && results[1].items) || [];
      state.offline = false;
    } catch (e) {
      // Keep the last good render on screen and flag it, rather than
      // blanking the board every time a poll misses.
      state.offline = true;
      renderChrome();
      return;
    }
    // Default selection: first queue with open work, else the first queue.
    if (!state.queue && state.queues.length) {
      var withWork = state.queues.filter(function (q) { return q.depth > 0; });
      state.queue = (withWork[0] || state.queues[0]).queue;
    }
    renderAll();
  }

  async function loadDetail(ref) {
    if (!ref) { state.detail = null; renderDetail(); return; }
    renderDetail();  // paint the loading state immediately
    try {
      var data = await getJson('/api/ux-fixes/item?ref=' + encodeURIComponent(ref));
      if (state.ref !== ref) return;  // user moved on while this was in flight
      state.detail = (data && data.item) || null;
    } catch (e) {
      if (state.ref !== ref) return;
      state.detail = null;
    }
    renderDetail();
  }

  // ── render: chrome ───────────────────────────────────────────────────────
  function renderChrome() {
    var stale = $('q2Stale');
    if (stale) stale.hidden = !state.offline;
  }

  // ── render: column 1, queues ─────────────────────────────────────────────
  function renderQueues() {
    var host = $('q2Queues');
    if (!host) return;
    $('q2QueueCount').textContent = state.queues.length ? String(state.queues.length) : '';

    if (!state.queues.length) {
      host.innerHTML = '<div class="q2-empty">'
        + '<div class="q2-empty-title">No queues yet</div>'
        + 'A queue is a named backlog that WatchTower workers drain. Create one from any terminal:'
        + '<br><code class="q2-empty-code">wt config -q MYQUEUE --auto-drain on</code>'
        + '</div>';
      return;
    }

    var selected = projectKey(state.queue);
    host.innerHTML = state.queues.map(function (q) {
      var st = queueState(q);
      var isSel = projectKey(q.queue) === selected;
      return '<button type="button" class="q2-qrow' + (isSel ? ' is-selected' : '') + '"'
        + ' data-q2-queue="' + esc(q.queue) + '">'
        + '<span class="q2-qrow-head">'
        + '<span class="q2-qname">' + esc(q.queue) + '</span>'
        + '<span class="q2-pill is-' + esc(st.k) + '" title="' + esc(st.tip || '') + '">'
        + '<span class="q2-pill-dot" aria-hidden="true"></span>' + esc(st.label) + '</span>'
        + '</span>'
        + '<span class="q2-qrow-counts">'
        + '<span><b>' + (q.depth || 0) + '</b> open</span>'
        + '<span><b>' + (q.workers || 0) + '</b> wip</span>'
        + '<span><b>' + (q.closed || 0) + '</b> done</span>'
        + '</span>'
        + (q.repo_path ? '<span class="q2-qrepo" title="' + esc(q.repo_path) + '">' + esc(q.repo_path) + '</span>' : '')
        + '</button>';
    }).join('');
  }

  // ── render: column 2, tickets ────────────────────────────────────────────
  function ticketRow(it) {
    var st = statusOf(it);
    var ref = it.ref || '';
    var title = titleOf(it).split('\n')[0];
    var ageSrc = st === 'closed'
      ? (it.closed_at || it.updated_at || it.created_at)
      : (it.updated_at || it.created_at);
    return '<button type="button" class="q2-trow is-' + esc(st) + (ref === state.ref ? ' is-selected' : '') + '"'
      + ' data-q2-ref="' + esc(ref) + '">'
      + '<span class="q2-tdot" aria-hidden="true"></span>'
      + '<span class="q2-tref">' + esc(ref) + '</span>'
      + '<span class="q2-ttitle">' + esc(title) + '</span>'
      + (sessionOf(it) ? '<span class="q2-tsess" title="An agent session is attached">&#9679;</span>' : '')
      + '<span class="q2-tage" title="' + esc(ageSrc || '') + '">' + esc(relTime(ageSrc)) + '</span>'
      + '</button>';
  }

  function renderTickets() {
    var host = $('q2Tickets');
    if (!host) return;

    $('q2TicketsTitle').textContent = state.queue || 'Tickets';
    var closedBtn = $('q2ClosedBtn');
    if (closedBtn) {
      closedBtn.setAttribute('aria-pressed', state.showClosed ? 'true' : 'false');
      closedBtn.textContent = state.showClosed ? 'Hide closed' : 'Show closed';
    }

    if (!state.queue) {
      host.innerHTML = '<div class="q2-empty">Pick a queue on the left.</div>';
      $('q2TicketCount').textContent = '';
      return;
    }

    var mine = itemsForQueue(state.queue);
    var needle = state.search.trim().toLowerCase();
    if (needle) {
      mine = mine.filter(function (it) {
        return (String(it.ref || '') + ' ' + titleOf(it)).toLowerCase().indexOf(needle) !== -1;
      });
    }

    var openish = mine.filter(function (it) { return statusOf(it) !== 'closed'; });
    var closed = mine.filter(function (it) { return statusOf(it) === 'closed'; });

    // Blocked first (a human is the bottleneck), then in-progress, then open.
    var rank = { blocked: 0, in_progress: 1, open: 2 };
    openish.sort(function (a, b) {
      var d = (rank[statusOf(a)] || 3) - (rank[statusOf(b)] || 3);
      if (d) return d;
      return Date.parse(b.updated_at || b.created_at || 0) - Date.parse(a.updated_at || a.created_at || 0);
    });
    closed.sort(function (a, b) {
      return Date.parse(b.closed_at || b.updated_at || 0) - Date.parse(a.closed_at || a.updated_at || 0);
    });

    $('q2TicketCount').textContent = openish.length
      ? openish.length + ' open' + (closed.length ? ' · ' + closed.length + ' closed' : '')
      : (closed.length ? closed.length + ' closed' : '');

    if (!openish.length && !(state.showClosed && closed.length)) {
      host.innerHTML = '<div class="q2-empty">'
        + '<div class="q2-empty-title">'
        + (needle ? 'No match' : (closed.length ? 'All clear in ' + esc(state.queue) : 'No tickets in ' + esc(state.queue)))
        + '</div>'
        + (closed.length && !needle ? 'Every ticket here is closed. Use Show closed to review them.' : '')
        + '</div>';
      return;
    }

    var shownClosed = state.showClosed ? closed.slice(0, CLOSED_CAP) : [];
    var html = openish.map(ticketRow).join('');
    if (shownClosed.length) {
      var label = 'Closed' + (closed.length > shownClosed.length
        ? ' (newest ' + shownClosed.length + ' of ' + closed.length + ')' : '');
      html += '<div class="q2-group-label">' + esc(label) + '</div>' + shownClosed.map(ticketRow).join('');
    }
    host.innerHTML = html;
  }

  // ── render: column 3, ticket detail ──────────────────────────────────────
  function timelineHtml(item) {
    var verbs = {
      filed: 'Filed', claim: 'Claimed', close: 'Closed', reopen: 'Reopened',
      comment: 'Comment', answer: 'Answered', block: 'Blocked', edit: 'Edited',
    };
    var tl = Array.isArray(item.timeline) ? item.timeline : [];
    if (!tl.length) return '<div class="q2-empty">No activity yet.</div>';
    return '<div class="q2-tl">' + tl.map(function (ev) {
      var by = ev.by || {};
      var who = by.worker || by.kind || '';
      var body = ev.text || ev.note || ev.answer || ev.question || '';
      return '<div class="q2-tl-row">'
        + '<span class="q2-tl-dot" aria-hidden="true"></span>'
        + '<div>'
        + '<span class="q2-tl-verb">' + esc(verbs[ev.event] || ev.event || 'Event') + '</span> '
        + (ev.at ? '<span class="q2-tl-time" title="' + esc(ev.at) + '">' + esc(relTime(ev.at)) + '</span> ' : '')
        + (who ? '<span class="q2-tl-who">' + esc(String(who).slice(0, 24)) + '</span>' : '')
        + (body ? '<div class="q2-tl-body">' + esc(body) + '</div>' : '')
        + '</div></div>';
    }).join('') + '</div>';
  }

  function propRow(k, v, mono) {
    if (!v) return '';
    return '<div class="q2-prop-k">' + esc(k) + '</div>'
      + '<div class="q2-prop-v' + (mono ? ' q2-mono' : '') + '">' + esc(v) + '</div>';
  }

  function renderDetail() {
    var host = $('q2Detail');
    if (!host) return;

    if (!state.ref) {
      host.innerHTML = '<div class="q2-empty">'
        + '<div class="q2-empty-title">No ticket selected</div>'
        + 'Pick a ticket in the middle column to see its prompt, activity, and properties here.'
        + '</div>';
      return;
    }

    var item = state.detail;
    if (!item || item.ref !== state.ref) {
      host.innerHTML = '<div class="q2-empty">Loading ' + esc(state.ref) + '&hellip;</div>';
      return;
    }

    var st = statusOf(item);
    var full = titleOf(item);
    // Headline is the FIRST line only. Tickets are routinely multi-paragraph
    // prompts; rendering all of it at h1 weight made the pane a wall of bold
    // that duplicated the Full prompt block verbatim.
    var title = full.split('\n')[0];
    var body = full.slice(title.length).trim();
    // Show the raw prompt whenever it carries more than the headline already does.
    var prompt = (item.text && item.text.trim()) || body;
    var showPrompt = !!prompt && prompt !== title.trim();
    var sid = sessionOf(item);

    host.innerHTML = ''
      + '<div class="q2-detail-head">'
      + '<span class="q2-detail-ref">' + esc(item.ref) + '</span>'
      + '<span class="q2-status is-' + esc(st) + '">' + esc(st.replace('_', ' ')) + '</span>'
      + (item.priority ? '<span class="q2-chip is-' + esc(item.priority) + '">' + esc(item.priority) + '</span>' : '')
      + (item.type ? '<span class="q2-chip">' + esc(item.type) + '</span>' : '')
      + (item.lane ? '<span class="q2-chip">' + esc(item.lane) + '</span>' : '')
      + '</div>'
      + '<h1 class="q2-detail-title">' + esc(title) + '</h1>'
      + (showPrompt
        ? '<section class="q2-sec"><div class="q2-sec-label">Full prompt</div>'
          + '<pre class="q2-pre">' + esc(prompt) + '</pre></section>'
        : '')
      + '<section class="q2-sec"><div class="q2-sec-label">Activity</div>' + timelineHtml(item) + '</section>'
      + '<section class="q2-sec"><div class="q2-sec-label">Properties</div>'
      + '<div class="q2-props">'
      + propRow('Queue', item.project)
      + propRow('Worker', item.claimed_by ? String(item.claimed_by).slice(0, 28) : 'unassigned', !!item.claimed_by)
      + propRow('Session', sid ? sid.slice(0, 28) : '', true)
      + propRow('Created', item.created_at ? relTime(item.created_at) : '')
      + propRow('Claimed', item.claimed_at ? relTime(item.claimed_at) : '')
      + propRow('Closed', item.closed_at ? relTime(item.closed_at) : '')
      + propRow('Source', item.source)
      + propRow('Repo', item.repo_path, true)
      + propRow('URL', item.url)
      + '</div></section>';
  }

  function renderAll() {
    renderChrome();
    renderQueues();
    renderTickets();
    // The detail pane owns its own fetch; only repaint from cache here so a
    // 5s poll can't flicker the pane the user is reading.
    renderDetail();
  }

  // ── events ───────────────────────────────────────────────────────────────
  function selectQueue(name) {
    if (projectKey(name) === projectKey(state.queue)) return;
    state.queue = name;
    state.ref = '';
    state.detail = null;
    state.search = '';
    var search = $('q2Search');
    if (search) search.value = '';
    renderAll();
  }

  function selectTicket(ref) {
    if (ref === state.ref) return;
    state.ref = ref;
    state.detail = null;
    renderQueues();
    renderTickets();
    loadDetail(ref);
  }

  document.addEventListener('click', function (e) {
    var qBtn = e.target.closest('[data-q2-queue]');
    if (qBtn) { selectQueue(qBtn.getAttribute('data-q2-queue')); return; }
    var tBtn = e.target.closest('[data-q2-ref]');
    if (tBtn) { selectTicket(tBtn.getAttribute('data-q2-ref')); return; }
    if (e.target.closest('#q2ClosedBtn')) { state.showClosed = !state.showClosed; renderTickets(); return; }
    if (e.target.closest('#q2ThemeBtn')) { toggleTheme(); return; }
  });

  var searchInput = $('q2Search');
  if (searchInput) {
    searchInput.addEventListener('input', function () {
      state.search = searchInput.value || '';
      renderTickets();
    });
  }

  function toggleTheme() {
    var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('ccc-q2-theme', next); } catch (_) {}
    var btn = $('q2ThemeBtn');
    if (btn) btn.textContent = next === 'light' ? 'Dark' : 'Light';
  }

  (function initTheme() {
    var saved = 'dark';
    try { saved = localStorage.getItem('ccc-q2-theme') || 'dark'; } catch (_) {}
    document.documentElement.setAttribute('data-theme', saved);
    var btn = $('q2ThemeBtn');
    if (btn) btn.textContent = saved === 'light' ? 'Dark' : 'Light';
  })();

  // ── column resizers ──────────────────────────────────────────────────────
  // Both handles drive a CSS custom property on :root, so the grid is the only
  // thing that reacts and no render pass is needed while dragging.
  var COLS = {
    queues:  { varName: '--q2-queues-w',  key: 'ccc-q2-w-queues',  def: 260, min: 170, max: 560 },
    tickets: { varName: '--q2-tickets-w', key: 'ccc-q2-w-tickets', def: 440, min: 260, max: 900 }
  };

  // `desired` is what the user actually asked for; the applied width is that
  // value clamped to the current viewport. Keeping the two separate is what
  // lets a width survive a narrow-then-widen round trip instead of ratcheting
  // permanently smaller.
  var desired = { queues: COLS.queues.def, tickets: COLS.tickets.def };

  function applyColWidths() {
    ['queues', 'tickets'].forEach(function (which) {
      var spec = COLS[which];
      var other = which === 'queues' ? 'tickets' : 'queues';
      // Reserve room for the other column (at its own minimum) and the detail
      // pane, so the two fixed tracks can never squeeze detail out of view.
      var roomCap = window.innerWidth - COLS[other].min - 320;
      var cap = Math.max(spec.min, Math.min(spec.max, roomCap));
      var w = Math.round(Math.min(cap, Math.max(spec.min, desired[which])));
      document.documentElement.style.setProperty(spec.varName, w + 'px');
    });
  }

  function setColWidth(which, px, persist) {
    if (!COLS[which]) return;
    desired[which] = px;
    applyColWidths();
    if (persist) {
      try { localStorage.setItem(COLS[which].key, String(Math.round(px))); } catch (_) {}
    }
  }

  function readColWidth(which) { return desired[which]; }

  (function initColWidths() {
    Object.keys(COLS).forEach(function (which) {
      var saved = null;
      try { saved = localStorage.getItem(COLS[which].key); } catch (_) {}
      var n = parseFloat(saved);
      if (!isNaN(n)) desired[which] = n;
    });
    applyColWidths();
  })();

  document.querySelectorAll('[data-q2-resize]').forEach(function (handle) {
    var which = handle.getAttribute('data-q2-resize');
    var spec = COLS[which];
    if (!spec) return;

    handle.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      var startX = e.clientX;
      var startW = readColWidth(which);
      handle.setPointerCapture(e.pointerId);
      handle.classList.add('is-dragging');
      document.body.classList.add('q2-resizing');

      function onMove(ev) { setColWidth(which, startW + (ev.clientX - startX), false); }
      function onUp() {
        handle.removeEventListener('pointermove', onMove);
        handle.removeEventListener('pointerup', onUp);
        handle.removeEventListener('pointercancel', onUp);
        handle.classList.remove('is-dragging');
        document.body.classList.remove('q2-resizing');
        setColWidth(which, readColWidth(which), true);
      }
      handle.addEventListener('pointermove', onMove);
      handle.addEventListener('pointerup', onUp);
      handle.addEventListener('pointercancel', onUp);
    });

    handle.addEventListener('dblclick', function () { setColWidth(which, spec.def, true); });

    handle.addEventListener('keydown', function (e) {
      var step = e.shiftKey ? 40 : 12;
      if (e.key === 'ArrowLeft') { e.preventDefault(); setColWidth(which, readColWidth(which) - step, true); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); setColWidth(which, readColWidth(which) + step, true); }
      else if (e.key === 'Home') { e.preventDefault(); setColWidth(which, spec.def, true); }
    });
  });

  // A narrowed window can leave saved widths wider than the viewport. Re-clamp
  // without persisting, so the user's chosen width returns when they widen back.
  window.addEventListener('resize', function () {
    setColWidth('queues', readColWidth('queues'), false);
    setColWidth('tickets', readColWidth('tickets'), false);
  });

  // ── boot ─────────────────────────────────────────────────────────────────
  refresh();
  setInterval(function () {
    if (document.hidden) return;
    refresh();
  }, POLL_MS);
})();
