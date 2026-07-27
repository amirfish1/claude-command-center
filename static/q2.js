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

  // One definition per number, shared by the queue rows and the ticket-list
  // header. The header strings them together; the queue row scatters them to
  // its corners. Both draw the same markup, label and colour from here, so the
  // two surfaces cannot disagree about what a queue holds — which is exactly
  // what made the header claim "5 open" for a queue with 4 open and 1 blocked.
  function countParts(f, done) {
    f = f || {};
    return {
      needsInput: f.needsInput
        ? '<span class="q2-n is-blocked" title="Blocked waiting on a human answer">'
          + '<b>' + f.needsInput + '</b> needs input</span>'
        : '',
      wip: f.wip
        ? '<span class="q2-n is-wip" title="Claimed by a worker and in progress">'
          + '<b>' + f.wip + '</b> wip</span>'
        : '',
      open: '<span class="q2-n is-open" title="Open and unclaimed"><b>'
        + (f.waiting || 0) + '</b> open</span>',
      done: '<span class="q2-n is-done" title="Closed, all time"><b>'
        + (done || 0) + '</b> done</span>',
    };
  }

  function countsLine(f, done) {
    var c = countParts(f, done);
    return [c.needsInput, c.wip, c.open, c.done].filter(Boolean)
      .join('<span class="q2-n-sep" aria-hidden="true">·</span>');
  }

  // ── auto-drain toggle ────────────────────────────────────────────────────
  // /api/queue/status is cached server-side for 15s with stale-while-
  // revalidate, so for up to 15s after a successful write the poll still
  // reports the OLD auto_drain. Rendering that would flip the control back
  // under the user's hand. So a confirmed write is held as an override and
  // only released once the server's own payload agrees with it.
  var drainPending = {};    // queue → true while the POST is in flight
  var drainOverride = {};   // queue → value we wrote and the server has not caught up to

  function effectiveAutoDrain(q) {
    var k = projectKey(q.queue);
    if (drainOverride[k] != null) return drainOverride[k];
    return !!q.auto_drain;
  }

  // Drop overrides the server has caught up with. Called on every poll, before
  // rendering, so a stale override can never outlive the truth it was masking.
  function reconcileDrainOverrides() {
    (state.queues || []).forEach(function (q) {
      var k = projectKey(q.queue);
      if (drainOverride[k] != null && !!q.auto_drain === drainOverride[k]) {
        delete drainOverride[k];
      }
    });
  }

  async function setAutoDrain(queue, next) {
    var k = projectKey(queue);
    if (drainPending[k]) return;
    drainPending[k] = true;
    renderQueues();                       // paint the spinner immediately
    try {
      var res = await fetch('/api/queue/drain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ queue: queue, auto_drain: next }),
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
      // Trust the value the server echoes back, not the one we asked for.
      drainOverride[k] = !!data.auto_drain;
    } catch (e) {
      // Leave no override: the row falls back to the server's value, which is
      // the honest thing to show when the write did not land.
      delete drainOverride[k];
      note('Could not change auto-drain for ' + queue + ': ' + e.message);
    } finally {
      delete drainPending[k];
      renderQueues();
    }
  }

  // Row 1 carries only configuration (what this queue IS); the counts line
  // below carries state (what is in it right now).
  function queueChips(q) {
    var on = effectiveAutoDrain(q);
    return [on
      ? { k: 'auto-on', label: 'auto', tip: 'Auto-drain is ON. WatchTower spawns workers for this queue on its own. Click to turn it off.' }
      : { k: 'auto-off', label: 'manual', tip: 'Auto-drain is OFF. Nothing runs here until you start a worker. Click to turn it on.' }];
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
      // Everything else equal: most recently touched first. A queue with no
      // activity timestamp at all sorts last rather than first.
      q.last_activity_seconds != null ? q.last_activity_seconds : Infinity
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
    reconcileDrainOverrides();
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

  // A write that fails silently is worse than one that fails loudly: the user
  // would be left believing a toggle took effect. Surfaced in the topbar.
  var noteTimer = null;
  function note(msg) {
    var el = $('q2Note');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    if (noteTimer) clearTimeout(noteTimer);
    noteTimer = setTimeout(function () { el.hidden = true; }, 6000);
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
      var busy = !!drainPending[projectKey(q.queue)];
      var on = effectiveAutoDrain(q);
      // A real <button>, which is why the row itself cannot be one: nesting
      // interactive controls is invalid and breaks keyboard traversal.
      var chips = queueChips(q).map(function (c) {
        return '<button type="button" class="q2-chip q2-drain is-' + esc(c.k)
          + (busy ? ' is-busy' : '') + '"'
          + ' data-q2-drain="' + esc(q.queue) + '" data-q2-next="' + (on ? '0' : '1') + '"'
          + (busy ? ' disabled aria-busy="true"' : '')
          + ' role="switch" aria-checked="' + (on ? 'true' : 'false') + '"'
          + ' title="' + esc(busy ? 'Saving…' : c.tip || '') + '">'
          + (busy ? '<span class="q2-spin" aria-hidden="true"></span>' : '')
          + esc(c.label) + '</button>';
      }).join('');
      var c = countParts(f, q.closed);
      return '<div class="q2-qrow' + (isSel ? ' is-selected' : '')
        + (q.state === 'stuck' ? ' is-stuck' : '') + '"'
        + ' role="button" tabindex="0"'
        + ' data-q2-queue="' + esc(q.queue) + '">'
        // Row 1 — identity on the left, current work on the right.
        + '<span class="q2-qrow-head">'
        + '<span class="q2-qname">' + esc(q.queue) + '</span>'
        + ((f && f.github) ? '<span class="q2-gh-wrap" title="Backed by GitHub issues'
            + ((f.local) ? ' (plus ' + f.local + ' local ticket' + (f.local === 1 ? '' : 's') + ')' : '')
            + '." aria-label="GitHub-backed queue">' + GH_MARK + '</span>' : '')
        + (q.state === 'stuck' ? '<span class="q2-stuck-flag">stuck</span>' : '')
        + (q.repo_path ? '<span class="q2-qrepo" title="' + esc(q.repo_path) + '">' + esc(shortPath(q.repo_path)) + '</span>' : '')
        + '<span class="q2-qrow-tr">' + c.wip + c.open + '</span>'
        + '</span>'
        // Row 2 — configuration on the left, what needs a human on the right.
        + '<span class="q2-qrow-foot">'
        + '<span class="q2-qrow-bl">' + chips + c.done + '</span>'
        + '<span class="q2-qrow-br">' + c.needsInput
        + (q.last_activity_seconds != null
            ? '<span class="q2-qage" title="Most recent ticket activity in this queue">'
              + esc(agoFromSeconds(q.last_activity_seconds)) + '</span>'
            : '')
        + '</span>'
        + '</span>'
        + (q.state === 'stuck' ? '<span class="q2-qwhy">' + esc(stuckWhy(q)) + '</span>' : '')
        + '</div>';
    }).join('');
  }

  // ── render: column 2, tickets ────────────────────────────────────────────
  // Triage chips, same set and shorthand the main dashboard puts on a row
  // (static/app.js:35610). Type and priority share one chip because they are
  // read together ("BUG/p0"); value and confidence share one for the same
  // reason ("H/M"). The needs-input chip is deliberately omitted here — the
  // status dot on the same row already carries it.
  var TYPE_SHORT = { feature: 'FR', bug: 'BUG' };
  var READY_SHORT = { 'needs-shaping': 'shape', 'needs-spec': 'spec', 'shovel-ready': 'ready' };
  function ticketChips(it) {
    var c = [];
    if (it.type) {
      var label = TYPE_SHORT[it.type] || it.type;
      c.push('<span class="q2-tchip is-type-' + esc(it.type) + (it.priority ? ' is-prio-' + esc(it.priority) : '') + '"'
        + ' title="' + esc(it.priority ? it.type + ' / ' + it.priority : it.type) + '">'
        + esc(it.priority ? label + '/' + it.priority : label) + '</span>');
    } else if (it.priority) {
      c.push('<span class="q2-tchip is-prio-' + esc(it.priority) + '" title="priority">'
        + esc(it.priority) + '</span>');
    }
    if (it.readiness) {
      c.push('<span class="q2-tchip is-ready" title="readiness: ' + esc(it.readiness) + '">'
        + esc(READY_SHORT[it.readiness] || it.readiness) + '</span>');
    }
    if (it.value || it.confidence) {
      c.push('<span class="q2-tchip is-vc" title="value / confidence">'
        + esc(it.value || '-') + '/' + esc(it.confidence || '-') + '</span>');
    }
    return c.length ? '<span class="q2-tchips">' + c.join('') + '</span>' : '';
  }

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
    var age = relTime(ageSrc);
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
      + ticketChips(it)
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
  // Long ticket notes are prose, not one enormous heading. Keep the opening
  // sentence prominent and the rest at body weight. Splits only on a real
  // terminator followed by whitespace, so a note that wraps mid-clause is not
  // cut in half (same rule as splitFirstSentence in app.js:2041).
  function splitFirstSentence(text) {
    if (!text) return ['', ''];
    var s = String(text).trim();
    var m = s.match(/^([\s\S]*?[.!?])\s+([\s\S]+)$/);
    if (!m) return [s, ''];
    return [m[1].trim(), m[2].trim()];
  }

  function resList(v) {
    var arr = Array.isArray(v) ? v.filter(Boolean) : (v ? [v] : []);
    if (!arr.length) return '';
    if (arr.length === 1) return esc(String(arr[0]));
    return arr.map(function (x) { return '<div>' + esc(String(x)) + '</div>'; }).join('');
  }

  // A close event carries the worker's own account of what it did. Flattening
  // that to one blob loses the distinction that matters most: what was fixed
  // versus what was explicitly NOT.
  function resolutionHtml(resolution) {
    var res = (resolution && typeof resolution === 'object') ? resolution : {};
    var rows = [
      ['Summary', res.summary, ''],
      ['Caveat', res.caveats || res.caveat, 'is-caveat'],
      ['Follow-up', res.follow_ups || res.follow_up, ''],
      ['Unresolved', res.unresolved, 'is-unresolved'],
    ].filter(function (r) { return resList(r[1]); });
    if (!rows.length) return '';
    return '<div class="q2-res">' + rows.map(function (r) {
      return '<div class="q2-res-row ' + r[2] + '">'
        + '<span class="q2-res-k">' + esc(r[0]) + '</span>'
        + '<div class="q2-res-v">' + resList(r[1]) + '</div></div>';
    }).join('') + '</div>';
  }

  function sessionBtn(sid, label) {
    if (!sid) return '';
    return '<a class="q2-linkbtn" target="_blank" rel="noopener"'
      + ' href="/?session=' + encodeURIComponent(sid) + '"'
      + ' title="' + esc(sid) + '">' + esc(label) + ' &#8599;</a>';
  }

  function editFieldsHtml(fields) {
    var obj = (fields && typeof fields === 'object') ? fields : {};
    var keys = Object.keys(obj).sort();
    if (!keys.length) return '';
    return '<div class="q2-tl-fields">' + keys.map(function (k) {
      return '<div><span>' + esc(k) + '</span> ' + esc(String(obj[k])) + '</div>';
    }).join('') + '</div>';
  }

  function timelineHtml(item) {
    var tl = Array.isArray(item.timeline) ? item.timeline : [];
    if (!tl.length) return '<div class="q2-empty">No activity yet.</div>';

    function head(label, ev) {
      var by = (ev && ev.by && typeof ev.by === 'object') ? ev.by : {};
      var actor = by.worker || by.kind || '';
      return '<span class="q2-tl-verb">' + esc(label) + '</span>'
        + (ev.at ? '<span class="q2-tl-time" title="' + esc(ev.at) + '">' + esc(relTime(ev.at)) + '</span>' : '')
        + (actor ? '<span class="q2-tl-who">' + esc(String(actor).slice(0, 26)) + '</span>' : '')
        + (by.session_id ? sessionBtn(by.session_id, 'open session') : '');
    }
    function evt(kind, headHtml, bodyHtml) {
      return '<div class="q2-tl-row is-' + esc(kind) + '">'
        + '<span class="q2-tl-dot" aria-hidden="true"></span>'
        + '<div class="q2-tl-main"><div class="q2-tl-head">' + headHtml + '</div>'
        + (bodyHtml || '') + '</div></div>';
    }
    function text(t, cls) {
      return t ? '<div class="' + (cls || 'q2-tl-note') + '">' + esc(String(t)) + '</div>' : '';
    }

    var rows = tl.map(function (ev) {
      var type = String((ev && ev.event) || '');
      if (type === 'filed') {
        return evt('filed', head('Filed', ev)
          + (ev.source ? '<span class="q2-tl-meta">via ' + esc(ev.source) + '</span>' : ''),
          ev.project ? '<span class="q2-tl-tag">' + esc(ev.project) + '</span>' : '');
      }
      if (type === 'claim')    return evt('claim', head('Claimed', ev), '');
      if (type === 'progress') return evt('progress', head('Progress', ev), text(ev.text));
      // "Blocked" is the store's word; say what it means to a person.
      if (type === 'block')    return evt('block', head('Needs input', ev), text(ev.question, 'q2-tl-q'));
      if (type === 'answer')   return evt('answer', head('Answered', ev), text(ev.text, 'q2-tl-note is-answer'));
      if (type === 'comment')  return evt('comment', head('Comment', ev), text(ev.text));
      if (type === 'reopen')   return evt('reopen', head('Reopened', ev), text(ev.reason));
      if (type === 'close')    return evt('close', head('Closed', ev), resolutionHtml(ev.resolution));
      if (type === 'move') {
        var mv = [ev.from_ref, ev.to_ref].filter(Boolean).join(' → ');
        return evt('move', head('Moved', ev), text(mv));
      }
      if (type === 'edit')     return evt('edit', head('Edited', ev), editFieldsHtml(ev.fields));
      return evt('comment', head(type || 'Event', ev), text(ev.text));
    }).join('');

    // An open ticket has no terminal event, so the timeline would just stop
    // mid-story. Cap it with where the ticket actually stands.
    if (!item.closed_at) {
      var st = statusOf(item);
      var verb = st === 'in_progress' ? 'In progress'
        : st === 'blocked' ? 'Needs your input' : 'Open';
      rows += evt('now', '<span class="q2-tl-verb">' + esc(verb) + '</span>'
        + (item.claimed_by ? '<span class="q2-tl-who">' + esc(String(item.claimed_by).slice(0, 26)) + '</span>' : ''), '');
    }
    return '<div class="q2-tl">' + rows + '</div>';
  }

  function propRow(k, valHtml) {
    if (!valHtml) return '';
    return '<div class="q2-prop-k">' + esc(k) + '</div><div class="q2-prop-v">' + valHtml + '</div>';
  }

  function propSelect(label, field, options, current) {
    return '<div class="q2-prop-k">' + esc(label) + '</div>'
      + '<div class="q2-prop-v"><select class="q2-select" data-q2-field="' + esc(field) + '">'
      + options.map(function (o) {
          return '<option value="' + esc(o[0]) + '"' + (String(current || '') === o[0] ? ' selected' : '') + '>'
            + esc(o[1]) + '</option>';
        }).join('')
      + '</select></div>';
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
    var parts = splitFirstSentence(titleOf(item));
    var prompt = (item.text && item.text.trim()) || '';
    // Compare the RAW fields, exactly as the main dashboard does
    // (static/app.js:35052). Comparing against the rendered title instead
    // never matched — titleOf() strips boilerplate and rejoins lines, so the
    // reconstruction always differed by whitespace and the block showed the
    // note back verbatim under a "Full prompt" heading.
    var showPrompt = !!prompt && prompt !== String(item.note || '').trim();
    var sid = sessionOf(item);
    var editCount = (Array.isArray(item.timeline) ? item.timeline : [])
      .filter(function (ev) { return ev && ev.event === 'edit'; }).length;

    var side = '<div class="q2-side-group"><div class="q2-sec-label">Properties</div>'
      + '<div class="q2-props">'
      + propSelect('Priority', 'priority', [['', '-'], ['p0', 'p0 - urgent'], ['p1', 'p1'], ['p2', 'p2'], ['p3', 'p3']], item.priority)
      + propSelect('Type', 'type', [['', '-'], ['bug', 'bug'], ['feature', 'feature']], item.type)
      + propSelect('Readiness', 'readiness', [['', '-'], ['shovel-ready', 'shovel-ready'], ['needs-spec', 'needs-spec'], ['needs-shaping', 'needs-shaping']], item.readiness)
      + propSelect('Value', 'value', [['', '-'], ['H', 'H - High'], ['M', 'M - Med'], ['L', 'L - Low']], item.value)
      + propSelect('Confidence', 'confidence', [['', '-'], ['H', 'H - High'], ['M', 'M - Med'], ['L', 'L - Low']], item.confidence)
      + '</div></div>'
      + '<div class="q2-side-group"><div class="q2-sec-label">Assignment</div><div class="q2-props">'
      + propRow('Worker', item.claimed_by
          ? '<span class="q2-tag">' + esc(String(item.claimed_by).slice(0, 28)) + '</span>'
          : '<span class="q2-dim">unassigned</span>')
      + propRow('Session', sid ? sessionBtn(sid, 'open in CCC') : '')
      + propRow('Claimed', item.claimed_at ? '<span title="' + esc(item.claimed_at) + '">' + esc(relTime(item.claimed_at)) + '</span>' : '')
      + propRow('Closed', item.closed_at ? '<span title="' + esc(item.closed_at) + '">' + esc(relTime(item.closed_at)) + '</span>' : '')
      + '</div></div>'
      + '<div class="q2-side-group"><div class="q2-sec-label">Origin</div><div class="q2-props">'
      + propRow('Project', esc(item.project || ''))
      + propRow('Source', esc(item.source || ''))
      + propRow('Lane', esc(item.lane || ''))
      + propRow('Repo', item.repo_path ? '<span class="q2-mono" title="' + esc(item.repo_path) + '">' + esc(shortPath(item.repo_path)) + '</span>' : '')
      + propRow('URL', item.url ? '<a class="q2-linklike" href="' + esc(item.url) + '" target="_blank" rel="noopener">open &#8599;</a>' : '')
      + '</div></div>';

    // Write actions mirror the main dashboard's: answer a blocked ticket,
    // comment, and close or reopen depending on where the ticket stands.
    var answerSec = st === 'blocked'
      ? '<section class="q2-sec q2-sec-answer"><div class="q2-sec-label">The agent needs your decision</div>'
        + (item.block_question ? '<div class="q2-block-q">' + esc(item.block_question) + '</div>' : '')
        + '<textarea class="q2-input" data-q2-input="answer" rows="2" placeholder="Your answer" aria-label="Answer this ticket"></textarea>'
        + '<div class="q2-actrow"><button type="button" class="q2-btn q2-btn-primary" data-q2-act="answer">Send answer</button></div></section>'
      : '';
    var commentSec = '<section class="q2-sec"><div class="q2-sec-label">Add comment</div>'
      + '<textarea class="q2-input" data-q2-input="comment" rows="2" placeholder="Log an update - not a resolution" aria-label="Add a comment"></textarea>'
      + '<div class="q2-actrow"><button type="button" class="q2-btn" data-q2-act="comment">Add comment</button></div></section>';
    var closeSec = st === 'closed'
      ? '<section class="q2-sec"><div class="q2-sec-label">Reopen</div>'
        + '<textarea class="q2-input" data-q2-input="reopen" rows="2" placeholder="Reason for reopening (optional)" aria-label="Reason for reopening"></textarea>'
        + '<div class="q2-actrow"><button type="button" class="q2-btn" data-q2-act="reopen">Reopen ticket</button></div></section>'
      : '<section class="q2-sec"><div class="q2-sec-label">Close with a note</div>'
        + '<textarea class="q2-input" data-q2-input="close" rows="2" placeholder="Resolution summary (optional)" aria-label="Resolution summary"></textarea>'
        + '<div class="q2-actrow"><button type="button" class="q2-btn" data-q2-act="close">Mark as closed</button></div></section>';

    host.innerHTML = ''
      + '<div class="q2-detail-head">'
      + '<span class="q2-detail-ref">' + esc(item.ref) + '</span>'
      + '<span class="q2-status is-' + esc(st) + '">' + esc(statusLabel(st)) + '</span>'
      + (item.lane ? '<span class="q2-chip">' + esc(item.lane) + '</span>' : '')
      + (item.priority ? '<span class="q2-chip is-prio-' + esc(item.priority) + '">' + esc(item.priority) + '</span>' : '')
      + (item.type ? '<span class="q2-chip is-type-' + esc(item.type) + '">' + esc(item.type) + '</span>' : '')
      + '</div>'
      + '<h1 class="q2-detail-title">'
      + '<span class="q2-title-first">' + esc(parts[0]) + '</span>'
      + (parts[1] ? '<span class="q2-title-rest"> ' + esc(parts[1]) + '</span>' : '')
      + '</h1>'
      + '<div class="q2-detail-cols">'
      + '<div class="q2-detail-main">'
      + (showPrompt
          ? '<section class="q2-sec"><div class="q2-sec-label">Full prompt</div>'
            + '<pre class="q2-pre">' + esc(prompt) + '</pre></section>'
          : '')
      + '<section class="q2-sec"><div class="q2-sec-label">Activity'
      + (editCount
          ? '<label class="q2-show-edits"><input type="checkbox" data-q2-show-edits> show edits (' + editCount + ')</label>'
          : '')
      + '</div><div class="q2-tl-wrap">' + timelineHtml(item) + '</div></section>'
      + answerSec + commentSec + closeSec
      + '</div>'
      + '<aside class="q2-detail-side">' + side + '</aside>'
      + '</div>'
      + '<div class="q2-detail-foot">'
      + '<span class="q2-dim">Filed <span title="' + esc(item.created_at || '') + '">' + esc(relTime(item.created_at)) + '</span>'
      + (item.updated_at && item.updated_at !== item.created_at
          ? ' &middot; updated <span title="' + esc(item.updated_at) + '">' + esc(relTime(item.updated_at)) + '</span>' : '')
      + '</span>'
      + '<button type="button" class="q2-btn" data-q2-act="copy">Copy prompt</button>'
      + '</div>';
  }

  // ── detail actions ───────────────────────────────────────────────────────
  function detailInput(name) {
    var el = document.querySelector('[data-q2-input="' + name + '"]');
    return el ? String(el.value || '').trim() : '';
  }

  async function postJson(url, payload) {
    var res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || data.ok === false) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
  }

  async function detailAction(act, btn) {
    var ref = state.ref;
    if (!ref) return;

    if (act === 'copy') {
      var text = (state.detail && state.detail.text) || titleOf(state.detail) || '';
      try { await navigator.clipboard.writeText(text); note('Prompt copied'); }
      catch (e) { note('Could not copy: ' + e.message); }
      return;
    }

    var plan = {
      answer:  ['/api/ux-fixes/answer',  { ref: ref, answer: detailInput('answer') },  true,  'Answer sent'],
      comment: ['/api/ux-fixes/comment', { ref: ref, text: detailInput('comment') },   true,  'Comment added'],
      close:   ['/api/ux-fixes/close',   { ref: ref, summary: detailInput('close') },  false, 'Ticket closed'],
      reopen:  ['/api/ux-fixes/reopen',  { ref: ref, reason: detailInput('reopen') },  false, 'Ticket reopened'],
    }[act];
    if (!plan) return;
    // Answer and comment carry the user's words; sending an empty one would
    // write a blank event nobody can interpret.
    if (plan[2] && !Object.values(plan[1]).filter(function (v) { return v !== ref; })[0]) {
      note('Nothing to send - the box is empty.');
      return;
    }

    btn.disabled = true;
    try {
      await postJson(plan[0], plan[1]);
      note(plan[3]);
      // The list and the ticket both changed. Re-fetch rather than patching
      // local state, so what is on screen is what the store actually holds.
      state.detail = null;
      await loadDetail(ref);
      await refresh();
    } catch (e) {
      note('Failed: ' + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  // Editable property selects, same fields the main dashboard exposes.
  document.addEventListener('change', async function (e) {
    var sel = e.target.closest && e.target.closest('[data-q2-field]');
    if (!sel || !state.ref) return;
    var field = sel.getAttribute('data-q2-field');
    var payload = { ref: state.ref };
    payload[field] = sel.value;
    sel.disabled = true;
    try {
      await postJson('/api/ux-fixes/edit', payload);
      state.detail = null;
      await loadDetail(state.ref);
      await refresh();
    } catch (err) {
      note('Could not save ' + field + ': ' + err.message);
    } finally {
      sel.disabled = false;
    }
  });

  // Edit events are bookkeeping noise by default; the toggle reveals them.
  document.addEventListener('change', function (e) {
    var box = e.target.closest && e.target.closest('[data-q2-show-edits]');
    if (!box) return;
    var tl = document.querySelector('.q2-tl');
    if (tl) tl.classList.toggle('show-edits', box.checked);
  });

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
    // The drain toggle sits INSIDE the queue row, so it has to be matched
    // first — otherwise the row's own handler swallows the click and the
    // toggle only ever selects the queue.
    var drain = e.target.closest('[data-q2-drain]');
    if (drain) {
      e.stopPropagation();
      setAutoDrain(drain.getAttribute('data-q2-drain'),
                   drain.getAttribute('data-q2-next') === '1');
      return;
    }
    var act = e.target.closest('[data-q2-act]');
    if (act) { e.stopPropagation(); detailAction(act.getAttribute('data-q2-act'), act); return; }
    var qBtn = e.target.closest('[data-q2-queue]');
    if (qBtn) { selectQueue(qBtn.getAttribute('data-q2-queue')); return; }
    var tBtn = e.target.closest('[data-q2-ref]');
    if (tBtn) { selectTicket(tBtn.getAttribute('data-q2-ref')); return; }
    if (e.target.closest('#q2ClosedBtn')) { state.showClosed = !state.showClosed; renderTickets(); return; }
    if (e.target.closest('#q2ThemeBtn')) { toggleTheme(); return; }
  });

  // Queue rows are divs now (they contain a real button), so the Enter/Space
  // activation a <button> gave for free has to be restored by hand.
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    var row = e.target.closest && e.target.closest('.q2-qrow[data-q2-queue]');
    if (!row || e.target !== row) return;
    e.preventDefault();
    selectQueue(row.getAttribute('data-q2-queue'));
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
