const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = app.indexOf("    const _ideaTicketRows = (lane, bucket = 'active') => {");
const end = app.indexOf('    const _allTabIdeaConvs =', start);
assert.ok(start >= 0 && end > start);
const source = app.slice(start, end) + '\nthis.ticketRows = _ideaTicketRows;';

function representatives(rows, lane = 'ideas', bucket = 'active', trash = []) {
  const ctx = {
    _allTabConvs: rows, _allTabTrashConvs: trash,
    _allTabSessionId: r => r.session_id || r.id || '',
    _allTabLaneFor: r => r.idea_ticket?.queue === 'SMALL-BUGS' ? 'smallbugs' : 'ideas',
  };
  vm.runInNewContext(source, ctx);
  return Array.from(ctx.ticketRows(lane, bucket), r => r.session_id);
}
function row(id, ref, modified, extra = {}) {
  return { session_id: id, modified, idea_ticket: { ref, queue: 'IDEAS' }, ...extra };
}

test('live overlays cannot replace a ticket card or reorder tickets', () => {
  for (const lane of ['ideas', 'smallbugs']) {
    const rows = [row('origin', 'EXAMPLE-1', 20), row('child', 'EXAMPLE-1', 10),
      row('other', 'EXAMPLE-2', 15)];
    if (lane === 'smallbugs') rows.forEach(r => { r.idea_ticket.queue = 'SMALL-BUGS'; });
    for (let mask = 0; mask < 8; mask++) {
      rows.forEach((r, i) => { r.is_live = !!(mask & (1 << i)); });
      assert.deepEqual(representatives(rows, lane), ['origin', 'other']);
      assert.deepEqual(representatives([...rows].reverse(), lane), ['origin', 'other']);
    }
    // Actual new transcript activity can still change the representative.
    rows[1].modified = 30;
    assert.deepEqual(representatives(rows, lane), ['child', 'other']);
  }
});

test('equal timestamps remain stable across bucket/input order', () => {
  const rows = [row('session-b', 'EXAMPLE-1', 20), row('session-a', 'EXAMPLE-1', 20),
    row('session-c', 'EXAMPLE-2', 20)];
  assert.deepEqual(representatives(rows), ['session-a', 'session-c']);
  assert.deepEqual(representatives([...rows].reverse()), ['session-a', 'session-c']);
});

test('hidden and closed tickets stay excluded and pending spawns stay distinct', () => {
  const rows = [row('visible', 'EXAMPLE-1', 1),
    row('archived', 'EXAMPLE-1', 9, { archived: true }),
    row('trashed', 'EXAMPLE-1', 10, { trashed: true }),
    row('closed', 'EXAMPLE-2', 11, { idea_ticket: { ref: 'EXAMPLE-2', closed: true } }),
    row('pending-a', '', 3, { idea_ticket: { pending: true } }),
    row('pending-b', '', 2, { idea_ticket: { pending: true } })];
  assert.deepEqual(representatives(rows), ['pending-a', 'pending-b', 'visible']);
});


test('archived and trash buckets stay in their ticket lane and exclude active history', () => {
  const active = row('active', 'EXAMPLE-1', 20);
  const rows = [active, row('earlier', 'EXAMPLE-1', 10, { archived: true }),
    row('closed-old', 'EXAMPLE-2', 5, { archived: true }),
    row('closed-new', 'EXAMPLE-2', 15, { archived: true }),
    row('bug', 'BUG-1', 25, { archived: true, idea_ticket: { ref: 'BUG-1', queue: 'SMALL-BUGS', closed: true } })];
  assert.deepEqual(representatives(rows, 'ideas', 'archived'), ['closed-new']);
  assert.deepEqual(representatives(rows, 'smallbugs', 'archived'), ['bug']);
  const trash = [row('discarded', 'EXAMPLE-3', 30, { archived: true, trashed: true }),
    row('bug-trash', 'BUG-2', 31, { archived: true, trashed: true, idea_ticket: { ref: 'BUG-2', queue: 'SMALL-BUGS' } })];
  assert.deepEqual(representatives(rows, 'ideas', 'trash', trash), ['discarded']);
});


test('a child inheriting the Ideas lane without ticket metadata does not break rendering', () => {
  const rows = [row('active', 'EXAMPLE-1', 20), { session_id: 'child', modified: 30, idea_ticket: null }];
  assert.deepEqual(representatives(rows), ['active']);
  assert.deepEqual(representatives(rows, 'ideas', 'archived'), []);
});
