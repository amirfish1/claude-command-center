/* Copyright (c) 2026 Amir Fish. All rights reserved. SPDX-License-Identifier: LicenseRef-CCC-Software-License */
(function () {
  'use strict';

  // Slice 4 of the codex-single-renderer-merge plan (see
  // CCC-private-docs/plans/2026-09-12-codex-single-renderer-merge.md).
  //
  // Behind localStorage.cccCodexLiveOverlay ('1' to enable). OFF by default --
  // when the flag is absent/false this module never fetches anything and
  // static/codex-client.js's attachInline runs exactly as it does today.
  //
  // What this does when the flag is on: poll the `live-transcript` action
  // added in Slice 1 (ccc_server/codex_client.py's codex_client_dispatch,
  // mapped by ccc_server/codex_live_events.py) for one Codex pane's active
  // thread, and feed the resulting provisional events into the SAME shared
  // renderConversationEvents() the rollout JSONL already draws with -- via
  // the `data-live-key` upsert/reconcile path Slice 2 built (app.js's
  // _upsertProvisionalNode/_findMatchingProvisionalNode, invoked from
  // renderConversationEvents's per-event loop). This module never renders
  // anything itself; it only fetches and hands events to
  // window.CCCCodexRenderLiveEvents (a thin app.js export added for this).
  //
  // Lifecycle: static/codex-client.js's attachInline is the thing that
  // decides WHEN a pane looks at a Codex thread (pane paint, engine
  // detection, workspace fetch, conversation-selected). Rather than duplicate
  // that wiring with a second DOM watcher, this module exposes start()/stop()
  // and codex-client.js + app.js call start() at the same points they used to
  // call attachInline unconditionally (see codex-client.js's
  // upgradePaintedPane and app.js's renderConversationEvents trailing Codex
  // hook). start() is idempotent and cheap to call repeatedly.
  //
  // Cursors (per the plan's "2. Data path"): the rollout keeps convLastLine
  // (app.js, untouched by this module). This module keeps
  // {generation, cursor} per pane -- a generation change (server restart /
  // reconnect) clears only the provisional nodes already on screen; a
  // cursor change alone is not treated as a resync signal (the live-transcript
  // action always returns a fresh full snapshot, not an incremental delta).
  //
  // Dedupe against rollout rows is NOT this module's job -- it always hands
  // renderConversationEvents the full current overlay (turn_id + live_key on
  // every event), and the reconcile logic Slice 2 built there (tool call_id,
  // then turn_id+ordinal, then normalized text) decides whether a rollout row
  // has already superseded a given provisional node.

  const FLAG_KEY = 'cccCodexLiveOverlay';
  const POLLER_NAME = 'codexLiveOverlay';
  const MAX_FAILURE_BACKOFF_STEPS = 4;

  // Overridable only for tests (see tests/codex-live-source.test.cjs) so a
  // "stops polling" assertion doesn't need to wait 900ms+ of real time. Never
  // set in production.
  function pollBaseMs() { return Number(window.__CCC_TEST_LIVE_POLL_MS) || 900; }
  function pollMaxMs() { return Number(window.__CCC_TEST_LIVE_POLL_MAX_MS) || 6000; }

  function overlayEnabled() {
    try { return localStorage.getItem(FLAG_KEY) === '1'; } catch (_) { return false; }
  }

  function pollerOff() {
    return !!(window.__pollersOff && window.__pollersOff[POLLER_NAME]);
  }

  // One entry per pane element currently under this module's control.
  // {paneEl, paneId, context:{threadId,repoPath,environmentId}, generation,
  //  failures, active, inFlight, timer}
  const panes = new Map();

  function contextFor(paneEl) {
    if (!paneEl || typeof window.CCCCodexClientContext !== 'function') return null;
    const context = window.CCCCodexClientContext(paneEl);
    if (!context || !context.threadId || !context.repoPath) return null;
    return context;
  }

  function stop(paneEl) {
    const entry = panes.get(paneEl);
    if (!entry) return;
    window.clearTimeout(entry.timer);
    panes.delete(paneEl);
  }

  // A turn is still "live" -- worth polling for -- while any turn in the
  // overlay is in progress, or a request is waiting on the user. Matches the
  // plan's "Poll only while a turn is running or requests are pending."
  function isTurnActive(turns, requests) {
    const running = Array.isArray(turns) && turns.some((turn) => turn && String(turn.status || '') === 'inProgress');
    const pending = Array.isArray(requests) && requests.length > 0;
    return !!(running || pending);
  }

  // {turns:[{turn_id,status,events}]} -> one flat, turn-ordered events array
  // for renderConversationEvents. live_turns_from_snapshot already returns
  // turns oldest-first and events in item order within a turn.
  function flattenTurns(turns) {
    const events = [];
    if (!Array.isArray(turns)) return events;
    for (const turn of turns) {
      if (turn && Array.isArray(turn.events)) {
        for (const event of turn.events) events.push(event);
      }
    }
    return events;
  }

  // Remove only the provisional overlay -- confirmed (line-keyed) rollout
  // rows are untouched. Used on a generation change/resync.
  function clearProvisional(viewEl) {
    if (!viewEl || typeof viewEl.querySelectorAll !== 'function') return;
    viewEl.querySelectorAll('.event[data-live-key]').forEach((node) => node.remove());
  }

  async function fetchLiveTranscript(context) {
    const params = new URLSearchParams({ thread_id: context.threadId, repo_path: context.repoPath });
    if (context.environmentId) params.set('environment_id', context.environmentId);
    const response = await fetch('/api/codex/client/live-transcript?' + params.toString(), { cache: 'no-store' });
    let data = null;
    try { data = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok || !data || data.ok === false) {
      throw new Error((data && data.error) || 'live-transcript request failed');
    }
    return data;
  }

  // Apply one live-transcript response to a pane's view. Returns whether
  // polling should continue (a turn is running or a request is pending).
  function applySnapshot(entry, data) {
    const paneEl = entry.paneEl;
    if (!paneEl || !paneEl.isConnected) return false;
    const viewEl = paneEl.querySelector('.conversations-view');
    if (!viewEl) return entry.active;
    const turns = Array.isArray(data.turns) ? data.turns : [];
    const requests = Array.isArray(data.requests) ? data.requests : [];
    if (entry.generation != null && data.generation !== entry.generation) {
      // Resync: the store's generation changed under us (server restart or
      // app-server reconnect). The rollout-derived rows are unaffected --
      // only the provisional overlay is now suspect.
      clearProvisional(viewEl);
    }
    entry.generation = data.generation;
    entry.cursor = data.cursor;
    const events = flattenTurns(turns);
    if (events.length && typeof window.CCCCodexRenderLiveEvents === 'function') {
      window.CCCCodexRenderLiveEvents(entry.paneId, events);
    }
    return isTurnActive(turns, requests);
  }

  async function pollOnce(entry) {
    if (entry.inFlight) return;
    if (!entry.paneEl || !entry.paneEl.isConnected) { stop(entry.paneEl); return; }
    const freshContext = contextFor(entry.paneEl);
    if (!freshContext || freshContext.threadId !== entry.context.threadId) {
      // The pane moved to a different thread (or lost engine context)
      // underneath us -- a future start() call re-establishes a fresh entry
      // for whatever thread is there now.
      stop(entry.paneEl);
      return;
    }
    entry.context = freshContext;
    entry.paneId = freshContext.paneId || entry.paneId;
    entry.inFlight = true;
    if (typeof window._pollerTickManual === 'function') { try { window._pollerTickManual(POLLER_NAME); } catch (_) {} }
    try {
      const data = await fetchLiveTranscript(entry.context);
      entry.failures = 0;
      entry.active = applySnapshot(entry, data);
    } catch (_) {
      // A transient fetch failure doesn't prove the turn ended -- keep
      // polling (with backoff) rather than silently going stale.
      entry.failures = (entry.failures || 0) + 1;
      entry.active = true;
    } finally {
      entry.inFlight = false;
    }
  }

  function schedule(entry) {
    window.clearTimeout(entry.timer);
    entry.timer = null;
    if (!panes.has(entry.paneEl)) return;
    if (!overlayEnabled() || pollerOff()) { stop(entry.paneEl); return; }
    if (!entry.active) return; // idle: nothing running, nothing pending. A later start() resumes it.
    const backoffSteps = Math.min(entry.failures || 0, MAX_FAILURE_BACKOFF_STEPS);
    const delay = Math.min(pollMaxMs(), pollBaseMs() * Math.pow(2, backoffSteps));
    entry.timer = window.setTimeout(() => {
      pollOnce(entry).then(() => schedule(entry));
    }, delay);
  }

  // Start (or resume) polling for one Codex pane. Safe to call repeatedly --
  // callers invoke this at every point that used to call attachInline
  // unconditionally (pane paint, rollout re-render, workspace fetch); a pane
  // already being actively polled is a cheap no-op.
  function start(paneEl) {
    if (!overlayEnabled() || pollerOff() || !paneEl) return;
    const context = contextFor(paneEl);
    if (!context) return;
    let entry = panes.get(paneEl);
    if (!entry) {
      entry = {
        paneEl, paneId: context.paneId, context,
        generation: null, cursor: null, failures: 0,
        active: true, inFlight: false, timer: null,
      };
      panes.set(paneEl, entry);
      pollOnce(entry).then(() => schedule(entry));
      return;
    }
    entry.context = context;
    entry.paneId = context.paneId || entry.paneId;
    if (!entry.timer && !entry.inFlight) {
      // A previously-idle pane (turn had completed, nothing pending) --
      // something changed (a new message was sent, a request appeared).
      // Resume rather than wait for the pane to be recreated.
      entry.active = true;
      pollOnce(entry).then(() => schedule(entry));
    }
  }

  // Mirrors codex-client.js's own retire-on-select listener: a pane whose
  // thread changed stops polling for the OLD thread immediately instead of
  // waiting for the next poll tick to notice.
  window.addEventListener('ccc:conversation-selected', (event) => {
    const detail = event.detail || {};
    const entry = detail.paneEl && panes.get(detail.paneEl);
    if (entry && detail.threadId !== entry.context.threadId) stop(detail.paneEl);
  });

  window.CCCCodexLiveSource = {
    enabled: overlayEnabled,
    start,
    stop,
    __testing: { flattenTurns, isTurnActive, clearProvisional, applySnapshot, fetchLiveTranscript, panes },
  };
})();
