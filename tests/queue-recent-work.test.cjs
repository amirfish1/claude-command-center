const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
function recentWork(items) {
  const start = app.indexOf('  function _uxqWithMachine(');
  assert.notEqual(start, -1, 'recent work selector exists');
  const end = app.indexOf('  // Compact history rows', start);
  const ctx = { items };
  vm.createContext(ctx);
  return vm.runInContext(app.slice(start, end) + '\n_uxqRecentWorkItems(items)', ctx);
}

test('recent work uses actual work timestamps, ignores filing/editing, and preserves input order', () => {
  const items = [
    { ref: 'TEST-1', closed_at: '2026-01-01T10:00:00Z', status: 'closed', updated_at: '2026-01-05T00:00:00Z' },
    { ref: 'TEST-2', claimed_at: '2026-01-02T10:00:00Z', claimed_by: 'worker-2', status: 'in_progress' },
    { ref: 'TEST-3', created_at: '2026-01-03T10:00:00Z', updated_at: '2026-01-04T00:00:00Z', status: 'open' },
    { ref: 'TEST-4', closed_at: 'invalid', claimed_at: 'invalid' },
  ];
  const rows = recentWork(items);
  assert.deepEqual(Array.from(rows, row => row.item.ref), ['TEST-2', 'TEST-1']);
  assert.equal(rows[0].resolved, false);
  assert.equal(rows[0].worker, 'worker-2');
  assert.equal(rows[1].resolved, true);
  assert.deepEqual(items.map(item => item.ref), ['TEST-1', 'TEST-2', 'TEST-3', 'TEST-4']);
});

test('progress and historical claims survive a reopen without claiming it is resolved', () => {
  const rows = recentWork([
    { ref: 'TEST-1', status: 'open', timeline: [
      { event: 'claim', at: '2026-01-01T10:00:00Z', by: { worker: 'worker-1' } },
      { event: 'close', at: '2026-01-01T11:00:00Z', by: { worker: 'worker-1' } },
      { event: 'reopen', at: '2026-01-01T12:00:00Z' },
    ] },
    { ref: 'TEST-2', status: 'in_progress', claimed_at: '2026-01-01T09:00:00Z', timeline: [
      { event: 'progress', at: '2026-01-01T13:00:00Z', by: { worker: 'worker-2' }, text: 'Verified the fix' },
      { event: 'edit', at: '2026-01-02T13:00:00Z' },
    ] },
  ]);
  assert.deepEqual(Array.from(rows, row => row.item.ref), ['TEST-2', 'TEST-1']);
  assert.equal(rows[0].at, '2026-01-01T13:00:00Z');
  assert.equal(rows[0].worker, 'worker-2');
  assert.equal(rows[0].summary, 'Verified the fix');
  assert.equal(rows[1].resolved, false);
});

test('at most ten latest issues are shown, with resolution summaries and actor', () => {
  const items = Array.from({ length: 12 }, (_, index) => ({
    ref: `TEST-${index + 1}`, status: 'closed',
    closed_at: `2026-01-${String(index + 1).padStart(2, '0')}T10:00:00Z`,
    resolution: { summary: ['Fixed issue', 'Verified behavior'] },
    timeline: [{ event: 'close', at: `2026-01-${String(index + 1).padStart(2, '0')}T10:00:00Z`, by: { worker: 'closer' } }],
  }));
  const rows = recentWork(items);
  assert.equal(rows.length, 10);
  assert.equal(rows[0].item.ref, 'TEST-12');
  assert.equal(rows[9].item.ref, 'TEST-3');
  assert.equal(rows[0].worker, 'closer');
  assert.equal(rows[0].summary, 'Fixed issue\nVerified behavior');
  assert.equal(recentWork(null).length, 0);
});

test('list cache history and progress notes supply work times and close actor', () => {
  const rows = recentWork([
    { ref: 'TEST-1', status: 'closed', claimed_by: 'initial-worker', closed_by: 'finishing-worker',
      closed_at: '2026-01-02T12:00:00Z', resolution: { summary: 'Completed' } },
    { ref: 'TEST-2', status: 'open', history: [
      { event: 'claim', at: '2026-01-02T13:00:00Z', worker: 'history-worker' },
    ] },
    { ref: 'TEST-3', status: 'in_progress', progress_notes: [
      { at: '2026-01-02T14:00:00Z', by: 'progress-worker', text: 'Checked behavior' },
    ] },
  ]);
  assert.deepEqual(Array.from(rows, row => row.item.ref), ['TEST-3', 'TEST-2', 'TEST-1']);
  assert.equal(rows[0].worker, 'progress-worker');
  assert.equal(rows[0].summary, 'Checked behavior');
  assert.equal(rows[1].worker, 'history-worker');
  assert.equal(rows[2].worker, 'finishing-worker');
});

test('ticket activity moves only complete matching trees and keeps other sessions', () => {
  const start = app.indexOf('  function _workersPartitionTicketClusters(');
  const end = app.indexOf('  // Compact history rows', start);
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(app.slice(start, end), ctx);
  const tree = (...refs) => ({rows: refs.map(ref => ({card: {ref}}))});
  const matching = tree('TEST-1', 'TEST-1', '');
  const absent = tree('TEST-2');
  const mixed = tree('TEST-1', 'TEST-3');
  const unknown = tree('');
  const repeated = {...tree('TEST-1'), repeatKey: 'batch'};
  const input = [matching, absent, mixed, unknown, repeated];
  const result = ctx._workersPartitionTicketClusters(input, new Set(['TEST-1']), c => c.ref);
  assert.deepEqual(Array.from(result.matched.keys()), ['TEST-1']);
  assert.equal(result.matched.get('TEST-1')[0], matching);
  assert.deepEqual(Array.from(result.remaining), [absent, mixed, unknown]);
  assert.equal(result.matched.get('TEST-1')[1], repeated);
  assert.equal(input.length, 5);
  const agedOut = ctx._workersPartitionTicketClusters(input, new Set(), c => c.ref);
  assert.deepEqual(Array.from(agedOut.remaining), input);
});

test('assigned finished tickets stay in activity beyond the recent-ten window', () => {
  const start = app.indexOf('  function _uxqWithMachine(');
  const end = app.indexOf('  // Compact history rows', start);
  const items = Array.from({length: 14}, (_, i) => ({ref: `TEST-${i}`, status: 'closed', closed_at: new Date(1700000000000 + i * 1000).toISOString()}));
  const ctx = {_uxqItemsCache: {items}, _uxqItemRef: item => item.ref};
  vm.createContext(ctx);
  vm.runInContext(app.slice(start, end), ctx);
  const refs = vm.runInContext("_workersAssignedTicketRefs = new Set(['TEST-0']); _workersActivityRows().map(r => r.item.ref)", ctx);
  assert.equal(refs.length, 11);
  assert.ok(refs.includes('TEST-0'));
  assert.ok(!refs.includes('TEST-1'));
});

test('entering Workers bypasses the fresh-cache TTL and schedules an immediate repaint', async () => {
  const start = app.indexOf('  function _wtWarmActivityForWorkersLane(');
  const end = app.indexOf('  // ── picker card', start);
  const calls = [];
  const ctx = {_wtActivityWarmInFlight: false, _uxqItemsCache: {ts: Date.now()},
    _fetchUxqItems: async (...args) => calls.push(['items', ...args]),
    _fetchUxqHealth: async (...args) => calls.push(['health', ...args]),
    _scheduleSidebarRender: opts => calls.push(['render', opts.lifecycle])};
  vm.createContext(ctx);
  vm.runInContext(app.slice(start, end), ctx);
  ctx._wtWarmActivityForWorkersLane();
  assert.equal(calls.length, 0);
  ctx._wtWarmActivityForWorkersLane(true);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [['items', false, true], ['health', false, true], ['render', true]]);
});


test('finished mixed or unassigned trees can be represented explicitly as sessions', () => {
  const start = app.indexOf('  function _workersPartitionTicketClusters(');
  const end = app.indexOf('  // Compact history rows', start);
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(app.slice(start, end), ctx);
  const tree = {activityRef: 'session:example', rows: [{card:{ref:'TEST-1'}}, {card:{ref:'TEST-2'}}]};
  const result = ctx._workersPartitionTicketClusters([tree], new Set(['session:example']), c => c.ref);
  assert.equal(result.matched.get('session:example')[0], tree);
  assert.equal(result.remaining.length, 0);
});

test('placeholder updates are hidden without suppressing meaningful progress', () => {
  const start = app.indexOf('  function _workersMeaningfulUpdate(');
  const end = app.indexOf('  // Compact history rows', start);
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(app.slice(start, end), ctx);
  for (const text of ['Completion assessment pending.', 'Working...', 'Working…', ' working ', '']) {
    assert.equal(ctx._workersMeaningfulUpdate(text), '');
  }
  assert.equal(ctx._workersMeaningfulUpdate('Working through the remaining tests.'), 'Working through the remaining tests.');
});

test('ticket expansion survives reload and tolerates invalid saved preferences', () => {
  const start = app.indexOf('  const _workersTicketExpanded =');
  const end = app.indexOf('  // Match exact ticket refs', start);
  const store = new Map();
  const reload = () => {
    const ctx = {localStorage: {getItem: key => store.get(key), setItem: (key, value) => store.set(key, value)}};
    vm.createContext(ctx);
    vm.runInContext(app.slice(start, end), ctx);
    return ctx;
  };
  let ctx = reload();
  ctx._workersRememberTicketExpansion('TEST-1', true);
  ctx = reload();
  assert.equal(vm.runInContext("_workersTicketExpanded.has('TEST-1')", ctx), true);
  ctx._workersRememberTicketExpansion('TEST-1', false);
  assert.equal(vm.runInContext('_workersTicketExpanded.size', reload()), 0);
  store.set('ccc-workers-ticket-expanded', '{invalid');
  assert.equal(vm.runInContext('_workersTicketExpanded.size', reload()), 0);
});

test('a single ticket identified by child lanes owns an unlabelled parent tree', () => {
  const start = app.indexOf('  function _workersPartitionTicketClusters(');
  const end = app.indexOf('  // Compact history rows', start);
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(app.slice(start, end), ctx);
  const cluster = {rows: [
    {depth: 0, card: {ref: ''}},
    {depth: 1, card: {ref: 'TEST-7'}},
    {depth: 1, card: {ref: ''}},
  ]};
  const result = ctx._workersPartitionTicketClusters([cluster], new Set(['TEST-7']), c => c.ref);
  assert.equal(result.matched.get('TEST-7')[0], cluster);
  assert.equal(result.remaining.length, 0);
  const ambiguous = {rows: [...cluster.rows, {depth: 1, card: {ref: 'TEST-8'}}]};
  assert.equal(ctx._workersPartitionTicketClusters([ambiguous], new Set(['TEST-7','TEST-8']), c => c.ref).remaining[0], ambiguous);
});

test('explicit descendant lanes stay with the owning root without merging unrelated peer roots', () => {
  const start = app.indexOf('  function _workersPartitionTicketClusters(');
  const end = app.indexOf('  // Compact history rows', start);
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(app.slice(start, end), ctx);
  const rows = [{depth: 0, card: {ref: 'TEST-7'}}, {depth: 1, card: {ref: 'TEST-8'}}];
  const cluster = {rows};
  assert.equal(ctx._workersPartitionTicketClusters([cluster], new Set(['TEST-7','TEST-8']), c => c.ref).matched.get('TEST-7')[0], cluster);
  const peers = {rows: rows.map(row => ({...row, depth: 0}))};
  assert.equal(ctx._workersPartitionTicketClusters([peers], new Set(['TEST-7','TEST-8']), c => c.ref).remaining[0], peers);
});


test('Workers hides independent sessions and manual ticket queues even when assigned', () => {
  const start = app.indexOf('  function _uxqWithMachine(');
  const end = app.indexOf('  // Compact history rows', start);
  const items = ['TEST-1', 'IDEAS-2', 'SMALL-BUGS-3', 'session:orphan'].map(ref => ({
    ref, status: 'open', claimed_at: '2026-01-01T12:00:00Z', _sessionOnly: ref.startsWith('session:'),
  }));
  const ctx = {_uxqItemsCache: {items}, _uxqItemRef: item => item.ref};
  vm.createContext(ctx);
  vm.runInContext(app.slice(start, end), ctx);
  const refs = vm.runInContext("_workersAssignedTicketRefs = new Set(['IDEAS-2','SMALL-BUGS-3','session:orphan']); _workersActivityRows().map(row => row.item.ref)", ctx);
  assert.deepEqual(Array.from(refs), ['TEST-1']);
});
