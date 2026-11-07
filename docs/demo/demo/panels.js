// Invented data for the Decisions and Workers panels. backend.js asks this file
// first (window.__tryDemoExtra) and falls back to its own answers. Everything is
// made up for a pretend tea shop and lives in memory only.
(function () {
  'use strict';
  const t0 = Date.now();
  const iso = (agoMin) => new Date(t0 - agoMin * 60000).toISOString();
  const opt = (label, detail, kind, extra) => Object.assign({ label, detail, recommended: false, action: { kind } }, extra || {});

  const cards = [
    { id: 'dc_demo01', kind: 'wt_review', status: 'open', severity: 'warn', title: '[Q101:refund-window] Accept the refund-window change?',
      context: 'The checkout agent changed the refund window from 14 to 30 days and added a test. The tests pass; the policy page still says 14 days.',
      source_id: 'SHOP-41', source: { reviewer: 'you', commit: '4be09f2' }, created_at: iso(95), updated_at: iso(20), seen_count: 1,
      options: [opt('Accept the change', 'Merge it and update the policy page next.', 'wt_accept', { recommended: true }),
        opt('Reject with a note', 'Send it back to the agent with what must change.', 'wt_reject'), opt('I will do it', 'Mark as yours to handle.', 'human')] },
    { id: 'dc_demo02', kind: 'board', status: 'open', severity: 'info', title: '[Q102:gift-wrap-price] Price for gift wrap',
      context: 'The cart agent built the gift wrap option but nobody has set a price. It will ship free until you decide.',
      source_id: 'TEA-OPS-7', source: { category: 'Cart', status: 'blocked', notes: 'Needs a price' }, created_at: iso(240), updated_at: iso(60), seen_count: 2,
      options: [opt('Free for now', 'Ship it free and revisit after a week.', 'human', { recommended: true }), opt('Charge $3', 'Add a $3 line to the cart total.', 'inject'),
        opt('Snooze', 'Ask again tomorrow.', 'snooze')] },
    { id: 'dc_demo03', kind: 'wt', status: 'open', severity: 'critical', title: 'The SHOP queue has stalled with 3 tickets open',
      context: 'No worker has claimed a ticket in 40 minutes. Auto-drain is on. The last worker exited after a failed test run.',
      source_id: 'queue:SHOP', source: { tickets: ['SHOP-44 Warehouse webhook retries', 'SHOP-45 Order history paging', 'SHOP-46 Sitemap'] },
      created_at: iso(42), updated_at: iso(5), seen_count: 4,
      options: [opt('Start a worker', 'Spawn one agent to drain the queue.', 'spawn', { recommended: true, cost: 'about $0.40' }), opt('Pause the queue', 'Stop auto-drain until you look.', 'pause'),
        opt('Snooze', 'Ask again in an hour.', 'snooze')] },
    { id: 'dc_demo04', kind: 'session', status: 'open', severity: 'info', title: 'An idle session has unfinished work',
      context: 'The newsletter agent stopped after drafting two of four emails and is waiting for a reply.',
      source_id: 'session:newsletter', source: { goal: 'Draft the autumn newsletter series', last_assistant_text: 'Drafted emails 1 and 2. Want a different tone for the rest?' },
      created_at: iso(300), updated_at: iso(150), seen_count: 1,
      options: [opt('Steer it', 'Tell it to finish in the same tone.', 'inject', { recommended: true }), opt('I will do it', 'Mark as yours to handle.', 'human')] },
    { id: 'dc_demo05', kind: 'external', status: 'closed', severity: 'info', title: '[Q99:logo-colour] Which logo colour?',
      context: 'Answered yesterday: the green one.', source_id: 'report:logo', source: { source: 'design agent', detail: 'Submitted a question' },
      created_at: iso(1700), updated_at: iso(1500), seen_count: 1, decided: { option: 0, at: iso(1500), result: { ok: true, effect: 'sent to the design agent' } },
      options: [opt('The green one', 'Use the green logo.', 'inject'), opt('The brown one', 'Use the brown logo.', 'inject')] },
  ];
  const byId = new Map(cards.map(c => [c.id, c]));
  const openCount = () => cards.filter(c => c.status === 'open').length;

  // Workers: two queues, two live workers, a few finished ones. No session ids,
  // so a worker row is a plain label rather than a link to a transcript.
  const workers = () => [
    { worker_id: 'shop-worker-1', queue: 'SHOP', model: 'claude-sonnet-5-5', started_at: iso(12), alive: true, ticket: 'SHOP-44', session_id: '' },
    { worker_id: 'ops-worker-1', queue: 'TEA-OPS', model: 'codex', started_at: iso(4), alive: true, ticket: 'TEA-OPS-9', session_id: '' }];
  const queues = () => [
    { queue: 'SHOP', depth: 3, closed: 11, total: 14, auto_drain: true, stuck: false, claimable: 3, workers: 1, last_activity_seconds: 120 },
    { queue: 'TEA-OPS', depth: 2, closed: 5, total: 7, auto_drain: true, stuck: false, claimable: 2, workers: 1, last_activity_seconds: 240 },
    { queue: 'NEWSLETTER', depth: 0, closed: 4, total: 4, auto_drain: false, stuck: false, claimable: 0, workers: 0, last_activity_seconds: 9000 }];
  const past = () => [
    { worker_id: 'shop-worker-0', queue: 'SHOP', ended_ago_seconds: 2700, ended_at_iso: iso(45), session_id: '' },
    { worker_id: 'news-worker-2', queue: 'NEWSLETTER', ended_ago_seconds: 14400, ended_at_iso: iso(240), session_id: '' }];

  // Tickets: a few closed, a few claimed by the live workers, a few waiting.
  const ticket = (queue, n, title, status, claimedMin, closedMin, worker, summary, note) => ({
    ref: queue + '-' + n, number: n, queue, title, status, type: 'feature', priority: 'normal',
    claimed_by: claimedMin == null ? '' : worker, claimed_at: claimedMin == null ? '' : iso(claimedMin),
    closed_at: closedMin == null ? '' : iso(closedMin), closed_by: closedMin == null ? '' : worker,
    resolution: closedMin == null ? undefined : { summary },
    progress_notes: note ? [{ at: iso(claimedMin - 0.01 > 0 ? Math.max(1, claimedMin - 5) : 1), text: note, by: worker }] : [] });
  const tickets = () => [
    ticket('SHOP', 44, 'Warehouse webhook retries', 'in_progress', 12, null, 'shop-worker-1', '', 'Added retry with backoff; writing the duplicate-event test.'),
    ticket('SHOP', 45, 'Order history paging', 'open', null, null, '', ''),
    ticket('SHOP', 46, 'Sitemap for the shop pages', 'open', null, null, '', ''),
    ticket('SHOP', 43, 'Refund window copy', 'closed', 70, 45, 'shop-worker-0', 'Updated the policy page and added a test for the 30-day window.'),
    ticket('TEA-OPS', 9, 'Reorder alerts for low stock', 'in_progress', 4, null, 'ops-worker-1', '', 'Reading the stock sheet to find the lowest items.'),
    ticket('TEA-OPS', 8, 'Supplier price import', 'closed', 190, 130, 'ops-worker-0', 'Imported 41 invented prices and flagged 3 that changed by more than 20%.'),
    ticket('NEWSLETTER', 4, 'Autumn series, email 4', 'closed', 300, 240, 'news-worker-2', 'Drafted the last email and a subject line test.')];

  window.__tryDemoExtra = {
    get(path) {
      if (path === '/api/decision-inbox') {
        return { ok: true, cards, open_count: openCount(), total_count: cards.length, queue_repos: {}, governor: { findings: [
          { kind: 'long_idle', name: 'Newsletter drafts', detail: 'Idle for 2.5 hours with unfinished work.', session_id: 'demo-gov-1' }] },
          last_run: { finished_at: iso(8), errors: [] }, running_since: null, config: { enabled: true, interval_s: 3600 } };
      }
      if (path === '/api/decision-inbox/runs') {
        return { ok: true, decision_total: 3, hidden_count: 12, invalid_records: 0, decision_rows: [
          { id: 'dc_demo03', run_id: 'run-3', created_at: iso(42), title: 'The SHOP queue has stalled', source: { reason: 'Scheduled scan' }, llm_used: false, llm_calls: 0 },
          { id: 'dc_demo01', run_id: 'run-2', created_at: iso(95), title: 'Accept the refund-window change?', source: { reason: 'Scheduled scan' }, llm_used: true, llm_calls: 1 },
          { id: 'dc_demo02', run_id: 'run-1', created_at: iso(240), title: 'Price for gift wrap', source: { reason: 'Reported by an agent' }, llm_used: true, llm_calls: 1 }] };
      }
      if (path === '/api/decision-inbox/legacy-reconciliation') return { ok: true, cards: [] };
      if (path === '/api/decision-inbox/requests') return { ok: true, requests: [], answers_enabled: false, source_current: true, needs_answer_count: 0, coverage_issues: [], checked_at: iso(0) };
      if (path === '/api/queue/status') return { ok: true, projects: [], wt_workers: workers(), queues: queues(), past_workers: past(), worker_session_ids: [], worker_session_map: {}, github_sync: null };
      if (path === '/api/queue/list') return { ok: true, items: tickets(), synced_at: iso(0), version: '' };
      if (path === '/api/wt/workers') return { ok: true, workers: workers() };
      return undefined;
    },
    post(path, body) {
      if (path === '/api/decision-inbox/decide') {
        const c = byId.get(body && body.card_id);
        if (!c) return { ok: false, error: 'Unknown demo decision' };
        const o = c.options[body.option] || {};
        c.status = 'closed'; c.updated_at = iso(0);
        c.decided = { option: body.option, at: iso(0), result: { ok: true, effect: 'invented: ' + (o.label || 'done').toLowerCase() } };
        window.dispatchEvent(new CustomEvent('tryDemo:step', { detail: 'decision' }));
        return { ok: true, result: { ok: true, effect: 'invented: ' + (o.label || 'done').toLowerCase() } };
      }
      if (path === '/api/decision-inbox/dismiss') {
        const c = byId.get(body && body.card_id);
        if (c) { c.status = 'closed'; c.updated_at = iso(0); }
        return { ok: !!c };
      }
      if (path === '/api/decision-inbox/governor') return { ok: true };
      return undefined;
    },
  };
})();
