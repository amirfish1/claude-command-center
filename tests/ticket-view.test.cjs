// Ticket view (sidebar ⋮ switch): Workers lists every ticket, so the All /
// Features / Small bugs tabs go away and their tickets join the worker ones.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const QA = require('../static/queue-attention.js');
const app = require('./legacy-dashboard-source.cjs');
const navSource = fs.readFileSync(path.join(__dirname, '../static/sidebar-navigation.js'), 'utf8');
const slice = (start, end) => {
  const from = app.indexOf(start), to = app.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'missing ' + start);
  return app.slice(from, to);
};

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: k => (values.has(k) ? values.get(k) : null), setItem: (k, v) => values.set(k, String(v)) };
}

// The switch, the tab mapping and the tab normaliser, wired as app.js wires them.
function preference({ flag = true, stored = null } = {}) {
  const listeners = {};
  const knob = { hidden: true, attrs: {}, listeners: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(name, fn) { this.listeners[name] = fn; } };
  const window = { cccDecisionTabKey: () => 'decisions2',
    addEventListener: (name, fn) => { listeners[name] = fn; } };
  const local = storage(stored === null ? {} : { 'ccc-ticket-view': stored });
  const renders = [];
  const ctx = { window, localStorage: local, sessionStorage: storage(),
    document: { getElementById: id => (id === 'ticketViewToggle' ? knob : null) },
    renderArchiveList: () => renders.push(1), _syncMobileBottomNav: () => {},
    flags: { idea_tickets: flag } };
  vm.createContext(ctx);
  vm.runInContext(navSource, ctx);
  vm.runInContext('function ideaTicketsOn() { return !!flags.idea_tickets; }\n'
    + slice('  function ticketViewOn()', '  function ff(name)'), ctx);
  return { ctx, knob, nav: window.CCCNavigation, local, listeners, renders };
}

test('the switch is off by default, persists, and retires the three ticket tabs', () => {
  const { ctx, knob, nav, local, renders } = preference();
  assert.equal(ctx.ticketViewOn(), false);
  assert.equal(knob.hidden, false);
  assert.equal(knob.attrs['aria-checked'], 'false');
  assert.equal(nav.getTab(), 'alltickets');
  assert.equal(ctx.normalizeSidebarTabKey('ideas'), 'ideas');

  knob.listeners.click();
  assert.equal(ctx.ticketViewOn(), true);
  assert.equal(local.getItem('ccc-ticket-view'), '1');
  assert.equal(knob.attrs['aria-checked'], 'true');
  assert.equal(renders.length, 1, 'the list repaints at once');
  // Every reader sees the tab that is on screen, including raw getTab() callers.
  assert.equal(nav.getTab(), 'workers');
  for (const tab of ['alltickets', 'ideas', 'smallbugs', 'coding', 'inprogress', 'nonsense']) {
    assert.equal(ctx.normalizeSidebarTabKey(tab), 'workers', tab);
  }
  for (const tab of ['queues', 'jobs', 'archived', 'issues']) {
    assert.equal(ctx.normalizeSidebarTabKey(tab), tab);
  }
  assert.equal(ctx.normalizeSidebarTabKey('decisions'), 'decisions2');
  nav.setTab('smallbugs');
  assert.equal(nav.getTab(), 'workers', 'a spawn that lands on Small bugs still shows Workers');
  nav.setTab('jobs');
  assert.equal(nav.getTab(), 'jobs');

  nav.setTab('ideas');
  knob.listeners.click();
  assert.equal(ctx.ticketViewOn(), false);
  assert.equal(local.getItem('ccc-ticket-view'), '0');
  assert.equal(nav.getTab(), 'ideas', 'turning it off returns to the tab that was open');
});

test('without idea tickets the switch is hidden and a stored preference does nothing', () => {
  const { ctx, knob, nav, listeners } = preference({ flag: false, stored: '1' });
  assert.equal(ctx.ticketViewOn(), false);
  assert.equal(knob.hidden, true);
  assert.equal(nav.getTab(), 'alltickets');
  assert.equal(ctx.normalizeSidebarTabKey('alltickets'), 'coding');
  // The flag list arrives after boot.
  ctx.flags.idea_tickets = true;
  listeners['ccc:flags']();
  assert.equal(knob.hidden, false);
  assert.equal(knob.attrs['aria-checked'], 'true');
  assert.equal(nav.getTab(), 'workers');
});

test('another window flipping the switch is followed', () => {
  const { ctx, local, listeners, knob } = preference();
  assert.equal(ctx.ticketViewOn(), false);
  local.setItem('ccc-ticket-view', '1');
  listeners.storage({ key: 'ccc-other' });
  assert.equal(ctx.ticketViewOn(), false, 'unrelated keys do not re-read the preference');
  listeners.storage({ key: 'ccc-ticket-view' });
  assert.equal(ctx.ticketViewOn(), true);
  assert.equal(knob.attrs['aria-checked'], 'true');
});

// Lane membership and the render budget.
const modelCode = slice('  const _NON_WORKER_SPAWNED_VIAS', '  function _sidebarBudgetNaturalLane(');
const foldCode = slice('  function _continuationFoldMaps(', '  let _f2ContinuationEdgesCache');
const budgetCode = slice('  function _sidebarBudgetIdeaLane(', '  // What an empty list says');
function lanes(rows, { ticketView = true, f2 = {} } = {}) {
  const context = { Map, Set, currentConversation: '',
    workerSelfTicketRef: () => '', workerRowFacts: () => ({ isHelper: false, ticketRef: '' }),
    f2EffectiveParentSessionId: (id, parent) => f2[id] || parent || '',
    continuationParentId: row => row.continued_from_session_id || '',
    ideaTicketsOn: () => true, ticketViewOn: () => ticketView,
    _uxqHealthCache: { worker_session_ids: [] }, _wtWorkersCache: {}, _wtWorkerConvsCache: {},
    classifyKanbanColumn: row => (row.archived ? 'archived' : 'active'),
    isApprovalAskRow: () => false, _archiveEngineAllowsRow: () => true,
    _archiveWindowCutoff: () => 0, _archiveWindowAllowsRow: () => true,
    _archiveWindowRowTs: row => row.modified || 0 };
  vm.runInNewContext(foldCode + modelCode + budgetCode, context);
  const model = context._sidebarLaneModel(rows);
  const page = (tab, limit = 240) => context._boundSidebarRowsForRender(rows, limit, tab, {
    matches: row => model.matches(row, tab, false), priority: row => model.priority(row, tab),
    parentIds: model.parentIds, search: false });
  return { model, page };
}
const row = (id, extra = {}) => ({ session_id: id, modified: 100, engine: 'codex', ...extra });

test('Workers holds what All showed, and nothing changes while the switch is off', () => {
  const feature = row('feature', { idea_ticket: { ref: 'IDEAS-1', queue: 'IDEAS' } });
  const bug = row('bug', { idea_ticket: { ref: 'SMALL-BUGS-2', queue: 'SMALL-BUGS' } });
  const helper = row('helper', { parent_session_id: 'feature' });
  const terminal = row('terminal');
  const worker = row('worker', { spawned_lane: 'workers' });
  const chat = row('chat', { spawned_lane: 'other' });
  const rows = [feature, bug, helper, terminal, worker, chat];

  const on = lanes(rows).model;
  assert.equal(on.ticketView, true);
  for (const r of [feature, bug, helper, terminal, worker]) assert.equal(on.matches(r, 'workers', false), true, r.session_id);
  assert.equal(on.matches(chat, 'workers', false), false);
  assert.equal(on.matches(chat, 'archived', false), true);
  assert.equal(on.ticketFor(helper).ref, 'IDEAS-1', 'a helper is listed under its family ticket');
  assert.equal(on.priority(feature, 'workers'), true);
  assert.equal(on.priority(worker, 'workers'), false);
  assert.equal(on.priority(feature, 'archived'), false);

  const off = lanes(rows, { ticketView: false }).model;
  assert.equal(off.ticketView, false);
  for (const r of [feature, bug, helper, terminal, chat]) assert.equal(off.matches(r, 'workers', false), false, r.session_id);
  assert.equal(off.matches(worker, 'workers', false), true);
  assert.equal(off.priority(feature, 'workers'), false);
});

test('with a ticket habit, sessions with no ticket stay in Other', () => {
  const rows = Array.from({ length: 60 }, (_, i) => row('terminal-' + i));
  rows.push(row('feature', { idea_ticket: { ref: 'IDEAS-1', queue: 'IDEAS' } }));
  const { model } = lanes(rows);
  assert.equal(model.ticketlessInAll, false);
  assert.equal(model.matches(rows[0], 'workers', false), false);
  assert.equal(model.matches(rows[0], 'archived', false), true);
  assert.equal(model.matches(rows[60], 'workers', false), true);
});

test('owner tickets and the worker fleet share the page; neither pushes the other off', () => {
  const tickets = Array.from({ length: 30 }, (_, i) => row('ticket-' + i, {
    modified: i, idea_ticket: { ref: 'IDEAS-' + i, queue: 'IDEAS' } }));
  const workers = Array.from({ length: 600 }, (_, i) => row('worker-' + i, { spawned_lane: 'workers', modified: 1000 + i }));
  const rows = [...workers, ...tickets, ...Array.from({ length: 60 }, (_, i) => row('terminal-' + i))];
  const { page } = lanes(rows);
  const first = page('workers');
  assert.ok(tickets.every(t => first.rows.includes(t)), 'every owner ticket is on the first page');
  assert.equal(first.omittedFirst, 0, 'so Workers offers no extra page for them');
  assert.ok(first.omitted > 0 && first.rows.length <= 240);
  assert.equal(first.rows.filter(r => workers.includes(r)).length, 210, 'workers take what the tickets leave');

  // More owner tickets than one page holds: the footer appears and converges.
  const many = Array.from({ length: 300 }, (_, i) => row('many-' + i, {
    modified: i, idea_ticket: { ref: 'IDEAS-' + (100 + i), queue: 'IDEAS' } }));
  const big = lanes([...workers, ...many, ...Array.from({ length: 60 }, (_, i) => row('t-' + i))]);
  const crowded = big.page('workers');
  assert.equal(crowded.omittedFirst, 180);
  assert.equal(crowded.rows.filter(r => workers.includes(r)).length, 120, 'a long ticket history leaves workers half');
  assert.ok(workers.slice(-120).every(w => crowded.rows.includes(w)), 'and those are the newest workers');
  assert.equal(big.page('workers', 480).omittedFirst, 60);
  assert.equal(big.page('workers', 720).omittedFirst, 0);
});

// The Workers ticket list.
function workersList(extra = {}) {
  const ctx = { window: { QueueAttention: QA }, _workersTicketSessions: new Map(),
    _workersIdeaTicketActivity: new Map(),
    _uxqItemRef: item => item.ref, _uxqTicketAttention: item => QA.project(item, { explain: true }),
    _applyLiveOverlayToRow: c => c, _ultraFreshCurrent: c => c,
    workerStateBucket: c => c.bucket, rowOutcomeView: c => c.outcome || {},
    ticketSessionSection: c => c.section, ...extra };
  vm.createContext(ctx);
  vm.runInContext(slice('  function _uxqWithMachine(', '  let _workersTabWasActive')
    + slice('  function _workersMeaningfulUpdate(', '  // Compact history rows'), ctx);
  return ctx;
}

test('an idea ticket is dated by its sessions and labelled Feature or Small bug', () => {
  const ctx = workersList();
  const items = [
    { ref: 'IDEAS-1', status: 'open', title: 'A feature' },
    { ref: 'SMALL-BUGS-2', status: 'open', claimed_at: '2026-01-05T00:00:00Z', claimed_by: 'worker-9' },
    { ref: 'IDEAS-3', status: 'open', title: 'No session in the list' },
    { ref: 'CCC-4', status: 'open' },
    { ref: 'session:abc', _sessionOnly: true },
  ];
  assert.equal(ctx._workersIdeaTicketRows(items).length, 0, 'nothing until a session is listed');
  ctx._workersIdeaTicketActivity.set('IDEAS-1', Date.parse('2026-01-02T00:00:00Z'));
  ctx._workersIdeaTicketActivity.set('SMALL-BUGS-2', Date.parse('2026-01-01T00:00:00Z'));
  const rows = ctx._workersIdeaTicketRows(items);
  assert.deepEqual(Array.from(rows, r => r.item.ref), ['IDEAS-1', 'SMALL-BUGS-2']);
  assert.equal(rows[0].at, '2026-01-02T00:00:00.000Z');
  assert.equal(rows[0].worker, 'Feature');
  assert.equal(rows[1].at, '2026-01-05T00:00:00.000Z', 'a later recorded claim still counts');
  assert.equal(rows[1].worker, 'worker-9');
  assert.equal(ctx._workersIdeaTicket({ ref: 'CCC-4' }), false);
  assert.equal(ctx._workersIdeaTicket({ ref: 'IDEAS-12' }), true);
});

test('an idea ticket takes its state from its sessions, in the sections the old tabs used', () => {
  const ctx = workersList();
  const state = cards => {
    ctx._workersTicketSessions.set('IDEAS-1', { cards });
    return ctx._workersTicketState({ ref: 'IDEAS-1', status: 'open' });
  };
  assert.deepEqual({ ...state([{ section: 'active', outcome: { kind: 'working' } }]) }, { label: 'Working', rank: 0, css: 'is-working' });
  assert.equal(state([{ section: 'finished' }, { section: 'needs-you' }]).label, 'Needs you');
  assert.equal(state([{ section: 'active' }]).label, 'Open');
  assert.equal(state([{ section: 'finished' }]).label, 'Finished');
  assert.equal(state([{ section: 'archived' }]).label, 'Archived');
  assert.ok(state([{ section: 'archived' }]).rank > state([{ section: 'active' }]).rank, 'archived work sinks');
  ctx._workersTicketSessions.set('IDEAS-1', { cards: [{ section: 'finished' }] });
  assert.equal(ctx._workersTicketState({ ref: 'IDEAS-1', status: 'closed' }).label, 'Resolved');
  // The owner's next step is the context line; worker tickets keep their own rule.
  const asking = { outcome: { kind: 'next', text: 'Choose the layout.' } };
  assert.equal(ctx._workersTicketContext({ ref: 'IDEAS-1', status: 'open' }, asking).text, 'Choose the layout.');
  assert.equal(ctx._workersTicketContext({ ref: 'CCC-4', status: 'in_progress' }, asking), null);
  // "Workers cannot claim this yet" is about worker tickets, not the owner's own.
  const unready = { status: 'open', readiness: 'needs-spec' };
  assert.equal(ctx._workersTicketContext({ ...unready, ref: 'IDEAS-1' }, asking).text, 'Choose the layout.');
  assert.equal(ctx._workersTicketContext({ ...unready, ref: 'IDEAS-1' }, { outcome: {} }), null);
  assert.match(ctx._workersTicketContext({ ...unready, ref: 'CCC-4' }, null).text, /cannot claim/);
  const question = { ref: 'IDEAS-1', status: 'open', needs_input: true, block_question: 'Choose: narrow or wide.' };
  assert.match(ctx._workersTicketContext(question, asking).text, /narrow or wide/, 'a recorded question still leads');
});

test('a ticket name opens a session: the only one, else the lead topic, else its claimer', () => {
  const ctx = workersList({
    conversationsData: [{ id: 'devincli-claimer', session_id: 'devincli-claimer' }, { id: 'closer', session_id: 'closer' }],
  });
  const open = (item, sessions) => ctx._workersTicketOpenSession(item, sessions);
  assert.equal(open({ ref: 'CCC-1' }, { count: 1, lanes: 1, cards: [{ id: 'owner' }, { id: 'lane' }] }), 'owner');
  const topics = { count: 2, lanes: 3,
    cards: [{ id: 'old', modified: 1 }, { id: 'old-lane', modified: 99, bucket: 'working' }, { id: 'new', modified: 5 }],
    roots: [{ id: 'old', modified: 1 }, { id: 'new', modified: 5 }] };
  assert.equal(open({ ref: 'CCC-2' }, topics), 'new', 'a child lane never becomes the destination');
  topics.roots[0].bucket = 'needs-you';
  assert.equal(open({ ref: 'CCC-2' }, topics), 'old', 'the topic waiting on the owner leads');
  assert.equal(open({ ref: 'CCC-3', claimed_session_id: 'claimer' }, undefined), 'devincli-claimer');
  assert.equal(open({ ref: 'CCC-4', timeline: [{ event: 'claim', by: { session_id: 'gone' } }, { event: 'close', by: { session_id: 'closer' } }] }, undefined), 'closer');
  assert.equal(open({ ref: 'CCC-5', claimed_session_id: 'unknown' }, undefined), '', 'no dead link for a session the list never loaded');
});
