const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
function load() {
  const start = source.indexOf('  function ticketSessionSection(');
  const end = source.indexOf('  // The finished row\'s one action:', start);
  return vm.runInNewContext(source.slice(start, end) + '\n({ ticketSessionSection, stableTicketSessionGroups });', {
    _applyLiveOverlayToRow: c => c,
    rowOutcomeView: loadRowOutcomeView(),
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
test('polls and changing timestamps preserve order; new tickets lead', () => {
  const { stableTicketSessionGroups: groups } = load();
  const ids = g => Array.from(g.active, r => r.session_id);
  assert.deepEqual(ids(groups([row('a'), row('b')], 'features')), ['a', 'b']);
  assert.deepEqual(ids(groups([row('b', { modified: 99 }), row('a')], 'features')), ['a', 'b']);
  assert.deepEqual(ids(groups([row('b'), row('c'), row('a')], 'features')), ['c', 'a', 'b']);
});
test('selected row stays anchored until deselection; explicit close moves immediately', () => {
  const { stableTicketSessionGroups: groups } = load();
  groups([row('a')], 'features');
  const question = row('a', { question_waiting: true });
  assert.equal(groups([question], 'features', 'a').active.length, 1);
  assert.equal(groups([question], 'features', 'b')['needs-you'].length, 1);
  assert.equal(groups([row('a', { archived: true })], 'features', 'a').finished.length, 1);
});
test('ticket identity survives a new representative session and categories stay independent', () => {
  const { stableTicketSessionGroups: groups } = load();
  groups([row('a'), row('b')], 'features');
  const continuation = row('new', { idea_ticket: { ref: 'TASK-a' } });
  assert.equal(groups([row('b'), continuation], 'features').active[0].session_id, 'new');
  assert.equal(groups([row('b'), row('a')], 'bugs').active[0].session_id, 'b');
});
