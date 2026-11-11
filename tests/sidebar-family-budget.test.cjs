const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  function _sidebarBudgetIdeaLane(');
const end = app.indexOf('  // What an empty list says', start);

function setup(rows) {
  const context = {
    _archiveWindowCutoff: () => 0,
    _archiveWindowAllowsRow: () => true,
    _archiveWindowRowTs: row => row.modified,
    classifyKanbanColumn: () => 'active',
    continuationParentId: row => row.continued_from_session_id || '',
    currentConversation: '',
  };
  vm.runInNewContext(app.slice(start, end), context);
  return { context, byId: new Map(rows.map(row => [row.session_id, row])) };
}

test('All keeps recent unticketed helpers in the render budget with ticketed ancestors', () => {
  const parent = {session_id: 'parent', modified: 500, idea_ticket: {ref: 'TEST-1'}};
  const middle = {session_id: 'middle', parent_session_id: 'parent', modified: 501};
  const child = {session_id: 'child', parent_session_id: 'middle', modified: 502};
  const rows = Array.from({length: 300}, (_, i) => ({session_id: 'other-' + i,
    modified: i, idea_ticket: {ref: 'TEST-' + (i + 2)}})).concat(parent, middle, child);
  const {context} = setup(rows);
  const result = context._boundSidebarRowsForRender(rows, 240, 'alltickets');
  assert.equal(result.rows.length, 240);
  for (const row of [parent, middle, child]) assert.ok(result.rows.includes(row));
  assert.equal(result.omitted, 63);
});

test('family priority respects ticket queue, closed tickets, missing parents and cycles', () => {
  const parent = {session_id: 'parent', idea_ticket: {ref: 'TEST-1', queue: 'SMALL-BUGS'}};
  const child = {session_id: 'child', parent_session_id: 'parent'};
  const {context, byId} = setup([parent, child]);
  const lane = tab => context._sidebarBudgetIdeaLane(child, tab, byId);
  assert.equal(lane('smallbugs'), true);
  assert.equal(lane('ideas'), false);
  assert.equal(lane('alltickets'), true);
  parent.idea_ticket.closed = true;
  assert.equal(lane('alltickets'), false);
  delete parent.idea_ticket;
  parent.parent_session_id = 'child';
  assert.equal(lane('alltickets'), false);
  byId.delete('parent');
  assert.equal(lane('alltickets'), false);
});

test('a recent helper retains its older ancestors without exceeding the row cap', () => {
  const parent = {session_id: 'parent', modified: -100, idea_ticket: {ref: 'TEST-1'}};
  const middle = {session_id: 'middle', parent_session_id: 'parent', modified: -99};
  const child = {session_id: 'child', parent_session_id: 'middle', modified: 502};
  const rows = Array.from({length: 300}, (_, i) => ({session_id: 'other-' + i,
    modified: i, idea_ticket: {ref: 'TEST-' + (i + 2)}})).concat(parent, middle, child);
  const {context} = setup(rows);
  const result = context._boundSidebarRowsForRender(rows, 240, 'alltickets');
  assert.ok(result.rows.includes(child));
  assert.ok(result.rows.includes(parent));
  assert.ok(result.rows.includes(middle));
  assert.equal(result.rows.length, 240);
});
