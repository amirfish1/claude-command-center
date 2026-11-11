const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const app = fs.readFileSync('static/app.js', 'utf8');
const ticketStart = app.indexOf('    const _ideaTicketFor =');
const ticketEnd = app.indexOf('    const _ideaTicketLane =', ticketStart);
const rowsStart = app.indexOf('    const _ideaTicketRows =');
const rowsEnd = app.indexOf('    const _allTabIdeaConvs =', rowsStart);

function group(rows) {
  const context = {
    _allTabById: new Map(rows.map(c => [c.session_id, c])),
    _allTabSessionId: c => c.session_id,
    _allTabParentId: c => c.parent_session_id,
    _allTabLaneFor: () => 'ideas', _ticketlessRowsInAll: true,
    _allTabConvs: rows, _allTabTrashConvs: [],
    _qActive: '', _ipWindowCutoff: null,
    _archiveWindowAllowsRow: () => true,
    _archiveWindowRowTs: c => c.modified,
    _continuationFoldMaps: () => ({hidden: new Set()}),
    continuationParentId: () => '',
  };
  return vm.runInNewContext(app.slice(ticketStart, ticketEnd)
    + app.slice(rowsStart, rowsEnd) + '\n_ideaTicketRows("alltickets");', context);
}

test('new unticketed descendants share the ancestor ticket instead of taking standalone rows', () => {
  const root = {session_id: 'root', modified: 1, idea_ticket: {ref: 'TEST-1'}};
  const parent = {session_id: 'parent', parent_session_id: 'root', modified: 2};
  const child = {session_id: 'child', parent_session_id: 'parent', modified: 3};
  const independent = {session_id: 'independent', modified: 4};
  const out = group([root, parent, child, independent]);
  assert.deepEqual(Array.from(out, c => c.session_id), ['independent', 'root']);
  assert.equal(child.idea_ticket, undefined);
});

test('an explicit descendant ticket remains independent', () => {
  const root = {session_id: 'root', modified: 1, idea_ticket: {ref: 'TEST-1'}};
  const child = {session_id: 'child', parent_session_id: 'root', modified: 3,
    idea_ticket: {ref: 'TEST-2'}};
  assert.deepEqual(Array.from(group([root, child]), c => c.session_id), ['child', 'root']);
});
