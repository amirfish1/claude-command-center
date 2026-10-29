const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const insert = app.slice(app.indexOf('  function insertPendingSpawnCard('),
  app.indexOf('  // ── Externally-initiated spawns'));
const budget = app.slice(app.indexOf('  function _sidebarBudgetIdeaLane('),
  app.indexOf('  function _boundSidebarRowsForRender('));

function launch({enabled = true, source = 'claude', meta = {}, parents = [], query = ''} = {}) {
  const events = [];
  const prefs = new Map([['ccc-sidebar-tab', 'archived']]);
  const ctx = {
    conversationsData: parents, pendingSpawns: new Map(), columnOverrides: {},
    activePaneId: () => 'primary', ideaTicketsOn: () => enabled,
    isSpawnLogPlaceholderSource: () => true,
    localStorage: {setItem: (k, v) => prefs.set(k, v)},
    _setSharedQueuePanelHost: () => {},
    selectConversation: id => events.push({kind: 'select', id}),
    filterConversations: () => ctx.conversationsData,
    $convSearch: {value: query},
    renderSidebar: rows => events.push({kind: 'render', rows, tab: prefs.get('ccc-sidebar-tab')}),
  };
  vm.runInNewContext(insert + budget, ctx);
  ctx.insertPendingSpawnCard('test-pending', 'New topic', source, null, meta);
  return {ctx, events, prefs, card: ctx.pendingSpawns.get('test-pending')};
}

test('local launches are visible in Ideas before a session id or ticket exists', () => {
  for (const source of ['claude', 'codex', 'gemini']) {
    const {ctx, events, card} = launch({source, meta: {fast_path: source === 'claude'}});
    assert.equal(card.idea_ticket.pending, true);
    assert.equal(card.idea_ticket.ref, '');
    assert.equal(card.expected_session_id, undefined);
    assert.equal(events.find(e => e.kind === 'render').tab, 'ideas');
    assert.ok(events.find(e => e.kind === 'render').rows.includes(card));
    assert.equal(ctx._sidebarBudgetIdeaLane(card, 'ideas'), true);
    assert.equal(ctx._sidebarBudgetIdeaLane(card, 'smallbugs'), false);
    if (source === 'claude') assert.deepEqual(events.map(e => e.kind), ['select', 'render']);
  }
});

test('continuation placeholders retain their existing topic queue', () => {
  const ticket = {ref: 'BUG-1', queue: 'SMALL-BUGS', closed: false};
  const {card, prefs} = launch({parents: [{session_id: 'origin', idea_ticket: ticket}],
    meta: {parent_session_id: 'origin'}});
  assert.equal(card.idea_ticket.ref, 'BUG-1');
  assert.equal(prefs.get('ccc-sidebar-tab'), 'smallbugs');
  assert.equal(ticket.closed, false);
});

test('external workers and typed lanes are not filed as Ideas', () => {
  for (const meta of [{external_spawn: true, no_auto_select: true, spawned_via: 'worker'},
    {spawned_lane: 'workers'}, {spawned_lane: 'other'}]) {
    const {card, prefs} = launch({meta});
    assert.equal(card.idea_ticket, undefined);
    assert.equal(prefs.get('ccc-sidebar-tab'), 'archived');
  }
});

test('UI launches in another tab appear without stealing this tab', () => {
  const {card, prefs, events} = launch({meta: {external_spawn: true, spawned_via: 'ui', no_auto_select: true}});
  assert.equal(card.idea_ticket.pending, true);
  assert.equal(prefs.get('ccc-sidebar-tab'), 'archived');
  assert.equal(events.some(e => e.kind === 'select'), false);
});

test('disabled experiment retains the deferred Claude sidebar path', () => {
  const {card, events, prefs} = launch({enabled: false, meta: {fast_path: true}});
  assert.equal(card.idea_ticket, undefined);
  assert.deepEqual(events.map(e => e.kind), ['select']);
  assert.equal(prefs.get('ccc-sidebar-tab'), 'archived');
});

test('a previous search cannot hide the new local Ideas session', () => {
  const {ctx, events} = launch({source:'claude', meta:{fast_path:true}, query:'unrelated search'});
  assert.equal(ctx.$convSearch.value, '');
  assert(events.some(e => e.kind === 'render' && e.rows.some(r => r.pending_spawn)));
  const external = launch({meta:{external_spawn:true, no_auto_select:true, spawned_via:'ui'}, query:'keep my search'});
  assert.equal(external.ctx.$convSearch.value, 'keep my search');
});
