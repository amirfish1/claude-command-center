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
    projects: {},       // queue name → per-project health row (why it's stuck)
    workers: [],        // live WatchTower workers — the only proof a claim is real
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

  // Elapsed seconds → "3h ago". Split out because the queue health rows carry
  // an age in seconds while tickets carry timestamps; both must format alike.
  function agoFromSeconds(s) {
    if (s == null || !isFinite(s)) return '';
    if (s < 0) return 'just now';
    s = Math.floor(s);
    if (s < 60) return s + 's ago';
    var m = Math.floor(s / 60);
    if (m < 60) return m + 'm ago';
    var h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }

  function relTime(iso) {
    if (!iso) return '';
    var diff = Date.now() - Date.parse(iso);
    if (!isFinite(diff)) return '';
    return agoFromSeconds(diff / 1000);
  }

  function projectKey(v) { return String(v || '').trim().toUpperCase(); }

  // Claim model, matching the main dashboard (static/app.js:35523). A ticket's
  // claim fields are just a record; they outlive the worker that wrote them.
  // The only proof that work is happening is a LIVE WatchTower worker on the
  // same queue whose session or id matches the claim.
  function hasClaimMetadata(it) {
    return !!(it && (it.claimed_by || it.claimed_at || it.claimed_session_id));
  }

  function hasLiveClaim(it) {
    if (!hasClaimMetadata(it)) return false;
    var project = projectKey(it && it.project);
    var claimedSession = String((it && it.claimed_session_id) || '').trim();
    var claimedBy = String((it && it.claimed_by) || '').trim();
    return (state.workers || []).some(function (w) {
      if (project && projectKey(w.queue) !== project) return false;
      var session = String(w.session_id || '').trim();
      var id = String(w.worker_id || '').trim();
      return (claimedSession && claimedSession === session)
        || (claimedBy && (claimedBy === session || claimedBy === id));
    });
  }

  // A claim whose worker is gone. NOT an error: the ticket is simply back to
  // open and the store never cleared the fields. Main CCC demotes it to open
  // rather than flagging it, and so do we.
  function isStaleClaim(it) {
    var raw = String((it && it.status) || 'open');
    return raw === 'in_progress' ? !hasLiveClaim(it)
      : hasClaimMetadata(it) && !hasLiveClaim(it);
  }

  // claimed_by with no claimed_session_id: a claim label with no durable id, so
  // liveness can never be proven either way. Worth marking, not worth alarming.
  function isUnverifiedClaim(it) {
    var claimedBy = String((it && it.claimed_by) || '').trim();
    var claimedSession = String((it && it.claimed_session_id) || '').trim();
    return !!claimedBy && !claimedSession && !hasLiveClaim(it);
  }

  // Effective status. Closed wins over everything: needs_input is not cleared
  // on close, so testing it first made every closed-but-once-blocked ticket
  // read as blocked forever.
  function statusOf(it) {
    var raw = String((it && it.status) || 'open');
    if (raw === 'closed') return 'closed';
    if (it && it.needs_input) return 'blocked';
    if (raw === 'in_progress' && isStaleClaim(it)) return 'open';
    if (raw === 'open' && hasLiveClaim(it)) return 'in_progress';
    return raw;
  }

  function isLiveWip(it) { return statusOf(it) === 'in_progress' && hasLiveClaim(it); }

  // The store's status key is `blocked`; the word shown to a person is always
  // "needs input". Keep the key internal (it drives classes and sorting) and
  // translate at every render point, so the raw key can never leak into copy.
  var STATUS_LABEL = {
    blocked: 'needs input',
    in_progress: 'in progress',
    open: 'open',
    closed: 'closed'
  };
  function statusLabel(st) {
    return STATUS_LABEL[st] || String(st || '').replace(/_/g, ' ');
  }

  function unresolvedNotes(it) {
    return (it && it.resolution && Array.isArray(it.resolution.unresolved))
      ? it.resolution.unresolved.filter(Boolean) : [];
  }

  // Operational bucket, the order the main dashboard sorts by
  // (static/app.js:35571): live work, then things needing a human, then
  // follow-ups, then claimable work, then clean closes, then inert rows.
  var PRIO_RANK = { p0: 0, p1: 1, p2: 2, p3: 3 };
  function prioRank(it) {
    if (it && PRIO_RANK[it.priority] != null) return PRIO_RANK[it.priority];
    return (it && it.lane === 'express') ? 0 : 2;
  }
  function unready(it) {
    return (it && (it.readiness === 'needs-shaping' || it.readiness === 'needs-spec')) ? 1 : 0;
  }
  function isWaitingToDrain(it) {
    if (statusOf(it) !== 'open') return false;
    return !isStaleClaim(it) && it.claimable !== false
      && it.watchtower_runnable !== false && !unready(it);
  }
  function operationalBucket(it) {
    var st = statusOf(it);
    if (isLiveWip(it)) return 0;
    if (st === 'blocked') return 1;
    if (String(it.status) === 'closed' && unresolvedNotes(it).length) return 2;
    if (isWaitingToDrain(it)) return 3;
    if (st === 'closed') return 4;
    return 5;
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

  // Per-queue facts derived in ONE pass over the item list. renderQueues runs
  // every poll across every queue, so filtering the full item array per queue
  // would be O(queues x items) on each tick.
  function queueFacts() {
    var by = {};
    function bucket(k) {
      if (!by[k]) by[k] = { github: 0, local: 0, needsInput: 0, wip: 0, waiting: 0 };
      return by[k];
    }
    (state.items || []).forEach(function (it) {
      var k = projectKey(it && it.project);
      if (!k || k === '?') return;
      var b = bucket(k);
      if (String(it.source || '') === 'github' || it.github_repo) b.github++; else b.local++;
      var st = statusOf(it);
      if (st === 'closed') return;
      if (it.needs_input) b.needsInput++;
      else if (st === 'in_progress') b.wip++;
      // Waiting = open, unclaimed, and something a worker is actually allowed
      // to pick up. `claimable === false` marks GitHub issues without the
      // queue's label: real inventory, but nothing will ever claim them.
      else if (st === 'open' && it.claimable !== false) b.waiting++;
    });
    return by;
  }

  // One counts line, shared by the queue rows and the ticket-list header, so
  // the two can never disagree about how many tickets a queue has. Plain text,
  // fixed order, each number labelled. Blocked tickets are counted separately
  // from open ones — folding them together was what made the header claim
  // "5 open" for a queue with 4 open and 1 blocked.
  function countsLine(f, done) {
    f = f || {};
    var parts = [];
    if (f.needsInput) {
      parts.push('<span class="q2-n is-blocked" title="Blocked waiting on a human answer">'
        + '<b>' + f.needsInput + '</b> needs input</span>');
    }
    if (f.wip) {
      parts.push('<span class="q2-n is-wip" title="Claimed by a worker and in progress">'
        + '<b>' + f.wip + '</b> wip</span>');
    }
    parts.push('<span class="q2-n" title="Open and unclaimed"><b>' + (f.waiting || 0) + '</b> open</span>');
    parts.push('<span class="q2-n" title="Closed, all time"><b>' + (done || 0) + '</b> done</span>');
    return parts.join('<span class="q2-n-sep" aria-hidden="true">·</span>');
  }

  // Row 1 carries only configuration (what this queue IS); the counts line
  // below carries state (what is in it right now).
  function queueChips(q) {
    return [q.auto_drain
      ? { k: 'auto-on', label: 'auto', tip: 'Auto-drain is ON. WatchTower spawns workers for this queue on its own.' }
      : { k: 'auto-off', label: 'manual', tip: 'Auto-drain is OFF. Nothing runs here until you start a worker.' }];
  }

  // "stuck" on its own tells the user nothing actionable. The server sets it
  // when a queue has claimable work, auto-drain on, and no live worker — but
  // there are two very different causes, and the fix differs per cause.
  // Rendered as visible text on the row, not a tooltip: a cause you have to
  // hover to discover is a cause nobody reads. Kept to one short sentence so
  // it fits the column at its default width.
  function stuckWhy(q) {
    var hr = (state.projects || {})[projectKey(q.queue)] || {};
    var sid = hr.fixer_session_id ? String(hr.fixer_session_id).slice(0, 8) : '';
    if (!hr.fixer_session_id) {
      return 'No worker ever claimed here. Nothing was spawned.';
    }
    if (!hr.fixer_live) {
      return 'Worker ' + sid + ' is gone. It died before draining the queue.';
    }
    return 'Worker ' + sid + ' is alive but has closed nothing in 10+ min. Hung.';
  }

  // GitHub mark, inline so the page keeps its zero-asset contract.
  var GH_MARK = '<svg class="q2-gh" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">'
    + '<path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38'
    + ' 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53'
    + '.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95'
    + ' 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27'
    + 'c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95'
    + '.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z"/>'
    + '</svg>';

  // Repo path shares the name line now, so it has to be short. Keep the last
  // two segments; the full path stays in the title attribute.
  function shortPath(p) {
    var parts = String(p || '').replace(/\/+$/, '').split('/').filter(Boolean);
    if (parts.length <= 2) return parts.join('/');
    return parts.slice(-2).join('/');
  }

  // Sort order: the four things the user asked to float up, most-actionable
  // first. `stuck` still outranks everything — it is the only state that means
  // something is broken rather than merely busy.
  function queueRank(q, f) {
    f = f || {};
    return [
      q.state === 'stuck' ? 0 : 1,
      f.needsInput ? 0 : 1,
      f.wip ? 0 : 1,
      q.auto_drain ? 0 : 1,
      f.github ? 0 : 1,
      -(q.depth || 0)
    ];
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
      // Per-project health rows carry WHY a queue is stuck (is there a fixer at
      // all, is it alive, has it closed anything lately). The queue rows only
      // carry the boolean, which is useless to a user staring at a red flag.
      state.projects = {};
      ((results[0] && results[0].projects) || []).forEach(function (r) {
        state.projects[projectKey(r && r.project)] = r;
      });
      state.workers = (((results[0] && results[0].wt_workers) || [])
        .filter(function (w) { return w && w.alive !== false; }));
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
    var facts = queueFacts();
    var ordered = state.queues.slice().sort(function (a, b) {
      var ra = queueRank(a, facts[projectKey(a.queue)]);
      var rb = queueRank(b, facts[projectKey(b.queue)]);
      for (var i = 0; i < ra.length; i++) {
        if (ra[i] !== rb[i]) return ra[i] - rb[i];
      }
      return String(a.queue).localeCompare(String(b.queue));
    });

    host.innerHTML = ordered.map(function (q) {
      var f = facts[projectKey(q.queue)];
      var isSel = projectKey(q.queue) === selected;
      var chips = queueChips(q).map(function (c) {
        return '<span class="q2-chip is-' + esc(c.k) + '" title="' + esc(c.tip || '') + '">'
          + esc(c.label) + '</span>';
      }).join('');
      return '<button type="button" class="q2-qrow' + (isSel ? ' is-selected' : '')
        + (q.state === 'stuck' ? ' is-stuck' : '') + '"'
        + ' data-q2-queue="' + esc(q.queue) + '">'
        // Row 1 — identity and configuration.
        + '<span class="q2-qrow-head">'
        + '<span class="q2-qname">' + esc(q.queue) + '</span>'
        + ((f && f.github) ? '<span class="q2-gh-wrap" title="Backed by GitHub issues'
            + ((f.local) ? ' (plus ' + f.local + ' local ticket' + (f.local === 1 ? '' : 's') + ')' : '')
            + '." aria-label="GitHub-backed queue">' + GH_MARK + '</span>' : '')
        + (q.state === 'stuck' ? '<span class="q2-stuck-flag">stuck</span>' : '')
        + (q.repo_path ? '<span class="q2-qrepo" title="' + esc(q.repo_path) + '">' + esc(shortPath(q.repo_path)) + '</span>' : '')
        + '<span class="q2-qrow-config">' + chips + '</span>'
        + '</span>'
        // Row 2 — the counts, same renderer the ticket header uses, plus the
        // last-touch age on the right (the queue's newest ticket activity,
        // same figure the main dashboard's health strip shows).
        + '<span class="q2-counts">' + countsLine(f, q.closed)
        + (q.last_activity_seconds != null
            ? '<span class="q2-qage" title="Most recent ticket activity in this queue">'
              + esc(agoFromSeconds(q.last_activity_seconds).replace(/\s*ago$/, '')) + '</span>'
            : '')
        + '</span>'
        + (q.state === 'stuck' ? '<span class="q2-qwhy">' + esc(stuckWhy(q)) + '</span>' : '')
        + '</button>';
    }).join('');
  }

  // ── render: column 2, tickets ────────────────────────────────────────────
  function ticketRow(it) {
    var st = statusOf(it);
    var ref = it.ref || '';
    var title = titleOf(it).split('\n')[0];
    var stale = isStaleClaim(it);
    var unverified = isUnverifiedClaim(it);
    var unresolved = String(it.status) === 'closed' && unresolvedNotes(it).length > 0;
    // Same source the main dashboard uses: last touch, or close time once
    // closed. Rendered without the trailing " ago", as it is there.
    var ageSrc = st === 'closed'
      ? (it.closed_at || it.updated_at || it.created_at)
      : (it.updated_at || it.created_at);
    var age = relTime(ageSrc).replace(/\s*ago$/, '');
    var dotTitle = unresolved ? 'closed, unresolved follow-up'
      : unverified ? 'claimed by ' + String(it.claimed_by || '') + ', liveness unverified'
      : (stale && st !== 'blocked') ? 'stale claim, no live worker is on this'
      : statusLabel(st);
    return '<button type="button" class="q2-trow is-' + esc(st)
      + (ref === state.ref ? ' is-selected' : '')
      + (stale ? ' is-stale-claim' : '')
      + (unverified ? ' is-unverified-claim' : '')
      + (unresolved ? ' has-unresolved' : '') + '"'
      + ' data-q2-ref="' + esc(ref) + '">'
      + '<span class="q2-tref">' + esc(ref) + '</span>'
      + '<span class="q2-ttitle">' + esc(title) + '</span>'
      // Age then dot: the status marker sits to the RIGHT of the age, matching
      // the main dashboard's .fq-row-signals order.
      + '<span class="q2-tsignals">'
      + '<span class="q2-tage" title="' + esc(ageSrc || '') + '">' + esc(age) + '</span>'
      + '<span class="q2-tdot" title="' + esc(dotTitle) + '" aria-label="' + esc(dotTitle) + '"></span>'
      + '</span>'
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

    // Same comparator as the main dashboard (static/app.js:35581): operational
    // bucket first, then open rows by priority and oldest-first (engine order),
    // everything else newest-first.
    function bySameOrderAsMainCcc(a, b) {
      var bucket = operationalBucket(a) - operationalBucket(b);
      if (bucket) return bucket;
      if (statusOf(a) === 'open') {
        var p = prioRank(a) - prioRank(b);
        if (p) return p;
        return (a.number || 0) - (b.number || 0);
      }
      return (b.number || 0) - (a.number || 0);
    }
    openish.sort(bySameOrderAsMainCcc);
    closed.sort(bySameOrderAsMainCcc);

    // Same counts renderer as the queue rows. Derived from the rows actually on
    // screen (so it honours the search filter) but split by the same statuses,
    // rather than lumping blocked and in-progress under "open".
    var hdr = { needsInput: 0, wip: 0, waiting: 0 };
    openish.forEach(function (it) {
      var st = statusOf(it);
      if (st === 'blocked') hdr.needsInput++;
      else if (st === 'in_progress') hdr.wip++;
      else hdr.waiting++;
    });
    $('q2TicketCount').innerHTML = '<span class="q2-counts">' + countsLine(hdr, closed.length) + '</span>';

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
      // "Blocked" is the store's word for this event; say what it means to a
      // person, matching the "needs input" label used everywhere else.
      comment: 'Comment', answer: 'Answered', block: 'Needs input', edit: 'Edited',
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
      + '<span class="q2-status is-' + esc(st) + '">' + esc(statusLabel(st)) + '</span>'
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
    queues:  { varName: '--q2-queues-w',  key: 'ccc-q2-w-queues',  def: 300, min: 170, max: 560 },
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
