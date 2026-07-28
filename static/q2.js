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
  // Keep a just-finished ticket in the working list for handoff context, while
  // leaving the all-time closure history behind the explicit control.
  var RECENT_CLOSED_WINDOW_MS = 12 * 60 * 60 * 1000;

  var state = {
    queues: [],
    projects: {},       // queue name → per-project health row (why it's stuck)
    workers: [],        // live WatchTower workers — the only proof a claim is real
    items: [],
    queue: '',
    ref: '',
    detail: null,       // full item payload for state.ref
    showClosed: false,
    closedCap: CLOSED_CAP,   // grows via the 'show more' button
    search: '',
    offline: false,
    booted: false,      // guards the one-shot restore of the saved selection
    arm: '',            // which write form is open ('' = none)
    configs: {},        // queue -> wt config (engine/model/effort/workers)
    configDefaults: {}, // wt defaults, merged under each queue's own config
    configsOwn: {},     // queue -> only the fields that queue SET itself
    log: [],            // reconciler activity lines for the selected queue
    logQueue: '',
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
  // A queue only drains the ticket types in its claim_types. Everything else
  // is inventory: real, open, and never going to be picked up here. Counting
  // those as "open" made a bugs-only queue with three feature requests look
  // like it had three tickets waiting for a worker that will never take them.
  // Untyped counts as a bug, matching WatchTower's own filter
  // (ccc_server/queue_events.py:358).
  function claimTypesFor(key) {
    var q = (state.queues || []).filter(function (x) { return projectKey(x.queue) === key; })[0];
    var t = q && Array.isArray(q.claim_types) ? q.claim_types : [];
    return t.filter(function (x) { return x === 'bug' || x === 'feature'; });
  }
  function ticketType(it) {
    var ty = String((it && (it.type || it.item_type)) || '').trim().toLowerCase();
    return (ty === 'bug' || ty === 'feature') ? ty : 'bug';
  }
  function isClaimableType(it, types) {
    return !types.length || types.indexOf(ticketType(it)) !== -1;
  }

  function queueFacts() {
    var by = {};
    var typesCache = {};
    function bucket(k) {
      if (!by[k]) by[k] = { github: 0, local: 0, needsInput: 0, wip: 0, waiting: 0, parked: 0 };
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
      else if (st === 'open') {
        if (typesCache[k] === undefined) typesCache[k] = claimTypesFor(k);
        // `claimable === false` is the GitHub-label exclusion; claim_types is
        // the per-queue policy. Either one parks the ticket.
        if (it.claimable === false || !isClaimableType(it, typesCache[k])) b.parked++;
        else b.waiting++;
      }
    });
    return by;
  }

  // One definition per number, shared by the queue rows and the ticket-list
  // header. The header strings them together; the queue row scatters them to
  // its corners. Both draw the same markup, label and colour from here, so the
  // two surfaces cannot disagree about what a queue holds — which is exactly
  // what made the header claim "5 open" for a queue with 4 open and 1 blocked.
  function countParts(f, done, types) {
    f = f || {};
    // "3 open" on a bugs-only queue reads as three things a worker will take.
    // Naming the count after the claimed type makes "0 bugs" say what it means.
    types = types || [];
    // Singular for one, so a row never reads "1 features".
    var openBase = types.length === 1 ? (types[0] === 'bug' ? 'bug' : 'feature') : '';
    var openWord = openBase
      ? openBase + ((f.waiting || 0) === 1 ? '' : 's')
      : 'open';
    var openTip = types.length
      ? 'Open and claimable here (this queue drains ' + types.join(' + ') + ')'
      : 'Open and unclaimed';
    return {
      needsInput: f.needsInput
        ? '<span class="q2-n is-blocked" title="Blocked waiting on a human answer">'
          + '<b>' + f.needsInput + '</b> needs input</span>'
        : '',
      wip: f.wip
        ? '<span class="q2-n is-wip" title="Claimed by a worker and in progress">'
          + '<b>' + f.wip + '</b> wip</span>'
        : '',
      open: '<span class="q2-n is-open' + ((f.waiting || 0) ? '' : ' is-zero')
        + '" title="' + esc(openTip) + '"><b>'
        + (f.waiting || 0) + '</b> ' + esc(openWord) + '</span>',
      // Parked (open, but excluded by claim_types) is deliberately NOT counted
      // here. It is inventory nothing will act on, and a second number beside
      // the real one only competed with it. The diagram still shows the pile.
      parked: '',
      done: '<span class="q2-n is-done' + ((done || 0) ? '' : ' is-zero')
        + '" title="Closed, all time"><b>' + (done || 0) + '</b> closed</span>',
    };
  }

  function countsLine(f, done, types) {
    var c = countParts(f, done, types);
    return [c.needsInput, c.wip, c.parked, c.open, c.done].filter(Boolean)
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
  var typesOverride = {};   // queue → claim_types we wrote, same 15s cache lag

  function effectiveClaimTypes(q) {
    var k = projectKey(q.queue);
    if (typesOverride[k]) return typesOverride[k];
    return Array.isArray(q.claim_types)
      ? q.claim_types.filter(function (t) { return t === 'bug' || t === 'feature'; }) : [];
  }

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
      if (typesOverride[k]) {
        var live = Array.isArray(q.claim_types)
          ? q.claim_types.filter(function (t) { return t === 'bug' || t === 'feature'; }) : [];
        if (live.slice().sort().join(',') === typesOverride[k].slice().sort().join(',')) {
          delete typesOverride[k];
        }
      }
    });
  }

  // One press can change BOTH settings (e.g. off -> play/bugs on a queue that
  // was claiming everything), so they are written together and the row only
  // settles once both land.
  async function setDrainMode(queue, mode) {
    var k = projectKey(queue);
    if (drainPending[k]) return;
    drainPending[k] = true;
    renderQueues();
    try {
      var wantAuto = mode !== 'off';
      var wantTypes = (mode === 'bug' || mode === 'feature') ? [mode] : [];
      var res = await postJson('/api/queue/drain', { queue: queue, auto_drain: wantAuto });
      drainOverride[k] = !!res.auto_drain;
      // Claim types only matter while running; leave them alone when stopping
      // so the previous selection is still there when it starts again.
      if (wantAuto) {
        await postJson('/api/wt/queue/claim-types', { queue: queue, claim_types: wantTypes });
        typesOverride[k] = wantTypes;
      }
    } catch (e) {
      delete drainOverride[k];
      delete typesOverride[k];
      note('Could not change drain policy for ' + queue + ': ' + e.message);
    } finally {
      delete drainPending[k];
      renderQueues();
    }
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

  // Drain policy as a single transport control instead of a text chip. The
  // cycle walks the two settings that decide whether anything runs here:
  //   stop  -> play (all types) -> play/bugs -> play/features -> stop
  // Both are real WatchTower settings: auto_drain (/api/queue/drain) and
  // claim_types (/api/wt/queue/claim-types).
  // stop -> bugs -> features -> all -> stop. Bugs first because starting a
  // stopped queue on everything is the broadest possible action; the narrow
  // one should be the cheapest to reach.
  var DRAIN_MODES = ['off', 'bug', 'feature', 'all'];

  // Drawn, not typed. The ▶ / ■ glyphs render at different weights and
  // baselines across fonts and never optically matched each other.
  var ICON_PLAY = '<svg class="q2-transport-icon" viewBox="0 0 16 16" width="13" height="13"'
    + ' aria-hidden="true" focusable="false"><path fill="currentColor"'
    + ' d="M4.8 3.1a.6.6 0 0 1 .92-.51l6.4 4.4a.6.6 0 0 1 0 1.02l-6.4 4.4a.6.6 0 0 1-.92-.51V3.1Z"/></svg>';
  var ICON_GEAR = '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">'
    + '<path fill="currentColor" d="M8 10.2a2.2 2.2 0 1 1 0-4.4 2.2 2.2 0 0 1 0 4.4Zm5.6-1.4.02-.8-.02-.8 1.26-.97a.4.4 0 0 0 .1-.5l-1.2-2.07a.4.4 0 0 0-.48-.18l-1.48.6a5.7 5.7 0 0 0-1.38-.8L10.2.86a.4.4 0 0 0-.4-.34H7.4a.4.4 0 0 0-.4.34l-.22 1.58c-.5.2-.96.47-1.38.8l-1.48-.6a.4.4 0 0 0-.48.18L2.24 4.9a.4.4 0 0 0 .1.5L3.6 6.4l-.02.8.02.8-1.26.97a.4.4 0 0 0-.1.5l1.2 2.07c.1.17.3.24.48.18l1.48-.6c.42.33.88.6 1.38.8l.22 1.58c.03.2.2.34.4.34h2.4c.2 0 .37-.14.4-.34l.22-1.58c.5-.2.96-.47 1.38-.8l1.48.6c.18.06.38-.01.48-.18l1.2-2.07a.4.4 0 0 0-.1-.5L13.6 8.8Z"/>'
    + '</svg>';
  var ICON_PLUS = '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">'
    + '<path fill="currentColor" d="M7.25 2.75a.75.75 0 0 1 1.5 0v4.5h4.5a.75.75 0 0 1 0 1.5h-4.5v4.5a.75.75 0 0 1-1.5 0v-4.5h-4.5a.75.75 0 0 1 0-1.5h4.5v-4.5Z"/>'
    + '</svg>';
  var ICON_TINY_PLAY = '<svg class="q2-tdot-play" viewBox="0 0 16 16" width="11" height="11"'
    + ' aria-hidden="true" focusable="false"><path fill="currentColor"'
    + ' d="M4.8 3.1a.6.6 0 0 1 .92-.51l6.4 4.4a.6.6 0 0 1 0 1.02l-6.4 4.4a.6.6 0 0 1-.92-.51V3.1Z"/></svg>';
  var ICON_TINY_STOP = '<svg class="q2-tdot-play" viewBox="0 0 16 16" width="10" height="10"'
    + ' aria-hidden="true" focusable="false"><rect x="4" y="4" width="8" height="8" rx="1.6"'
    + ' fill="currentColor"/></svg>';

  var ICON_STOP = '<svg class="q2-transport-icon" viewBox="0 0 16 16" width="13" height="13"'
    + ' aria-hidden="true" focusable="false"><rect x="4" y="4" width="8" height="8" rx="1.6"'
    + ' fill="currentColor"/></svg>';

  function drainMode(q) {
    if (!effectiveAutoDrain(q)) return 'off';
    var t = effectiveClaimTypes(q);
    if (t.length === 1) return t[0];
    return 'all';
  }
  function nextDrainMode(mode) {
    var i = DRAIN_MODES.indexOf(mode);
    return DRAIN_MODES[(i + 1) % DRAIN_MODES.length];
  }
  function drainModeLabel(mode) {
    return mode === 'off' ? 'Stopped - nothing runs here'
      : mode === 'all' ? 'Running - claims every type'
      : mode === 'bug' ? 'Running - claims bugs only'
      : 'Running - claims features only';
  }

  function drainControl(q) {
    var key = projectKey(q.queue);
    var busy = !!drainPending[key];
    var mode = drainMode(q);
    var next = nextDrainMode(mode);
    var sub = (mode === 'bug' || mode === 'feature') ? mode + 's' : '';
    return '<button type="button" class="q2-transport is-' + esc(mode) + (busy ? ' is-busy' : '') + '"'
      + ' data-q2-drain="' + esc(q.queue) + '" data-q2-mode="' + esc(next) + '"'
      + (busy ? ' disabled aria-busy="true"' : '')
      + ' aria-label="' + esc(drainModeLabel(mode)) + '"'
      + ' title="' + esc(busy ? 'Saving…' : drainModeLabel(mode) + '. Click for: ' + drainModeLabel(next)) + '">'
      + (busy
          ? '<span class="q2-spin" aria-hidden="true"></span>'
          : (mode === 'off' ? ICON_STOP : ICON_PLAY))
      + (sub ? '<span class="q2-transport-sub">' + esc(sub) + '</span>' : '')
      + '</button>';
  }

  // Row 1 carries only configuration (what this queue IS); the counts line
  // below carries state (what is in it right now).
  function queueChips(q) {
    var on = effectiveAutoDrain(q);
    var types = Array.isArray(q.claim_types)
      ? q.claim_types.filter(function (t) { return t === 'bug' || t === 'feature'; }) : [];
    // "auto" alone hid the policy that matters most: WHAT it drains. A
    // bugs-only queue full of features is doing exactly what it was told.
    var suffix = types.length && types.length < 2
      ? ' \u00b7 ' + (types[0] === 'bug' ? 'bugs' : 'features') : '';
    return [on
      ? { k: 'auto-on', label: 'auto' + suffix,
          tip: 'Auto-drain is ON'
            + (types.length ? ', claiming ' + types.join(' + ') + ' only' : ' for every ticket type')
            + '. Click to turn it off.' }
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
    // Restore the last selection, but only once the queue list has arrived and
    // only if it still exists — a saved pointer to a deleted queue would leave
    // the board on an empty column with no way back.
    if (!state.booted && state.queues.length) {
      state.booted = true;
      var savedQueue = '', savedRef = '';
      try {
        savedQueue = localStorage.getItem(LS_QUEUE) || '';
        savedRef = localStorage.getItem(LS_REF) || '';
      } catch (_) {}
      var match = state.queues.filter(function (q) {
        return projectKey(q.queue) === projectKey(savedQueue);
      })[0];
      if (match) {
        state.queue = match.queue;
        // The ticket is only restored if it is still in that queue. Validating
        // against the item list rather than trusting the id avoids a detail
        // pane stuck on "Loading" for a ref that no longer exists.
        if (savedRef && state.items.some(function (it) {
          return it.ref === savedRef && projectKey(it.project) === projectKey(match.queue);
        })) {
          state.ref = savedRef;
          loadDetail(savedRef);
        }
      }
    }
    // Default selection: first queue with open work, else the first queue.
    if (!state.queue && state.queues.length) {
      var withWork = state.queues.filter(function (q) { return q.depth > 0; });
      state.queue = (withWork[0] || state.queues[0]).queue;
    }
    // One extra tail per poll, only for the queue on screen.
    if (state.queue) await loadLog(state.queue);
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
      var c = countParts(f, q.closed, claimTypesFor(projectKey(q.queue)));
      return '<div class="q2-qrow' + (isSel ? ' is-selected' : '')
        + (q.state === 'stuck' ? ' is-stuck' : '') + '"'
        + ' role="button" tabindex="0"'
        + ' data-q2-queue="' + esc(q.queue) + '">'
        + '<span class="q2-qrow-main">'
        // Row 1 — identity on the left, current work on the right.
        + '<span class="q2-qrow-head">'
        + '<span class="q2-qname">' + esc(q.queue) + '</span>'
        + ((f && f.github) ? '<span class="q2-gh-wrap" title="Backed by GitHub issues'
            + ((f.local) ? ' (plus ' + f.local + ' local ticket' + (f.local === 1 ? '' : 's') + ')' : '')
            + '." aria-label="GitHub-backed queue">' + GH_MARK + '</span>' : '')

        + (q.repo_path ? '<span class="q2-qrepo" title="' + esc(q.repo_path) + '">' + esc(shortPath(q.repo_path)) + '</span>' : '')
        + '<span class="q2-qrow-tr">' + c.wip + c.parked + c.open + '</span>'
        + '</span>'
        // Row 2 — configuration on the left, what needs a human on the right.
        + '<span class="q2-qrow-foot">'
        + '<span class="q2-qrow-bl">' + c.done + '</span>'
        // Age sits LEFT of needs-input so the needs-input count lands directly
        // under the open count, making the right edge one readable column.
        + '<span class="q2-qrow-br">'
        + (q.last_activity_seconds != null
            ? '<span class="q2-qage" title="Most recent ticket activity in this queue">'
              + esc(agoFromSeconds(q.last_activity_seconds)) + '</span>'
            : '')
        + c.needsInput
        + '</span>'
        + '</span>'
        + (q.state === 'stuck' ? '<span class="q2-qwhy">' + esc(stuckWhy(q)) + '</span>' : '')
        + '</span>'
        // Spans both rows: the policy governs the whole queue, not one line.
        + drainControl(q)
        + '</div>';
    }).join('');
  }

  // ── flow diagram + reconciler log ────────────────────────────────────────
  // The ticket list cannot say whether anything is actually working a queue: a
  // ticket marked in_progress proves only that something once claimed it. The
  // diagram shows the pipeline itself — backlog, watcher, worker, recently
  // done — so an idle-but-armed queue looks different from a dead one.
  var LS_LOG_OPEN = 'ccc-q2-log-open';
  // "Finished recently" window. The label is derived from it so the two can
  // never disagree about what the Closed column is actually counting.
  var DONE_WINDOW_MS = 60 * 60 * 1000;
  var DONE_WINDOW_LABEL = '1h';
  var STACK_CAP = 14;                    // drawn cards before it becomes "+N"

  var LS_CONV_OPEN = 'ccc-q2-conv-open';
  // Collapsed by default, and RESET on every ticket change. Persisting "open"
  // meant one expand made every subsequent ticket boot an embedded dashboard
  // on click; the preference is per-ticket, not global.
  var convOpenFor = '';
  function convOpen() { return !!convOpenFor && convOpenFor === state.ref; }
  function setConvOpen(on) { convOpenFor = on ? state.ref : ''; }

  // Closed by default: the log is a drill-in for when something looks wrong,
  // not a permanent fixture taking a quarter of the column.
  function logOpen() {
    try { return localStorage.getItem(LS_LOG_OPEN) === '1'; } catch (_) { return false; }
  }

  // Engine / model / effort live in the queue CONFIG, which /api/queue/status
  // does not carry. Fetched separately and cached: it is small, changes only
  // when someone edits a queue, and the board polls every 5s.
  async function loadConfigs() {
    try {
      var data = await postJson('/api/queue/config-options', {});
      var map = {};
      // config-options returns only the fields a queue has EXPLICITLY set, so
      // a queue on the default engine came back with nothing and read as "not
      // configured". Merge the defaults in, the same way the settings form does.
      var defaults = data.defaults || {};
      var own = {};
      (data.queues || []).forEach(function (q) {
        own[projectKey(q.queue)] = q.config || {};
        map[projectKey(q.queue)] = Object.assign({}, defaults, q.config || {});
      });
      state.configDefaults = defaults;
      state.configsOwn = own;
      state.configs = map;
    } catch (_) { /* the strip just falls back to "not configured" */ }
  }

  async function loadLog(queue) {
    if (!queue) { state.log = []; state.logQueue = ''; return; }
    try {
      var res = await fetch('/api/wt/activity-log?queue=' + encodeURIComponent(queue) + '&lines=200',
                            { cache: 'no-store' });
      var data = await res.json();
      if (projectKey(queue) !== projectKey(state.queue)) return;  // selection moved
      state.log = Array.isArray(data.lines) ? data.lines : [];
      state.logQueue = queue;
    } catch (_) {
      state.log = [];
      state.logQueue = queue;
    }
  }

  // "2026-07-27 01:02:52 UTC  CCC   SPAWN  CCC-666 — text"
  var LOG_RE = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s*UTC\s+(\S+)\s+(\S+)\s*(.*)$/;
  function parseLogLine(line) {
    var m = String(line || '').match(LOG_RE);
    if (!m) return { raw: String(line || '') };
    return { date: m[1], time: m[2], queue: m[3], verb: m[4], rest: m[5] };
  }

  // Everything the diagram draws, gathered once.
  function flowModel() {
    var key = projectKey(state.queue);
    var q = (state.queues || []).filter(function (x) { return projectKey(x.queue) === key; })[0] || {};
    var mine = (state.items || []).filter(function (it) { return projectKey(it.project) === key; });
    var now = Date.now();

    var types = claimTypesFor(key);
    var waiting = [], parked = [], blocked = [], working = [], doneRecent = [];
    mine.forEach(function (it) {
      var st = statusOf(it);
      if (st === 'closed') {
        var t = Date.parse(it.closed_at || it.updated_at || '');
        if (isFinite(t) && now - t < DONE_WINDOW_MS) doneRecent.push(it);
        return;
      }
      if (st === 'blocked') blocked.push(it);
      else if (st === 'in_progress') working.push(it);
      else if (it.claimable === false || !isClaimableType(it, types)) parked.push(it);
      else waiting.push(it);
    });
    doneRecent.sort(function (a, b) {
      return Date.parse(b.closed_at || b.updated_at || 0) - Date.parse(a.closed_at || a.updated_at || 0);
    });

    var workers = (state.workers || []).filter(function (w) { return projectKey(w.queue) === key; });
    var cfg = (state.configs || {})[key] || {};
    return {
      cfg: cfg,
      q: q,
      auto: effectiveAutoDrain(q),
      github: !!((queueFacts()[key] || {}).github),
      stuck: q.state === 'stuck',
      waiting: waiting, parked: parked, blocked: blocked, working: working,
      doneRecent: doneRecent, workers: workers, types: types,
    };
  }

  function stackHtml(items, cls) {
    if (!items.length) return '<div class="q2-dg-empty">empty</div>';
    var shown = items.slice(0, STACK_CAP);
    var html = shown.map(function (it, i) {
      return '<div class="q2-dg-card ' + cls + '" style="--i:' + i + '"'
        + ' title="' + esc(it.ref + ' — ' + titleOf(it).split('\n')[0].slice(0, 90)) + '"'
        + ' data-q2-ref="' + esc(it.ref) + '">'
        + '<span class="q2-dg-card-ref">' + esc(it.ref) + '</span>'
        + '</div>';
    }).join('');
    if (items.length > shown.length) {
      html += '<div class="q2-dg-more">+' + (items.length - shown.length) + ' more</div>';
    }
    return html;
  }

  // Re-rendering the diagram on every 5s poll would restart every CSS
  // animation mid-cycle, which reads as a stutter. Only rebuild when something
  // it actually draws has changed.
  function flowSignature(m) {
    return [
      projectKey(state.queue), m.auto ? 1 : 0, m.github ? 1 : 0, m.stuck ? 1 : 0,
      m.waiting.length, m.parked.length, m.blocked.length,
      m.working.map(function (it) { return it.ref; }).join(','),
      m.workers.map(function (w) { return w.worker_id; }).join(','),
      m.doneRecent.map(function (it) { return it.ref; }).join(','),
    ].join('|');
  }

  function renderDiagram() {
    var host = $('q2Diagram');
    if (!host) return;
    if (!state.queue) { host.innerHTML = ''; return; }

    var m = flowModel();
    var sig = flowSignature(m);
    if (host.getAttribute('data-sig') === sig) return;
    host.setAttribute('data-sig', sig);

    // Flow is only animated where work can actually move. A manual queue draws
    // the same pipeline with the links dead, which is the honest picture.
    var feedLive = m.auto && (m.waiting.length > 0);   // parked tickets are not flow
    var workLive = m.working.length > 0 && m.workers.length > 0;

    var watchLabel = !m.auto ? 'idle'
      : m.github ? 'polling GitHub' : 'watching';
    var watchNote = !m.auto ? 'Auto-drain off'
      : m.stuck ? 'armed, nothing spawned'
      : m.waiting.length ? 'ready to dispatch'
      : m.parked.length ? 'nothing claimable (' + m.parked.length + ' parked)'
      : 'no claimable work';

    var workerBody;
    if (m.workers.length) {
      workerBody = m.workers.map(function (w) {
        var on = m.working.filter(function (it) {
          var s = String(it.claimed_session_id || '').trim(), by = String(it.claimed_by || '').trim();
          return (s && s === w.session_id) || (by && (by === w.session_id || by === w.worker_id));
        })[0];
        return '<div class="q2-dg-worker is-live">'
          + '<div class="q2-dg-worker-head">'
          + '<span class="q2-dg-spin" aria-hidden="true"></span>'
          + '<span class="q2-dg-worker-id">' + esc(w.worker_id || 'worker') + '</span>'
          + (w.session_id ? sessionBtn(w.session_id, 'open') : '')
          + '</div>'
          + (on
              ? '<div class="q2-dg-worker-on" data-q2-ref="' + esc(on.ref) + '" title="' + esc(titleOf(on).split('\n')[0]) + '">'
                + '<span class="q2-dg-card-ref">' + esc(on.ref) + '</span>'
                + '<span class="q2-dg-worker-title">' + esc(titleOf(on).split('\n')[0].slice(0, 60)) + '</span></div>'
              : '<div class="q2-dg-worker-idle">holding nothing</div>')
          + '</div>';
      }).join('');
    } else {
      // The Worker stage is about the running INSTANCE. What the queue would
      // spawn is configuration, and lives in the column header instead.
      workerBody = '<div class="q2-dg-worker is-empty">'
        + '<div class="q2-dg-slot" aria-hidden="true"></div>'
        + '<div class="q2-dg-worker-idle">' + esc(m.auto ? 'None live' : 'None live \u00b7 stopped') + '</div>'
        + '</div>';
    }

    host.innerHTML = ''
      + '<div class="q2-dg' + (m.stuck ? ' is-stuck' : '') + '">'
      // 1. Backlog
      + '<div class="q2-dg-stage">'
      + '<div class="q2-dg-label" title="' + esc(m.types.length
          ? 'This queue drains ' + m.types.join(' + ') + ' only' : 'This queue drains every type') + '">'
      + esc(m.types.length === 1 ? (m.types[0] === 'bug' ? 'Bugs' : 'Features') : 'Backlog')
      + '<span class="q2-dg-n">' + m.waiting.length + '</span></div>'
      + '<div class="q2-dg-stack">' + stackHtml(m.waiting, 'is-open') + '</div>'
      + (m.parked.length
          ? '<div class="q2-dg-sub is-parked" title="Open, but excluded by this queue&#39;s claim types">'
            + m.parked.length + ' parked</div>'
            + '<div class="q2-dg-stack is-parked">' + stackHtml(m.parked.slice(0, 6), 'is-parked') + '</div>'
          : '')
      + (m.blocked.length
          ? '<div class="q2-dg-sub is-blocked">' + m.blocked.length + ' needs input</div>' : '')
      + '</div>'
      // link: backlog -> watcher
      + '<div class="q2-dg-link' + (feedLive ? ' is-flowing' : '') + '" aria-hidden="true">'
      + '<span class="q2-dg-dot"></span><span class="q2-dg-dot"></span><span class="q2-dg-dot"></span></div>'
      // 2. Watcher
      + '<div class="q2-dg-stage is-narrow">'
      + '<div class="q2-dg-label">Watcher</div>'
      + (m.github
          ? '<div class="q2-dg-gh" title="This queue is backed by GitHub issues, polled continuously into the local store.">'
            + '<span class="q2-dg-gh-pulse" aria-hidden="true"></span>' + GH_MARK + '</div>'
          : '')
      + '<div class="q2-dg-radar' + (m.auto ? ' is-on' : '') + (m.stuck ? ' is-stuck' : '') + '">'
      + '<span class="q2-dg-radar-sweep" aria-hidden="true"></span>'
      + '<span class="q2-dg-radar-core" aria-hidden="true"></span>'
      + '</div>'
      + '<div class="q2-dg-sub">' + esc(watchLabel) + '</div>'
      + '<div class="q2-dg-note">' + esc(watchNote) + '</div>'
      + '</div>'
      // link: watcher -> worker
      + '<div class="q2-dg-link' + (workLive ? ' is-flowing' : '') + '" aria-hidden="true">'
      + '<span class="q2-dg-dot"></span><span class="q2-dg-dot"></span><span class="q2-dg-dot"></span></div>'
      // 3. Worker
      + '<div class="q2-dg-stage is-worker">'
      + '<div class="q2-dg-label">Worker<span class="q2-dg-n">' + m.workers.length + '</span></div>'
      + workerBody
      + '</div>'
      // link: worker -> done
      + '<div class="q2-dg-link' + (m.doneRecent.length ? ' is-flowing is-slow' : '') + '" aria-hidden="true">'
      + '<span class="q2-dg-dot"></span><span class="q2-dg-dot"></span><span class="q2-dg-dot"></span></div>'
      // 4. Done
      + '<div class="q2-dg-stage">'
      + '<div class="q2-dg-label" title="Closed in the last ' + esc(DONE_WINDOW_LABEL) + '">'
      + 'Closed &middot; ' + esc(DONE_WINDOW_LABEL)
      + '<span class="q2-dg-n">' + m.doneRecent.length + '</span></div>'
      + '<div class="q2-dg-stack is-done">' + stackHtml(m.doneRecent, 'is-closed') + '</div>'
      // All-time total. It was only in the header this replaces, and it is the
      // one number the diagram could not otherwise show.
      + '<div class="q2-dg-sub" title="Closed in this queue, all time">'
      + (m.q.closed || 0) + ' total</div>'
      + '</div>'
      + '</div>';
  }

  // Chronological, pinned to the newest line unless the user has scrolled up.
  function renderLogBar() {
    var host = $('q2LogBar');
    if (!host) return;
    var open = logOpen();
    host.classList.toggle('is-collapsed', !open);

    var body = host.querySelector('.q2-logbar-body');
    // Preserve "was the user reading history" across a repaint of the SAME
    // queue. A different queue is a different log, so it always opens at the
    // newest line rather than inheriting the previous queue's scroll offset.
    var sameQueue = host.getAttribute('data-log-queue') === projectKey(state.queue);
    var pinned = true;
    if (body && sameQueue) {
      pinned = (body.scrollHeight - body.scrollTop - body.clientHeight) < 24;
    }
    host.setAttribute('data-log-queue', projectKey(state.queue));

    var rows;
    if (!state.queue) {
      rows = '<div class="q2-dim q2-log-empty">Pick a queue.</div>';
    } else if (!state.log.length) {
      rows = '<div class="q2-dim q2-log-empty">No reconciler activity recorded for '
        + esc(state.queue) + '.</div>';
    } else {
      // Group consecutive lines about the same ticket. The log interleaves
      // several tickets' events, and without a break between clusters it reads
      // as one undifferentiated wall.
      var prevRef = null;
      rows = state.log.map(function (line) {
        var e = parseLogLine(line);
        if (e.raw) return '<div class="q2-log-row"><span class="q2-log-rest">' + esc(e.raw) + '</span></div>';
        var refM = String(e.rest || '').match(/^([A-Z0-9][A-Z0-9-]*-\d+)\b/);
        var thisRef = refM ? refM[1] : null;
        var newCluster = prevRef !== null && thisRef !== prevRef;
        prevRef = thisRef;
        return '<div class="q2-log-row' + (newCluster ? ' is-cluster-start' : '') + '">'
          + '<span class="q2-log-time" title="' + esc(e.date + ' ' + e.time + ' UTC') + '">' + esc(e.time) + '</span>'
          + '<span class="q2-log-verb is-' + esc(e.verb.toLowerCase()) + '">' + esc(e.verb) + '</span>'
          + '<span class="q2-log-rest">' + esc(e.rest) + '</span>'
          + '</div>';
      }).join('');
    }

    host.innerHTML = '<div class="q2-logbar-head">'
      + '<button type="button" class="q2-ops-toggle" data-q2-log-toggle aria-expanded="' + (open ? 'true' : 'false') + '">'
      + '<span class="q2-ops-caret" aria-hidden="true">' + (open ? '&#9662;' : '&#9656;') + '</span>'
      + 'Activity log' + (state.queue ? ' &middot; ' + esc(state.queue) : '')
      + '</button>'
      + '<span class="q2-spacer"></span>'
      + '<span class="q2-dim q2-logbar-count">' + (state.log.length ? state.log.length + ' lines' : '') + '</span>'
      + '</div>'
      + '<div class="q2-logbar-body">' + rows + '</div>';

    if (pinned) {
      var nb = host.querySelector('.q2-logbar-body');
      if (nb) nb.scrollTop = nb.scrollHeight;
    }
  }

  // ── render: column 2, tickets ────────────────────────────────────────────
  // Triage chips, same set and shorthand the main dashboard puts on a row
  // (static/app.js:35610). Type and priority share one chip because they are
  // read together ("BUG/p0"); value and confidence share one for the same
  // reason ("H/M"). The needs-input chip is deliberately omitted here — the
  // status dot on the same row already carries it.
  var TYPE_SHORT = { feature: 'FR', bug: 'BUG' };
  var READY_SHORT = { 'needs-shaping': 'shape', 'needs-spec': 'spec' };
  // Two spellings of "this is fine" live in the store. Both stay silent.
  var READY_OK = { 'ready': 1, 'shovel-ready': 1 };
  // Last touch: the newest of updated / closed / created. Closed rows sort by
  // this and must DISPLAY it too, or the list looks mis-ordered — a ticket
  // reopened and re-closed an hour ago showed its original close time.
  function touchedAt(it) {
    return Math.max(Date.parse((it && it.updated_at) || 0) || 0,
                    Date.parse((it && it.closed_at) || 0) || 0,
                    Date.parse((it && it.created_at) || 0) || 0);
  }

  function ticketChips(it) {
    var c = [];
    if (it.type) {
      // Colour carries the TYPE only. Tinting the same chip by priority as
      // well meant a p0 bug and a p0 feature looked identical, which defeats
      // the point of the chip; the priority is right there in the text.
      var label = TYPE_SHORT[it.type] || it.type;
      c.push('<span class="q2-tchip is-type-' + esc(it.type) + '"'
        + ' title="' + esc(it.priority ? it.type + ' / ' + it.priority : it.type) + '">'
        + esc(it.priority ? label + '/' + it.priority : label) + '</span>');
    } else if (it.priority) {
      c.push('<span class="q2-tchip is-prio-' + esc(it.priority) + '" title="priority">'
        + esc(it.priority) + '</span>');
    }
    // Readiness only shows when it is a problem. The store carries BOTH
    // "ready" (192 tickets) and "shovel-ready" (12) for the same idea, so
    // filtering one still left the chip on most rows. Anything that is not a
    // problem is silent; absence means ready.
    if (it.readiness && !READY_OK[it.readiness]) {
      c.push('<span class="q2-tchip is-unready" title="readiness: ' + esc(it.readiness) + '">'
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
      ? (new Date(touchedAt(it)).toISOString())
      : (it.updated_at || it.created_at);
    var age = relTime(ageSrc);
    // Only an idle, open ticket can be "run now": a closed one has nothing to
    // do and a live-claimed one is already being worked.
    var queued = !!it.run_requested;
    var canRun = st !== 'closed' && !isLiveWip(it);
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
      + (canRun
          ? '<span class="q2-tdot-wrap' + (queued ? ' is-queued' : '') + '"'
            + ' data-q2-run="' + esc(ref) + '" data-q2-queued="' + (queued ? '1' : '0') + '"'
            + ' role="button" tabindex="0"'
            + ' title="' + esc(queued ? 'Queued to run - click to cancel' : dotTitle + ' - click to run now') + '">'
            + '<span class="q2-tdot" aria-hidden="true"></span>'
            + (queued ? ICON_TINY_STOP : ICON_TINY_PLAY)
            + '</span>'
          : '<span class="q2-tdot" title="' + esc(dotTitle) + '" aria-label="' + esc(dotTitle) + '"></span>')
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
    // Closed rows sort by last TOUCH, not by ref number. Ticket ids are
    // creation order, so a ticket reopened and re-closed an hour ago sank to
    // wherever it was filed (CCC-625 landed 37 rows down despite being the
    // most recently touched ticket in the queue). The row's age column shows
    // the same value, so the order it implies is the order you see.
    closed.sort(function (a, b) { return touchedAt(b) - touchedAt(a); });
    var recentClosedCutoff = Date.now() - RECENT_CLOSED_WINDOW_MS;
    function isRecentClosed(it) { return touchedAt(it) >= recentClosedCutoff; }
    var recentClosed = closed.filter(isRecentClosed);

    // Same counts renderer as the queue rows. Derived from the rows actually on
    // screen (so it honours the search filter) but split by the same statuses,
    // rather than lumping blocked and in-progress under "open".
    // The counts moved to the diagram below. This slot carries the queue's
    // worker configuration instead: engine, model and effort are always all
    // three shown, because "which of them is unset" is itself the answer when
    // a queue runs differently from how you remember configuring it.
    var qk = projectKey(state.queue);
    var cfg = (state.configs || {})[qk] || {};
    var own = (state.configsOwn || {})[qk] || {};
    // A value inherited from the wt defaults is shown dimmed, exactly like an
    // unset one — because it IS unset on this queue. Rendering the inherited
    // engine at full weight while the inherited model read "default model" was
    // the same fact told two different ways.
    function specPart(field, fallback) {
      var v = cfg[field];
      var isOwn = own[field] != null && own[field] !== '';
      return '<button type="button" class="q2-spec-v' + (isOwn ? '' : ' is-inherited') + '"'
        + ' data-q2-cfg-open="' + esc(field) + '"'
        + ' title="' + esc(field + ': ' + (v || 'unset')
            + (isOwn ? ' (set on this queue)' : ' (inherited default)') + ' - click to edit') + '">'
        + esc(v || fallback) + '</button>';
    }
    $('q2TicketCount').innerHTML = '<span class="q2-spec">'
      + specPart('engine', 'default engine')
      + '<span class="q2-spec-sep">&middot;</span>' + specPart('model', 'default model')
      + '<span class="q2-spec-sep">&middot;</span>' + specPart('effort', 'default effort')
      + '<button type="button" class="q2-spec-n" data-q2-cfg-open="desired_workers"'
      + ' title="Desired workers - click to edit">&times;'
      + esc(String(cfg.desired_workers != null ? cfg.desired_workers : 1)) + '</button>'
      + '</span>';

    if (!openish.length && !(state.showClosed ? closed.length : recentClosed.length)) {
      host.innerHTML = '<div class="q2-empty">'
        + '<div class="q2-empty-title">'
        + (needle ? 'No match' : (closed.length ? 'All clear in ' + esc(state.queue) : 'No tickets in ' + esc(state.queue)))
        + '</div>'
        + (closed.length && !needle ? 'Every ticket here is closed. Use Show closed to review them.' : '')
        + '</div>';
      return;
    }

    var shownClosed = state.showClosed ? closed.slice(0, state.closedCap) : recentClosed;
    var html = openish.map(ticketRow).join('');
    if (shownClosed.length) {
      var label = state.showClosed
        ? 'Closed' + (closed.length > shownClosed.length
          ? ' (newest ' + shownClosed.length + ' of ' + closed.length + ')' : '')
        : 'Recent closed · last 12h';
      html += '<div class="q2-group-label">' + esc(label) + '</div>' + shownClosed.map(ticketRow).join('');
      if (closed.length > shownClosed.length) {
        html += '<button type="button" class="q2-more-btn" data-q2-more>Show '
          + Math.min(CLOSED_CAP, closed.length - shownClosed.length)
          + ' more closed &middot; ' + (closed.length - shownClosed.length) + ' left</button>';
      }
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

  // Editable chips replace the property dropdowns. The values are already
  // shown as chips at the top of the pane, so a second copy in a sidebar was
  // duplication; clicking the chip itself cycles it. Order matters: the empty
  // string is last so a chip can always be cleared.
  var CYCLES = {
    priority:   ['p0', 'p1', 'p2', 'p3', ''],
    type:       ['bug', 'feature', ''],
    value:      ['H', 'M', 'L', ''],
    confidence: ['H', 'M', 'L', ''],
    readiness:  ['shovel-ready', 'needs-spec', 'needs-shaping', ''],
  };
  function nextInCycle(field, current) {
    var list = CYCLES[field] || [''];
    var i = list.indexOf(String(current || ''));
    return list[(i + 1) % list.length];
  }
  function editChip(field, label, value, cls) {
    return '<button type="button" class="q2-chip q2-echip' + (cls ? ' ' + cls : '')
      + (value ? '' : ' is-unset') + '"'
      + ' data-q2-cycle="' + esc(field) + '" data-q2-val="' + esc(value || '') + '"'
      + ' title="' + esc(field + ': ' + (value || 'unset') + ' — click to change') + '">'
      + esc(label) + '</button>';
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
    var showPrompt = !!prompt && prompt !== String(item.note || '').trim();
    var sid = sessionOf(item);
    var editCount = (Array.isArray(item.timeline) ? item.timeline : [])
      .filter(function (ev) { return ev && ev.event === 'edit'; }).length;
    var closed = st === 'closed';

    // Everything editable lives on one chip row, next to the read-only state.
    var chips = ''
      + '<span class="q2-status is-' + esc(st) + '">' + esc(statusLabel(st)) + '</span>'
      + (item.lane ? '<span class="q2-chip is-lane">' + esc(item.lane) + '</span>' : '')
      + editChip('type', item.type ? (TYPE_SHORT[item.type] || item.type) : 'type?',
                 item.type, item.type ? 'is-type-' + item.type : '')
      + editChip('priority', item.priority || 'prio?', item.priority,
                 item.priority ? 'is-prio-' + item.priority : '')
      + editChip('readiness', item.readiness ? (READY_SHORT[item.readiness] || 'ready') : 'ready?',
                 item.readiness, (item.readiness && !READY_OK[item.readiness]) ? 'is-unready' : '')
      + editChip('value', 'V:' + (item.value || '-'), item.value)
      + editChip('confidence', 'C:' + (item.confidence || '-'), item.confidence);

    // One toolbar, not four buried sections. Each write action arms an inline
    // form rather than living permanently expanded at the bottom of the pane.
    function act(k, label, cls) {
      return '<button type="button" class="q2-btn' + (cls ? ' ' + cls : '')
        + (state.arm === k ? ' is-armed' : '') + '" data-q2-arm="' + esc(k) + '">'
        + esc(label) + '</button>';
    }
    var runQueued = !!item.run_requested;
    var toolbar = '<div class="q2-actions">'
      + (closed ? '' :
          '<button type="button" class="q2-btn' + (runQueued ? ' is-armed' : '') + '"'
          + ' data-q2-act="run" title="' + (runQueued
              ? 'Queued to run. Click to cancel.'
              : 'Ask WatchTower to run this ticket next.') + '">'
          + (runQueued ? '&#9632; Queued' : '&#9654; Run now') + '</button>')
      + (closed ? act('reopen', 'Reopen') : act('close', 'Close'))
      + '<span class="q2-spacer"></span>'
      + '<button type="button" class="q2-btn" data-q2-act="copy">Copy prompt</button>'
      + '</div>';

    // Close and reopen stay armed: they are terminal and want a deliberate
    // press. Answer and comment do NOT — they are the two things you come to a
    // ticket to do, and hiding them behind a button made the ticket read-only
    // until you found the toolbar.
    var FORMS = {
      close:   ['Resolution summary (optional)', 'Mark as closed', 'close'],
      reopen:  ['Reason for reopening (optional)', 'Reopen ticket', 'reopen'],
    };
    var armed = FORMS[state.arm];
    var formHtml = armed
      ? '<section class="q2-sec q2-armed">'
        + (state.arm === 'answer' && item.block_question
            ? '<div class="q2-block-q">' + esc(item.block_question) + '</div>' : '')
        + '<textarea class="q2-input" data-q2-input="' + esc(armed[2]) + '" rows="2" autofocus'
        + ' placeholder="' + esc(armed[0]) + '" aria-label="' + esc(armed[1]) + '"></textarea>'
        + '<div class="q2-actrow">'
        + '<button type="button" class="q2-btn" data-q2-arm="">Cancel</button>'
        + '<button type="button" class="q2-btn q2-btn-primary" data-q2-act="' + esc(armed[2]) + '">'
        + esc(armed[1]) + '</button></div></section>'
      : '';

    // Assignment and origin: one compact strip at the very bottom, replacing
    // the fixed sidebar. It is reference data, not something you act on.
    var metaBits = [
      item.claimed_by ? 'worker <span class="q2-mono">' + esc(String(item.claimed_by).slice(0, 26)) + '</span>' : '',
      sid ? 'session ' + sessionBtn(sid, 'open in CCC') : '',
      item.claimed_at ? 'claimed ' + esc(relTime(item.claimed_at)) : '',
      item.closed_at ? 'closed ' + esc(relTime(item.closed_at)) : '',
      item.project ? 'queue ' + esc(item.project) : '',
      item.source ? 'source ' + esc(item.source) : '',
      item.repo_path ? '<span class="q2-mono" title="' + esc(item.repo_path) + '">' + esc(shortPath(item.repo_path)) + '</span>' : '',
      item.url ? '<a class="q2-linklike" href="' + esc(item.url) + '" target="_blank" rel="noopener">link &#8599;</a>' : '',
    ].filter(Boolean);

    var topHtml = ''
      + '<div class="q2-detail-head">'
      + '<span class="q2-detail-ref">' + esc(item.ref) + '</span>'
      + chips
      + '</div>'
      + '<h1 class="q2-detail-title">'
      + '<span class="q2-title-first">' + esc(parts[0]) + '</span>'
      + (parts[1] ? '<span class="q2-title-rest"> ' + esc(parts[1]) + '</span>' : '')
      + '</h1>'
      + toolbar
      + formHtml
      + (showPrompt
          ? '<section class="q2-sec"><div class="q2-sec-label">Full prompt</div>'
            + '<pre class="q2-pre">' + esc(prompt) + '</pre></section>'
          : '')
      + '<section class="q2-sec"><div class="q2-sec-label">Activity'
      + (editCount
          ? '<label class="q2-show-edits"><input type="checkbox" data-q2-show-edits> show edits (' + editCount + ')</label>'
          : '')
      + '</div>' + timelineHtml(item)
      // The answer box belongs WITH the question, at the end of the thread —
      // the agent is waiting on it and it should need no hunting.
      + (st === 'blocked'
          // The question is NOT repeated here. The timeline's last "Needs input"
          // event already shows it, immediately above, and printing it twice
          // read as two separate questions.
          ? '<div class="q2-inline q2-inline-answer">'
            + '<textarea class="q2-input" data-q2-input="answer" rows="2"'
            + ' placeholder="Answer the agent&hellip;" aria-label="Answer this ticket"></textarea>'
            + '<div class="q2-actrow"><button type="button" class="q2-btn q2-btn-primary"'
            + ' data-q2-act="answer">Send answer</button></div></div>'
          : '')
      // Comment sits after the last activity item, always available: it is a
      // reply to the thread, so it reads as the next entry in it.
      + '<div class="q2-inline">'
      + '<textarea class="q2-input" data-q2-input="comment" rows="2"'
      + ' placeholder="Add a comment - an update, not a resolution" aria-label="Add a comment"></textarea>'
      + '<div class="q2-actrow"><button type="button" class="q2-btn" data-q2-act="comment">Comment</button></div>'
      + '</div>'
      + '</section>'
      + (metaBits.length ? '<div class="q2-meta-strip">' + metaBits.join('<span class="q2-n-sep">·</span>') + '</div>' : '')
      + '<div class="q2-detail-foot"><span class="q2-dim">Filed '
      + '<span title="' + esc(item.created_at || '') + '">' + esc(relTime(item.created_at)) + '</span>'
      + (item.updated_at && item.updated_at !== item.created_at
          ? ' &middot; updated <span title="' + esc(item.updated_at) + '">' + esc(relTime(item.updated_at)) + '</span>' : '')
      + '</span></div>';

    // Only the top half is rewritten on a poll. The conversation iframe is a
    // sibling managed separately — replacing it every 5s reloaded the whole
    // embedded dashboard, which scrolled and flickered under the user.
    var top = host.querySelector('.q2-detail-top');
    if (!top) {
      host.innerHTML = '<div class="q2-detail-top"></div>';
      top = host.querySelector('.q2-detail-top');
    }
    // renderDetail runs on every poll. Without this, anything half-typed into
    // the always-open answer/comment boxes is destroyed 5 seconds later.
    var drafts = {}, focused = null, selStart = 0, selEnd = 0;
    top.querySelectorAll('[data-q2-input]').forEach(function (el) {
      var k = el.getAttribute('data-q2-input');
      if (el.value) drafts[k] = el.value;
      if (document.activeElement === el) {
        focused = k; selStart = el.selectionStart; selEnd = el.selectionEnd;
      }
    });
    top.innerHTML = topHtml;
    top.querySelectorAll('[data-q2-input]').forEach(function (el) {
      var k = el.getAttribute('data-q2-input');
      if (drafts[k] != null) el.value = drafts[k];
      if (focused === k) {
        el.focus();
        try { el.setSelectionRange(selStart, selEnd); } catch (_) {}
      }
    });
    syncConv(sid);
  }

  // Rebuilds the conversation block ONLY when the session or the open state
  // changes. Any other render leaves the existing iframe untouched, so it
  // keeps its scroll position, its input text, and its own polling.
  function syncConv(sid) {
    var host = $('q2Detail');
    if (!host) return;
    var existing = host.querySelector('.q2-conv');
    if (!sid) { if (existing) existing.remove(); return; }
    var open = convOpen();
    var want = sid + '|' + (open ? '1' : '0');
    if (existing && existing.getAttribute('data-conv') === want) return;
    if (existing) existing.remove();
    var el = document.createElement('div');
    el.className = 'q2-conv';
    el.setAttribute('data-conv', want);
    el.innerHTML = '<div class="q2-conv-head">'
      + '<button type="button" class="q2-conv-toggle" data-q2-conv-toggle aria-expanded="'
      + (open ? 'true' : 'false') + '" title="' + (open ? 'Collapse' : 'Expand') + ' the conversation">'
      + '<span class="q2-conv-caret" aria-hidden="true">' + (open ? '&#9660;' : '&#9650;') + '</span>'
      + 'Conversation</button>'
      + '<span class="q2-spacer"></span>'
      + '<span class="q2-dim q2-mono">' + esc(sid.slice(0, 8)) + '</span>'
      + '<a class="q2-linkbtn" href="/?ccc_popout=conversation&conv=' + encodeURIComponent(sid)
      + '" target="_blank" rel="noopener">pop out &#8599;</a>'
      + '</div>'
      + (open
          ? '<div class="q2-conv-body">'
            + '<iframe class="q2-conv-frame" title="Conversation ' + esc(sid) + '"'
            + ' src="/?ccc_popout=conversation&conv=' + encodeURIComponent(sid) + '"></iframe>'
            + '</div>'
          : '');
    host.appendChild(el);
    var frame = el.querySelector('.q2-conv-frame');
    if (frame) frame.addEventListener('load', function () { trimConvFrame(frame); });
  }

  // The popout is a full dashboard sized for a whole window, so inside a pane
  // it reads oversized and carries controls that make no sense here. It is
  // same-origin, so we can style it from outside instead of adding q2-specific
  // branches to app.js.
  // The blue tint has to be applied INSIDE the frame. Setting it on the
  // wrapper does nothing: the embedded document paints its own opaque
  // background over the whole box, which is why the pane still read black.
  function convTint() {
    var cs = getComputedStyle(document.documentElement);
    var blue = (cs.getPropertyValue('--wip-blue') || '#58a6ff').trim();
    var surface = (cs.getPropertyValue('--bg') || '#0d1117').trim();
    return 'color-mix(in srgb, ' + blue + ' 15%, ' + surface + ')';
  }

  var CONV_TRIM_CSS = [
    /* No zoom here: it shrinks the document inside a full-height viewport and
       leaves a dead band at the bottom. The iframe element is scaled instead
       (see .q2-conv-frame), which shrinks the viewport with it. */
    /* The row with the engine chip, headless/terminal pills, session id and
       Verbose/Replay/Annotate/Clear. It is .conv-pane-header, NOT #convToolbar
       (which the popout CSS already hides) — every control on it is a
       whole-session action that belongs in the main dashboard. */
    '#convToolbar { display: none !important; }',
    '.conv-pane-header, [data-role="pane-header"] { display: none !important; }',
    /* Floating Annotate button, only ever shown in popout contexts. */
    '#annotationFabBtn { display: none !important; }',
    /* Composer: two lines is plenty when the pane is half a column. */
    '#convInputBar textarea { max-height: 46px !important; min-height: 26px !important; }',
    '.conv-input-row-top { padding-top: 2px !important; padding-bottom: 2px !important; }',
    /* Workspace strip (clone/branch/ahead, context %, model) moves BELOW the
       composer. It is reference state, not something you act on before typing,
       and above the box it pushed the input down the pane. */
    '.conv-pane { display: flex !important; flex-direction: column !important; }',
    '.conv-input-context { order: 99 !important; margin-top: 2px !important; }',
    '#convInputBar { order: 98 !important; }',
    '.conv-input-bar { padding-top: 4px !important; padding-bottom: 4px !important; }',
    '.conv-pane, .conversations-view { padding-bottom: 0 !important; }',
    /* Tint every surface the embedded page paints, so the pane reads as a
       different material from the ticket detail above it. */
    'html, body, .conv-pane, .conversations-view, .conv-body, #convView,'
      + ' .conv-input-bar, .conv-pane-body { background: __TINT__ !important; }'
  ].join('\n');

  function trimConvFrame(frame) {
    try {
      var doc = frame.contentDocument;
      if (!doc || doc.getElementById('q2TrimStyle')) return;
      var st = doc.createElement('style');
      st.id = 'q2TrimStyle';
      frame.style.background = 'transparent';
      st.textContent = CONV_TRIM_CSS.replace(/__TINT__/g, convTint());
      (doc.head || doc.documentElement).appendChild(st);
    } catch (_) {
      // Cross-origin would land here. Same-origin today; if that ever changes
      // the frame simply renders untrimmed rather than breaking.
    }
  }

  // ── detail write actions ─────────────────────────────────────────────────
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

  async function saveField(field, value) {
    if (!state.ref) return;
    var payload = { ref: state.ref };
    payload[field] = value;
    try {
      await postJson('/api/ux-fixes/edit', payload);
      await loadDetail(state.ref);
      await refresh();
    } catch (err) {
      note('Could not save ' + field + ': ' + err.message);
    }
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

    // Run is a toggle: a second press on a still-queued ticket cancels it,
    // which is the contract /api/ux-fixes/run expects via `cancel`.
    if (act === 'run') {
      var queued = !!(state.detail && state.detail.run_requested);
      btn.disabled = true;
      try {
        await postJson('/api/ux-fixes/run', { ref: ref, cancel: queued });
        note(queued ? 'Run request cancelled' : 'Queued to run');
        await loadDetail(ref);
        await refresh();
      } catch (e) {
        note('Failed: ' + e.message);
      } finally { btn.disabled = false; }
      return;
    }

    // Field names are the SERVER's, not guesses. answer/close/reopen were sent
    // as answer/summary/reason and every one was rejected ("ref and text
    // required"); only comment happened to match. Verified against
    // server.py:52747 (answer.text), :52943 (close.note), :52890 (reopen.note),
    // :52913 (comment.text).
    var plan = {
      answer:  ['/api/ux-fixes/answer',  { ref: ref, text: detailInput('answer') },  true,  'Answer sent'],
      comment: ['/api/ux-fixes/comment', { ref: ref, text: detailInput('comment') }, true,  'Comment added'],
      close:   ['/api/ux-fixes/close',   { ref: ref, note: detailInput('close') },   false, 'Ticket closed'],
      reopen:  ['/api/ux-fixes/reopen',  { ref: ref, note: detailInput('reopen') },  false, 'Ticket reopened'],
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
      state.arm = '';
      var box = document.querySelector('[data-q2-input="' + act + '"]');
      if (box) box.value = '';
      // Re-fetch rather than patching local state, so what is on screen is
      // what the store actually holds. Deliberately NOT clearing state.detail
      // first: that would flash the "Loading" state and tear down the iframe.
      await loadDetail(ref);
      await refresh();
    } catch (e) {
      note('Failed: ' + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  // Edit events are bookkeeping noise by default; the toggle reveals them.
  document.addEventListener('change', function (e) {
    var box = e.target.closest && e.target.closest('[data-q2-show-edits]');
    if (!box) return;
    var tl = document.querySelector('.q2-tl');
    if (tl) tl.classList.toggle('show-edits', box.checked);
  });

  // ── modals: new ticket, queue configuration ──────────────────────────────
  function closeModal() {
    var host = $('q2Modal');
    if (!host) return;
    host.hidden = true;
    host.innerHTML = '';
    document.removeEventListener('keydown', modalKey, true);
  }
  function modalKey(e) {
    if (e.key === 'Escape') { e.preventDefault(); closeModal(); }
  }
  function openModal(html, onMount) {
    var host = $('q2Modal');
    if (!host) return;
    host.innerHTML = '<div class="q2-modal-backdrop" data-q2-modal-close></div>'
      + '<div class="q2-modal" role="dialog" aria-modal="true">' + html + '</div>';
    host.hidden = false;
    document.addEventListener('keydown', modalKey, true);
    if (onMount) onMount(host.querySelector('.q2-modal'));
  }

  // ── new ticket ───────────────────────────────────────────────────────────
  // Images paste or drop straight in: they upload to the same pasted-images
  // directory the rest of CCC uses and their path is appended to the note, so
  // the worker receives a path it can actually open.
  async function uploadImage(file) {
    var res = await fetch('/api/upload-image', {
      method: 'POST',
      headers: { 'Content-Type': file.type || 'image/png' },
      body: file,
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
    return data.path;
  }

  function openNewTicket() {
    if (!state.queue) { note('Pick a queue first.'); return; }
    var queue = state.queue;
    openModal(
      '<div class="q2-modal-head"><h2>New ticket in ' + esc(queue) + '</h2>'
      + '<button type="button" class="q2-icon-btn" data-q2-modal-close aria-label="Close">&times;</button></div>'
      + '<div class="q2-drop" data-q2-drop>'
      + '<div class="q2-drop-hint">Paste or drop an image</div>'
      + '<div class="q2-drop-thumbs" data-q2-thumbs></div>'
      + '</div>'
      + '<label class="q2-modal-label" for="q2TicketNote">Describe the fix</label>'
      + '<textarea class="q2-input q2-modal-text" id="q2TicketNote" rows="7"'
      + ' placeholder="What should the agent do?"></textarea>'
      + '<div class="q2-modal-foot">'
      + '<span class="q2-dim q2-modal-hint">&#8984;/Ctrl + Enter to file</span>'
      + '<button type="button" class="q2-btn" data-q2-modal-close>Cancel</button>'
      + '<button type="button" class="q2-btn q2-btn-primary" data-q2-file-ticket>Add ticket</button>'
      + '</div>',
      function (modal) {
        var ta = modal.querySelector('#q2TicketNote');
        var thumbs = modal.querySelector('[data-q2-thumbs]');
        var drop = modal.querySelector('[data-q2-drop]');

        async function take(files) {
          for (var i = 0; i < files.length; i++) {
            var f = files[i];
            if (!f || !/^image\//.test(f.type || '')) continue;
            var img = document.createElement('div');
            img.className = 'q2-thumb is-busy';
            img.innerHTML = '<span class="q2-spin" aria-hidden="true"></span>';
            thumbs.appendChild(img);
            try {
              var path = await uploadImage(f);
              img.className = 'q2-thumb';
              img.innerHTML = '<img alt="" src="/api/pasted-image?path=' + encodeURIComponent(path) + '">';
              img.title = path;
              // The path is what the worker acts on, so it goes in the note.
              ta.value = (ta.value ? ta.value.replace(/\s*$/, '') + '\n' : '') + path;
            } catch (e) {
              img.className = 'q2-thumb is-error';
              img.textContent = 'failed';
              note('Image upload failed: ' + e.message);
            }
          }
        }
        ta.addEventListener('paste', function (e) {
          var items = (e.clipboardData && e.clipboardData.files) || [];
          if (items.length) { e.preventDefault(); take(items); }
        });
        ['dragenter', 'dragover'].forEach(function (t) {
          drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add('is-over'); });
        });
        ['dragleave', 'drop'].forEach(function (t) {
          drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove('is-over'); });
        });
        drop.addEventListener('drop', function (e) {
          if (e.dataTransfer && e.dataTransfer.files) take(e.dataTransfer.files);
        });
        ta.addEventListener('keydown', function (e) {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            modal.querySelector('[data-q2-file-ticket]').click();
          }
        });
        ta.focus();
      });
  }

  // The modal closes as soon as the request is SENT, not when it returns.
  // /api/ux-fixes/enqueue writes the ticket and then calls
  // dispatch_after_enqueue, which can spawn a worker process before it
  // responds (server.py:52986). Awaiting that left the dialog sitting open for
  // seconds after the ticket already existed, which reads as a stuck dialog.
  // The draft is held so a genuine failure can hand it back instead of
  // discarding what the user typed.
  function fileTicket(btn) {
    var ta = document.getElementById('q2TicketNote');
    var noteText = ta ? String(ta.value || '').trim() : '';
    if (!noteText) { note('Describe the fix first.'); return; }
    var queue = state.queue;
    btn.disabled = true;
    closeModal();
    note('Filing ticket in ' + queue + '…');

    postJson('/api/ux-fixes/enqueue', { note: noteText, project: queue, source: 'ccc' })
      .then(async function (data) {
        var ref = data.item && data.item.ref;
        note(ref ? 'Filed ' + ref : 'Ticket filed');
        await refresh();
        if (ref) selectTicket(ref);
      })
      .catch(function (e) {
        note('Could not file ticket: ' + e.message);
        // Hand the text back rather than losing it.
        openNewTicket();
        var box = document.getElementById('q2TicketNote');
        if (box) box.value = noteText;
      });
  }

  // ── queue configuration ──────────────────────────────────────────────────
  // The same fields `wt config -q ...` takes, against the same endpoints the
  // main dashboard's manager uses. It cannot literally reuse that dialog:
  // openQueueManager lives inside app.js's closure and this page does not load
  // app.js — that is the cost of the fork, paid here deliberately.
  function opt(v, label, cur) {
    return '<option value="' + esc(v) + '"' + (String(cur || '') === v ? ' selected' : '') + '>'
      + esc(label) + '</option>';
  }
  function field(label, inner, hint) {
    return '<label class="q2-field"><span class="q2-field-k">' + esc(label) + '</span>'
      + inner + (hint ? '<span class="q2-field-hint">' + esc(hint) + '</span>' : '') + '</label>';
  }

  async function openQueueConfig(queueName, focusField) {
    var options;
    try {
      options = await postJson('/api/queue/config-options', {});
    } catch (e) {
      note('Could not load queue options: ' + e.message);
      return;
    }
    var isNew = !queueName;
    var existing = (options.queues || []).filter(function (q) {
      return projectKey(q.queue) === projectKey(queueName);
    })[0];
    var c = Object.assign({}, options.defaults || {}, (existing && existing.config) || {});
    var models = options.models_by_engine || {};
    var engine = c.engine || 'claude';
    var types = Array.isArray(c.claim_types) ? c.claim_types : [];

    function modelOptions(eng, cur) {
      var list = models[eng] || [];
      return opt('', 'default', cur)
        + list.map(function (m) { return opt(m, m, cur); }).join('');
    }

    openModal(
      '<div class="q2-modal-head"><h2>' + (isNew ? 'New queue' : 'Queue ' + esc(queueName)) + '</h2>'
      + '<button type="button" class="q2-icon-btn" data-q2-modal-close aria-label="Close">&times;</button></div>'
      + '<div class="q2-fields">'
      + field('Name', '<input class="q2-input" data-q2-cfg="queue" value="' + esc(queueName || '')
          + '"' + (isNew ? '' : ' readonly') + ' placeholder="MYQUEUE">',
          isNew ? '1-64 letters, numbers, _ or -' : 'Renaming is not supported here')
      + field('Repo path', '<input class="q2-input" data-q2-cfg="repo_path" list="q2RepoPaths" value="'
          + esc(c.repo_path || '') + '" placeholder="/Users/you/Apps/project">')
      + '<datalist id="q2RepoPaths">'
      + (options.repo_paths || []).map(function (p) { return '<option value="' + esc(p) + '">'; }).join('')
      + '</datalist>'
      + field('Backend', '<select class="q2-input" data-q2-cfg="backend">'
          + opt('file', 'file (local tickets)', c.backend) + opt('github', 'github issues', c.backend)
          + '</select>')
      + field('GitHub repo', '<input class="q2-input" data-q2-cfg="github_repo" list="q2GhRepos" value="'
          + esc(c.github_repo || '') + '" placeholder="owner/repo">')
      + '<datalist id="q2GhRepos">'
      + (options.github_repos || []).map(function (r) { return '<option value="' + esc(r) + '">'; }).join('')
      + '</datalist>'
      + field('GitHub assignee', '<input class="q2-input" data-q2-cfg="github_assignee" value="'
          + esc(c.github_assignee || '') + '">')
      + field('Engine', '<select class="q2-input" data-q2-cfg="engine">'
          + Object.keys(models).map(function (e) { return opt(e, e, engine); }).join('')
          + '</select>')
      + field('Model', '<select class="q2-input" data-q2-cfg="model">' + modelOptions(engine, c.model) + '</select>')
      + field('Effort', '<select class="q2-input" data-q2-cfg="effort">'
          + opt('', 'default', c.effort) + ['low', 'medium', 'high', 'xhigh'].map(function (x) {
              return opt(x, x, c.effort); }).join('')
          + '</select>')
      + field('Desired workers', '<input class="q2-input" type="number" min="0" max="16"'
          + ' data-q2-cfg="desired_workers" value="' + esc(String(c.desired_workers != null ? c.desired_workers : 1)) + '">')
      + field('Auto-drain', '<select class="q2-input" data-q2-cfg="auto_drain">'
          + opt('false', 'off', String(!!c.auto_drain)) + opt('true', 'on', String(!!c.auto_drain))
          + '</select>')
      + field('Claim types', '<span class="q2-checkrow">'
          + '<label><input type="checkbox" data-q2-claim="bug"' + (types.indexOf('bug') !== -1 ? ' checked' : '') + '> bug</label>'
          + '<label><input type="checkbox" data-q2-claim="feature"' + (types.indexOf('feature') !== -1 ? ' checked' : '') + '> feature</label>'
          + '</span>', 'Neither ticked means every type')
      + '</div>'
      + '<div class="q2-modal-foot">'
      // These fields override the SYSTEM spawn defaults. When a queue leaves
      // one unset it falls through to those, so the form has to say where they
      // live — otherwise "default model" is a dead end.
      + '<a class="q2-linklike q2-modal-sys" href="/?ccc_settings=sessions" target="_blank" rel="noopener"'
      + ' title="Open CCC settings: Sessions &amp; Spawning">System spawn defaults &#8599;</a>'
      + '<span class="q2-dim q2-modal-hint">Saving replaces the whole config, as `wt config` does</span>'
      + '<button type="button" class="q2-btn" data-q2-modal-close>Cancel</button>'
      + '<button type="button" class="q2-btn q2-btn-primary" data-q2-save-queue>Save</button>'
      + '</div>',
      function (modal) {
        // Model list follows the engine, or it offers models the engine cannot run.
        var eng = modal.querySelector('[data-q2-cfg="engine"]');
        var mod = modal.querySelector('[data-q2-cfg="model"]');
        eng.addEventListener('change', function () { mod.innerHTML = modelOptions(eng.value, ''); });
        // Land on the field the user clicked, not the top of the form.
        var target = focusField && modal.querySelector('[data-q2-cfg="' + focusField + '"]');
        var first = target || modal.querySelector('[data-q2-cfg="' + (isNew ? 'queue' : 'repo_path') + '"]');
        if (first) {
          first.focus();
          if (target) {
            target.closest('.q2-field').classList.add('is-target');
            target.scrollIntoView({ block: 'center' });
          }
        }
      });
  }

  async function saveQueueConfig(btn) {
    var modal = document.querySelector('.q2-modal');
    if (!modal) return;
    var payload = {};
    modal.querySelectorAll('[data-q2-cfg]').forEach(function (el) {
      payload[el.getAttribute('data-q2-cfg')] = el.value;
    });
    payload.auto_drain = payload.auto_drain === 'true';
    payload.desired_workers = parseInt(payload.desired_workers, 10) || 0;
    payload.claim_types = [].slice.call(modal.querySelectorAll('[data-q2-claim]'))
      .filter(function (el) { return el.checked; })
      .map(function (el) { return el.getAttribute('data-q2-claim'); });
    if (!String(payload.queue || '').trim()) { note('Queue name is required.'); return; }
    btn.disabled = true;
    try {
      var data = await postJson('/api/queue/config', payload);
      closeModal();
      note('Saved ' + (data.queue || payload.queue));
      // The config write invalidates the drain/claim overrides we were holding.
      var k = projectKey(data.queue || payload.queue);
      delete drainOverride[k]; delete typesOverride[k];
      await loadConfigs();
      await refresh();
      selectQueue(data.queue || payload.queue);
    } catch (e) {
      note('Could not save queue: ' + e.message);
      btn.disabled = false;
    }
  }

  // Run-now straight from the ticket list. Same endpoint as the detail pane's
  // button; a second press on a still-queued ticket cancels it.
  async function runTicket(ref, queued) {
    try {
      await postJson('/api/ux-fixes/run', { ref: ref, cancel: !!queued });
      note(queued ? 'Run cancelled for ' + ref : 'Queued ' + ref + ' to run');
      if (state.ref === ref) await loadDetail(ref);
      await refresh();
    } catch (e) {
      note('Could not run ' + ref + ': ' + e.message);
    }
  }

  function renderAll() {
    renderChrome();
    renderQueues();
    renderDiagram();
    renderLogBar();
    renderTickets();
    // The detail pane owns its own fetch; only repaint from cache here so a
    // 5s poll can't flicker the pane the user is reading.
    renderDetail();
  }

  // ── events ───────────────────────────────────────────────────────────────
  // Selection survives a reload. Without this every refresh dropped the user
  // back on whichever queue happened to sort first, which on a 32-queue board
  // means losing your place on every code change.
  var LS_QUEUE = 'ccc-q2-queue';
  var LS_REF = 'ccc-q2-ref';
  function rememberSelection() {
    try {
      if (state.queue) localStorage.setItem(LS_QUEUE, state.queue);
      else localStorage.removeItem(LS_QUEUE);
      if (state.ref) localStorage.setItem(LS_REF, state.ref);
      else localStorage.removeItem(LS_REF);
    } catch (_) {}
  }

  function selectQueue(name) {
    if (projectKey(name) === projectKey(state.queue)) return;
    state.queue = name;
    state.ref = '';
    state.detail = null;
    state.search = '';
    state.closedCap = CLOSED_CAP;
    var search = $('q2Search');
    if (search) search.value = '';
    rememberSelection();
    state.log = [];
    renderAll();
    loadLog(name).then(renderLogBar);
  }

  function selectTicket(ref) {
    if (ref === state.ref) return;
    state.ref = ref;
    state.detail = null;
    state.arm = '';
    rememberSelection();
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
      setDrainMode(drain.getAttribute('data-q2-drain'), drain.getAttribute('data-q2-mode'));
      return;
    }
    var runDot = e.target.closest('[data-q2-run]');
    if (runDot) {
      e.stopPropagation();
      runTicket(runDot.getAttribute('data-q2-run'), runDot.getAttribute('data-q2-queued') === '1');
      return;
    }
    var act = e.target.closest('[data-q2-act]');
    if (act) { e.stopPropagation(); detailAction(act.getAttribute('data-q2-act'), act); return; }
    var qBtn = e.target.closest('[data-q2-queue]');
    if (qBtn) { selectQueue(qBtn.getAttribute('data-q2-queue')); return; }
    var tBtn = e.target.closest('[data-q2-ref]');
    if (tBtn) { selectTicket(tBtn.getAttribute('data-q2-ref')); return; }
    var convT = e.target.closest('[data-q2-conv-toggle], .q2-conv-head');
    if (convT && !e.target.closest('a')) {
      setConvOpen(!convOpen());
      renderDetail();
      return;
    }
    var arm = e.target.closest('[data-q2-arm]');
    if (arm) {
      e.stopPropagation();
      var want = arm.getAttribute('data-q2-arm');
      state.arm = (state.arm === want) ? '' : want;
      renderDetail();
      var ta = document.querySelector('[data-q2-input]');
      if (ta) ta.focus();
      return;
    }
    var cyc = e.target.closest('[data-q2-cycle]');
    if (cyc) {
      e.stopPropagation();
      var field = cyc.getAttribute('data-q2-cycle');
      saveField(field, nextInCycle(field, cyc.getAttribute('data-q2-val')));
      return;
    }
    // The whole log header toggles, not just the caret.
    var logHead = e.target.closest('.q2-logbar-head');
    if (logHead && !e.target.closest('a')) {
      try { localStorage.setItem(LS_LOG_OPEN, logOpen() ? '0' : '1'); } catch (_) {}
      renderLogBar();
      return;
    }
    var logT = e.target.closest('[data-q2-log-toggle]');
    if (logT) {
      try { localStorage.setItem(LS_LOG_OPEN, logOpen() ? '0' : '1'); } catch (_) {}
      renderLogBar();
      return;
    }
    if (e.target.closest('[data-q2-more]')) {
      state.closedCap += CLOSED_CAP;
      renderTickets();
      return;
    }
    if (e.target.closest('#q2ClosedBtn')) { state.showClosed = !state.showClosed; renderTickets(); return; }
    if (e.target.closest('#q2ThemeBtn')) { toggleTheme(); return; }
    if (e.target.closest('[data-q2-modal-close]')) { closeModal(); return; }
    if (e.target.closest('#q2NewTicketBtn')) { openNewTicket(); return; }
    if (e.target.closest('#q2NewQueueBtn')) { openQueueConfig(''); return; }
    var cfgOpen = e.target.closest('[data-q2-cfg-open]');
    if (cfgOpen) {
      if (!state.queue) { note('Pick a queue first.'); return; }
      openQueueConfig(state.queue, cfgOpen.getAttribute('data-q2-cfg-open'));
      return;
    }
    if (e.target.closest('#q2QueueSettingsBtn')) {
      if (!state.queue) { note('Pick a queue first.'); return; }
      openQueueConfig(state.queue);
      return;
    }
    var fileBtn = e.target.closest('[data-q2-file-ticket]');
    if (fileBtn) { fileTicket(fileBtn); return; }
    var saveQ = e.target.closest('[data-q2-save-queue]');
    if (saveQ) { saveQueueConfig(saveQ); return; }
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
      var roomCap = window.innerWidth - COLS[other].min - 240;
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

  // Glyph buttons in the markup are filled with drawn icons here, so the page
  // never depends on a font shipping a decent gear or plus.
  document.querySelectorAll('[data-q2-icon]').forEach(function (el) {
    var k = el.getAttribute('data-q2-icon');
    el.innerHTML = k === 'gear' ? ICON_GEAR : ICON_PLUS;
  });

  loadConfigs().then(renderAll);

  // ── boot ─────────────────────────────────────────────────────────────────
  refresh();
  setInterval(function () {
    if (document.hidden) return;
    refresh();
  }, POLL_MS);
})();
