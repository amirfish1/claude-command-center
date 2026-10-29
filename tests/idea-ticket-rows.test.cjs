const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = app.indexOf("    const _ideaTicketRows = (lane, bucket = 'active') => {");
const end = app.indexOf('    const _allTabIdeaConvs =', start);
assert.ok(start >= 0 && end > start);
const timeHelpers = app.slice(app.indexOf('  function _archiveWindow()'), app.indexOf('  const OPEN_ASK_RECENT_S'));
const source = timeHelpers + '\nconst _ipWindowCutoff = _archiveWindowCutoff();\n'
  + app.slice(start, end) + '\nthis.ticketRows = _ideaTicketRows;';

function representatives(rows, lane = 'ideas', bucket = 'active', trash = [], window = 'all', query = '') {
  const ctx = {
    _allTabConvs: rows, _allTabTrashConvs: trash, _qActive: query,
    ARCHIVE_WINDOW_KEY: 'ccc-archive-window',
    localStorage: {getItem: () => window},
    Date: class extends Date { static now() { return 2_000_000_000 * 1000; } },
    _allTabSessionId: r => r.session_id || r.id || '',
    _allTabLaneFor: r => r.idea_ticket?.queue === 'SMALL-BUGS' ? 'smallbugs' : 'ideas',
    continuationParentId: r => r.continued_from_session_id || '',
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

test('a newer spawned subagent never takes over its parent ticket row (CCC-220)', () => {
  const rows = [row('parent', 'EXAMPLE-1', 20),
    row('child', 'EXAMPLE-1', 30, { parent_session_id: 'parent' }),
    row('successor', 'EXAMPLE-2', 40, { parent_session_id: 'prior', continued_from_session_id: 'prior' }),
    row('prior', 'EXAMPLE-2', 10),
    row('orphan', 'EXAMPLE-3', 5, { parent_session_id: 'not-in-group' })];
  assert.deepEqual(representatives(rows), ['successor', 'parent', 'orphan']);
});

test('archive shaping preserves continuation identity for ticket representatives', () => {
  const shapingStart = app.indexOf('        goal: c.goal');
  const shapingEnd = app.indexOf('        runtime: c.runtime', shapingStart);
  assert.ok(shapingStart >= 0 && shapingEnd > shapingStart);
  const continuation = row('successor', 'EXAMPLE-1', 30, {
    parent_session_id: 'origin', continued_from_session_id: 'origin',
    first_message: 'Continue the earlier discussion.',
  });
  const shaped = vm.runInNewContext('({' + app.slice(shapingStart, shapingEnd) + '})', {c: continuation});
  assert.equal(shaped.continued_from_session_id, 'origin');
  assert.deepEqual(representatives([row('origin', 'EXAMPLE-1', 20),
    {...shaped, session_id: 'successor', modified: 30}]), ['successor']);
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


test('ticket rows follow Now, 1d, 7d and All in both ticket lanes', () => {
  const now = 2_000_000_000;
  for (const lane of ['ideas', 'smallbugs']) {
    const rows = [row('hour', 'EXAMPLE-1', now - 3600), row('day', 'EXAMPLE-2', now - 12 * 3600),
      row('week', 'EXAMPLE-3', now - 2 * 86400), row('old', 'EXAMPLE-4', now - 8 * 86400)];
    if (lane === 'smallbugs') rows.forEach(r => { r.idea_ticket.queue = 'SMALL-BUGS'; });
    for (const [window, expected] of [['8h', ['hour']], ['1d', ['hour', 'day']],
      ['7d', ['hour', 'day', 'week']], ['all', ['hour', 'day', 'week', 'old']]]) {
      assert.deepEqual(representatives(rows, lane, 'active', [], window), expected);
    }
    assert.deepEqual(representatives(rows, lane, 'active', [], '8h', 'search'),
      ['hour', 'day', 'week', 'old'], 'explicit searches still span all history');
  }
});

test('ticket archive and trash respect the same window', () => {
  const now = 2_000_000_000;
  const rows = [row('recent', 'EXAMPLE-1', now - 60, {archived:true}),
    row('old', 'EXAMPLE-2', now - 8 * 86400, {archived:true})];
  assert.deepEqual(representatives(rows, 'ideas', 'archived', [], '1d'), ['recent']);
  const trash = rows.map(r => ({...r, trashed:true}));
  assert.deepEqual(representatives([], 'ideas', 'trash', trash, '1d'), ['recent']);
  assert.deepEqual(representatives(rows, 'ideas', 'archived'), ['recent', 'old']);
});

test('moving an old session makes it visible and selects it in the destination', () => {
  const now = 2_000_000_000;
  for (const queue of ['IDEAS', 'SMALL-BUGS']) {
    const lane = queue === 'IDEAS' ? 'ideas' : 'smallbugs';
    const moved = row('moved', 'EXAMPLE-1', now - 10 * 86400,
      {idea_ticket: {ref: 'EXAMPLE-1', queue, moved_at: now}});
    const recent = row('recent', 'EXAMPLE-2', now - 60,
      {idea_ticket: {ref: 'EXAMPLE-2', queue}});
    const sibling = row('sibling', 'EXAMPLE-1', now - 30,
      {idea_ticket: {ref: 'EXAMPLE-1', queue}});
    assert.deepEqual(representatives([recent, sibling, moved], lane, 'active', [], '8h'),
      ['moved', 'recent']);
    moved.idea_ticket.moved_at = now - 2 * 86400;
    assert.deepEqual(representatives([moved], lane, 'active', [], '1d'), []);
  }
});

test('a running handoff leaves the main list for the Handed off section', () => {
  const rows = [row('h', 'EXAMPLE-1', 20), row('n', 'EXAMPLE-2', 15)];
  rows[0].idea_ticket.handoff = { state: 'running' };
  assert.deepEqual(representatives(rows, 'ideas', 'active'), ['n']);
  assert.deepEqual(representatives(rows, 'ideas', 'handoff'), ['h']);
  rows[0].idea_ticket.handoff.state = 'blocked';  // a question puts it back on top
  assert.deepEqual(representatives(rows, 'ideas', 'active'), ['h', 'n']);
  assert.deepEqual(representatives(rows, 'ideas', 'handoff'), []);
  rows[0].idea_ticket.handoff.state = 'waiting';  // parked on a dependency: stays handed off
  assert.deepEqual(representatives(rows, 'ideas', 'handoff'), ['h']);
});

test('sessionAttentionStep labels handoffs and owner-held tails', () => {
  const a = app.indexOf('  const _ATTN_NONE_RE');
  const b = app.indexOf('  // The server derives readiness', a);
  assert.ok(a >= 0 && b > a);
  const ctx = {};
  vm.runInNewContext(app.slice(a, b) + '\nthis.step = sessionAttentionStep;', ctx);
  const comp = extra => ({ version: 1, valid: true, ready_to_close: false,
    decision_dependency: { score: 0 }, remaining_actions: [], ...extra });
  const blocked = ctx.step({ idea_ticket: { handoff: { state: 'blocked', question: 'Ship?' } } });
  assert.equal(blocked.label, 'Needs you');
  assert.equal(blocked.text, 'Ship?');
  assert.equal(ctx.step({ idea_ticket: { handoff: { state: 'running' } }, session_state: { did: 'X' } }).kind, 'handoff');
  const waiting = ctx.step({ idea_ticket: { handoff: { state: 'waiting', waiting_on: 'EXAMPLE-11', question: 'build receipts' } } });
  assert.deepEqual([waiting.label, waiting.handoff, waiting.text], ['Waiting', 'running', 'Waiting on EXAMPLE-11: build receipts']);
  const mine = ctx.step({ session_state: { next_step_user: 'None.', completion: comp({
    remaining_actions: [{ text: 'User: approve the client email' }] }) } });
  assert.equal(mine.text, 'approve the client email');
  assert.ok(ctx.step({ session_state: { next_step_user: 'None.', completion: comp({
    decision_dependency: { score: 0.5, evidence: 'pick a plan' } }) } }).text.includes('pick a plan'));
  assert.equal(ctx.step({ session_state: { next_step_user: 'None.', completion: comp({
    remaining_actions: [{ text: 'agent: verify' }] }) } }), null);
});
