const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const app = require('./legacy-dashboard-source.cjs');
const modelCode = app.slice(app.indexOf('  const _NON_WORKER_SPAWNED_VIAS'), app.indexOf('  function _sidebarBudgetNaturalLane('));
const foldCode = app.slice(app.indexOf('  function _continuationFoldMaps('), app.indexOf('  let _f2ContinuationEdgesCache'));
const budgetCode = app.slice(app.indexOf('  function _sidebarBudgetIdeaLane('), app.indexOf('  // What an empty list says'));

function setup(rows, {engine = '', cutoff = 0, f2 = {}, workerIds = [], ticketView = false} = {}) {
  const context = {
    Map, Set, currentConversation: '',
    workerSelfTicketRef: row => row.selfTicket || '',
    workerRowFacts: row => ({isHelper: !!row.helperTicket, ticketRef: row.helperTicket}),
    f2EffectiveParentSessionId: (id, parent) => f2[id] || parent || '',
    continuationParentId: row => row.continued_from_session_id || '',
    ideaTicketsOn: () => true,
    ticketViewOn: () => ticketView,
    _uxqHealthCache: {worker_session_ids: workerIds}, _wtWorkersCache: {}, _wtWorkerConvsCache: {},
    classifyKanbanColumn: row => row.archived ? 'archived' : row.source === 'backlog' ? 'backlog' : 'active',
    isApprovalAskRow: row => !!row.approval,
    _archiveEngineAllowsRow: row => !engine || row.engine === engine,
    _archiveWindowCutoff: () => cutoff,
    _archiveWindowAllowsRow: row => !cutoff || row.modified >= cutoff,
    _archiveWindowRowTs: row => row.modified || 0,
  };
  vm.runInNewContext(foldCode + modelCode + budgetCode, context);
  const model = context._sidebarLaneModel(rows);
  const page = (tab, limit = 240, search = false) => context._boundSidebarRowsForRender(rows, limit, tab, {
    matches: row => model.matches(row, tab, search), parentIds: model.parentIds, search,
  });
  return {context, model, page};
}
const row = (id, extra = {}) => ({session_id: id, modified: 100, engine: 'codex', ...extra});

test('pagination includes effective F2 family membership that the old lane guess missed', () => {
  const parent = row('parent', {idea_ticket: {ref: 'TEST-1'}});
  const child = row('child', {modified: 200});
  const workers = Array.from({length: 500}, (_, i) => row('worker-' + i, {spawned_lane: 'workers', modified: 1000 + i}));
  const rows = [parent, child, ...workers];
  const {context, model, page} = setup(rows, {f2: {child: 'parent'}});
  assert.equal(context._sidebarBudgetIdeaLane(child, 'alltickets', new Map(rows.map(r => [r.session_id, r]))), false);
  assert.equal(model.matches(child, 'alltickets', false), true);
  assert.equal(model.ticketFor(child).ref, 'TEST-1');
  const result = page('alltickets');
  assert.ok(result.rows.includes(parent));
  assert.ok(result.rows.includes(child));
  assert.equal(result.omitted, 0, 'unrelated workers cannot leave a dead footer');
  assert.ok(result.rows.length <= 240);
});

test('Other uses real ledger, Hermes profiles, typed lanes and the full-list ticketless threshold', () => {
  const rows = Array.from({length: 60}, (_, i) => row('terminal-' + i));
  const ledgerWorker = row('ledger-worker');
  const chat = row('chat', {source: 'hermes', hermes_profile: ''});
  const hermesWorker = row('profile-worker', {source: 'hermes', hermes_profile: 'queue'});
  const typedOther = row('other', {spawned_lane: 'other'});
  const ticket = row('ticket', {idea_ticket: {ref: 'TEST-1'}});
  rows.push(ledgerWorker, chat, hermesWorker, typedOther, ticket);
  const {model, page} = setup(rows, {workerIds: ['ledger-worker']});
  assert.equal(model.ticketlessInAll, false);
  assert.equal(model.matches(rows[0], 'archived', false), true);
  assert.equal(model.matches(rows[0], 'alltickets', false), false);
  assert.equal(model.matches(ledgerWorker, 'archived', false), false);
  assert.equal(model.matches(chat, 'archived', false), true);
  assert.equal(model.matches(hermesWorker, 'archived', false), false);
  assert.equal(model.matches(typedOther, 'archived', false), true);
  assert.equal(model.matches(ticket, 'archived', false), false);
  assert.equal(page('archived', 20).omitted, 42);
  assert.equal(page('archived', 80).omitted, 0);
});

test('engine and time filters exclude hidden rows from the remaining count; search spans both', () => {
  const rows = Array.from({length: 600}, (_, i) => row('ticket-' + i, {
    modified: i, engine: i % 2 ? 'claude' : 'codex', idea_ticket: {ref: 'TEST-' + i},
  }));
  rows.push(row('scratch', {folder_label: 'scratch', modified: 1000}));
  const {page} = setup(rows, {engine: 'claude', cutoff: 580});
  assert.equal(page('alltickets').omitted, 0);
  assert.equal(page('alltickets').matched, 10);
  const search = page('alltickets', 240, true);
  assert.equal(search.omitted, 360);
  assert.equal(search.matched, 240);
  assert.equal(page('alltickets', 720, true).omitted, 0);
});

test('every eligible family remains reachable when pagination is exhausted', () => {
  const rows = [];
  const f2 = {};
  for (let i = 0; i < 320; i++) {
    rows.push(row('parent-' + i, {modified: i, archived: i % 3 === 0,
      idea_ticket: {ref: 'TEST-' + i, queue: i % 2 ? 'SMALL-BUGS' : 'IDEAS'}}));
    rows.push(row('child-' + i, {modified: 1000 + i}));
    f2['child-' + i] = 'parent-' + i;
  }
  rows.push(...Array.from({length: 500}, (_, i) => row('worker-' + i, {spawned_lane: 'workers'})));
  const {page, model} = setup(rows, {f2});
  for (const tab of ['alltickets', 'ideas', 'smallbugs']) {
    const expected = rows.filter(r => model.matches(r, tab, false));
    let limit = 240;
    let result = page(tab, limit);
    assert.ok(result.rows.length <= limit);
    while (result.omitted) {
      const previous = result.matched;
      limit += 240;
      result = page(tab, limit);
      assert.ok(result.matched > previous, 'offered click makes matching content available');
      assert.ok(result.rows.length <= limit);
      assert.ok(limit <= 1200, 'must converge');
    }
    assert.ok(expected.every(r => result.rows.includes(r)), tab + ' loses a matching family');
    assert.equal(result.matched, expected.length);
  }
});


test('Other preserves access to trashed sessions from every lane', () => {
  const rows = Array.from({length: 300}, (_, i) => row('worker-' + i, {spawned_lane: 'workers'}));
  const trashed = row('trashed-worker', {spawned_lane: 'workers', archived: true, trashed: true});
  rows.push(trashed);
  const {model, page} = setup(rows);
  assert.equal(model.matches(trashed, 'archived', false), true);
  assert.ok(page('archived').rows.includes(trashed));
  assert.equal(page('archived').omitted, 0);
});

test('In progress does not offer more pages for unrelated archived sessions', () => {
  const active = row('active');
  const archived = Array.from({length: 500}, (_, i) => row('archived-' + i, {archived: true}));
  const {page} = setup([active, ...archived]);
  const result = page('inprogress');
  assert.equal(result.matched, 1);
  assert.equal(result.omitted, 0);
  assert.ok(result.rows.includes(active));
});


test('a worker lane of a continued ticket stays reachable through its current topic', () => {
  const origin = row('origin', {all_lane_override: 'workers', modified: 10});
  const head = row('head', {continued_from_session_id: 'origin', modified: 20,
    idea_ticket: {ref: 'TEST-1'}});
  const lane = row('lane', {parent_session_id: 'origin', all_lane_override: 'workers', modified: 30, engine: 'claude'});
  const filler = Array.from({length: 500}, (_, i) => row('unrelated-' + i, {spawned_lane: 'workers', modified: 1000 + i}));
  const {model, page} = setup([origin, head, lane, ...filler], {engine: 'codex'});
  assert.equal(model.laneFor(lane), 'workers');
  assert.equal(model.matches(lane, 'alltickets', false), true, 'nested family membership is distinct from the lane override');
  const result = page('alltickets');
  assert.ok([origin, head, lane].every(r => result.rows.includes(r)));
  assert.equal(result.omitted, 0);
  assert.ok(result.rows.length <= 240);
});

test('a ticketed lane keeps the worker anchor and its sibling lanes pageable', () => {
  const parent = row('worker-parent', {spawned_lane: 'workers', modified: 10});
  const ticketed = row('ticketed-lane', {parent_session_id: 'worker-parent', modified: 20,
    idea_ticket: {ref: 'TEST-1', queue: 'SMALL-BUGS'}});
  const sibling = row('sibling', {parent_session_id: 'worker-parent', modified: 30, engine: 'claude'});
  const fillers = Array.from({length: 500}, (_, i) => row('unrelated-' + i,
    {spawned_lane: 'workers', modified: 1000 + i}));
  const {model, page} = setup([parent, ticketed, sibling, ...fillers], {engine: 'codex'});
  for (const tab of ['alltickets', 'smallbugs']) {
    assert.equal(model.matches(sibling, tab, false), true);
    assert.ok([parent, ticketed, sibling].every(r => page(tab).rows.includes(r)));
    assert.equal(page(tab).omitted, 0);
  }
});

test('automatic checks stay in Other despite inherited tickets and family membership', () => {
  const parent = row('parent', {idea_ticket: {ref: 'TEST-1'}});
  const checks = [
    row('directory-check', {session_cwd: '/tmp/te-side-check', all_lane_override: 'other'}),
    row('qa-check', {folder_path: '/tmp/wt-model-check'}),
    row('prompt-check', {first_message: 'You monitor an AI coding agent that is partway through ONE task.'}),
    row('marker-check', {first_message: 'TE_SIDECALL: judge this run'}),
  ].map(c => ({...c, parent_session_id: 'parent', idea_ticket: {ref: 'TEST-1'}}));
  const reviewer = row('reviewer', {parent_session_id: 'parent', model: 'claude-haiku-test', lifetime_tokens: 2000000});
  const {model, page, context} = setup([parent, ...checks, reviewer]);
  for (const check of checks) {
    assert.equal(model.laneFor(check), 'other');
    assert.equal(model.parentId(check), '');
    assert.equal(model.ticketFor(check), null);
    for (const tab of ['alltickets', 'ideas', 'workers']) {
      assert.equal(model.matches(check, tab, false), false, check.session_id + ' leaks into ' + tab);
      assert.ok(!page(tab).rows.filter(r => model.matches(r, tab, false)).includes(check));
    }
    assert.equal(model.matches(check, 'archived', false), true);
    assert.equal(model.matches(check, 'alltickets', true), true, 'search still finds checks');
  }
  assert.deepEqual(Array.from(page('archived').rows.filter(r => model.matches(r, 'archived', false)), r => r.session_id).sort(), checks.map(r => r.session_id).sort());
  assert.ok(page('alltickets').rows.includes(reviewer), 'real reviewer remains an individual lane');
  assert.equal(context._isOtherUtilityRow(row('ordinary', {model: 'claude-haiku-test'})), false);
  assert.equal(context._isOtherUtilityRow(row('titled', {display_name: 'te-side-check'})), false);
  assert.equal(context._isOtherUtilityRow(row('moved', {session_cwd: '/tmp/te-side-check', all_lane_override: 'coding'})), false);
});
