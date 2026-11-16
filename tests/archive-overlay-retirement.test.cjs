const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const app = require('./legacy-dashboard-source.cjs');
const start = app.indexOf('    // Seed only before the first authoritative live snapshot.');
const end = app.indexOf('    const shaped = rows.map(c => {', start);
assert.ok(start >= 0 && end > start);
const seed = app.slice(start, end);
const previous = { id: 'finished-session', is_live: true, codex_state: 'working', state: 'working' };
function run(fetchedAt, overlays = new Map()) {
  vm.runInNewContext(seed, {
    conversationsData: [previous], _liveSessionsActivityFetchedAt: fetchedAt,
    _sessionLiveOverlay: overlays, _liveOverlayFieldsFromRow: row => ({ ...row }),
    _rememberLiveOverlay: (sid, fields) => overlays.set(sid, fields),
  });
  return overlays;
}
test('archive rerenders cannot resurrect Working after a live snapshot omits the exited session', () => {
  const overlays = run(0);
  assert.equal(overlays.get(previous.id).codex_state, 'working');
  overlays.delete(previous.id); // successful live poll omitted the finished session
  for (let n = 0; n < 3; n++) run(100, overlays);
  assert.equal(overlays.has(previous.id), false);
  const row = { id: previous.id, is_live: false, last_event_type: 'result', last_assistant_text: 'Finished.' };
  const outcome = loadRowOutcomeView({ _applyLiveOverlayToRow: c => ({ ...c, ...overlays.get(c.id) }) })(row);
  assert.notEqual(outcome.kind, 'working');
});
test('startup seeding cannot overwrite a newer idle overlay', () => {
  const idle = { is_live: true, codex_state: 'idle', state: 'idle' };
  const overlays = run(0, new Map([[previous.id, idle]]));
  assert.equal(overlays.get(previous.id), idle);
});
