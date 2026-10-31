const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
function load(stubs = {}) {
  const start = source.indexOf('  function ticketSessionSection(');
  const end = source.indexOf('  // The finished row\'s one action:', start);
  const helpers = ['_archiveWindowTsSec', '_archiveWindowRowTs'].map(name => {
    const begin = source.indexOf('  function ' + name + '(');
    return source.slice(begin, source.indexOf('\n  }\n', begin) + 4);
  }).join('\n');
  return vm.runInNewContext(helpers + '\n' + source.slice(start, end) + '\n({ ticketSessionSection, stableTicketSessionGroups });', {
    _applyLiveOverlayToRow: c => c,
    _ultraFreshCurrent: c => c,
    rowOutcomeView: loadRowOutcomeView(),
    ...stubs,
  });
}
const row = (id, extra = {}) => ({ session_id: id, idea_ticket: { ref: 'TASK-' + id }, ...extra });
test('turn end, idle, offline and close recommendation remain Active', () => {
  const { ticketSessionSection: section } = load();
  for (const extra of [{ state: 'working' }, { state: 'idle' }, { is_live: false },
    { session_state: { completion: { version: 1, valid: true, ready_to_close: true } } }]) {
    assert.equal(section(row('a', extra)), 'active');
  }
  assert.equal(section(row('a', { archived: true })), 'finished');
  assert.equal(section(row('a', { idea_ticket: { closed: true } })), 'finished');
  assert.equal(section(row('a', { trashed: true, archived: true })), 'archived');
});
test('real questions need attention; stale previous-turn next steps do not', () => {
  const { ticketSessionSection: section } = load();
  assert.equal(section(row('a', { question_waiting: true })), 'needs-you');
  assert.equal(section(row('a', { needs_approval: true })), 'needs-you');
  const ask = { session_state: { next_step_user: 'Choose a region.' } };
  assert.equal(section(row('a', ask)), 'needs-you');
  assert.equal(section(row('a', { ...ask, last_event_type: 'user' })), 'active');
  assert.equal(section(row('a', { ...ask, state: 'working' })), 'active');
});
test('new activity moves an existing row ahead of older sessions, even when selected', () => {
  const { stableTicketSessionGroups: groups } = load();
  const ids = g => Array.from(g.active, r => r.session_id);
  assert.deepEqual(ids(groups([row('a', { modified: 20 }), row('b', { modified: 10 })], 'features')), ['a', 'b']);
  assert.deepEqual(ids(groups([row('a', { modified: 20 }), row('b', { modified: 30 })], 'features', 'b')), ['b', 'a']);
  assert.deepEqual(ids(groups([row('c', { modified: 5 }), row('a', { modified: 20 }), row('b', { modified: 30 })], 'features')), ['b', 'a', 'c']);
});
test('equal timestamps have deterministic order across shuffled polls', () => {
  const { stableTicketSessionGroups: groups } = load();
  const ids = g => Array.from(g.active, r => r.session_id);
  const a = row('a', { modified: 20 }), b = row('b', { modified: 20 });
  assert.deepEqual(ids(groups([b, a], 'features')), ['a', 'b']);
  assert.deepEqual(ids(groups([a, b], 'features')), ['a', 'b']);
});
test('Finished also sorts newest first and honors timestamp units and interaction recency', () => {
  const { stableTicketSessionGroups: groups } = load();
  const items = [row('old', { archived: true, modified: 1700000000000 }),
    row('recent', { archived: true, modified: 1700000020 }),
    row('touched', { archived: true, modified: 1700000000, last_interacted: 1700000030 })];
  assert.deepEqual(Array.from(groups(items, 'bugs').finished, r => r.session_id), ['touched', 'recent', 'old']);
});
test('selected row follows current attention state; explicit close moves immediately', () => {
  const { stableTicketSessionGroups: groups } = load();
  groups([row('a')], 'features');
  const question = row('a', { question_waiting: true });
  assert.equal(groups([question], 'features', 'a')['needs-you'].length, 1);
  assert.equal(groups([question], 'features', 'b')['needs-you'].length, 1);
  assert.equal(groups([row('a', { archived: true })], 'features', 'a').finished.length, 1);
});
test('ticket identity survives a new representative session and categories stay independent', () => {
  const { stableTicketSessionGroups: groups } = load();
  groups([row('a'), row('b')], 'features');
  const continuation = row('new', { idea_ticket: { ref: 'TASK-a' } });
  assert.equal(groups([row('b'), continuation], 'features').active[0].session_id, 'new');
  const question = row('b', { question_waiting: true });
  groups([row('b')], 'features');
  assert.equal(groups([question], 'features', 'b')['needs-you'].length, 1);
  assert.equal(groups([question], 'bugs', 'b')['needs-you'].length, 1);
});
test('a closed ticket does not hide a current continuation decision or work', () => {
  const { ticketSessionSection: section } = load();
  const closed = { idea_ticket: { ref: 'TASK-a', closed: true } };
  assert.equal(section(row('a', { ...closed, session_state: { next_step_user: 'Choose a region.' } })), 'needs-you');
  assert.equal(section(row('a', { ...closed, state: 'working' })), 'active');
  assert.equal(section(row('a', { ...closed, needs_approval: true })), 'needs-you');
  assert.equal(section(row('a', { ...closed, archived: true })), 'finished');
});
test('selected session leaves Needs you as soon as work resumes', () => {
  const { stableTicketSessionGroups: groups } = load();
  const ask = { session_state: { next_step_user: 'Choose a region.' } };
  assert.equal(groups([row('a', ask)], 'features', 'a')['needs-you'].length, 1);
  assert.equal(groups([row('a', { ...ask, state: 'working' })], 'features', 'a').active.length, 1);
});
test('section membership uses the same fresh state as the rendered status', () => {
  const { ticketSessionSection: section } = load({
    _ultraFreshCurrent: c => ({ ...c, state: 'working', last_event_type: 'user', session_state: null }),
  });
  assert.equal(section(row('a', { session_state: { next_step_user: 'Choose a region.' } })), 'active');
});
