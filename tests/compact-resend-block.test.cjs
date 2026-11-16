// A /compact clicked mid-turn is QUEUED server-side. If that queued entry was
// later dropped, the card spun forever and the composer refused a retyped
// /compact ("Already compacting") even though the card said re-running was
// safe. Only a live, on-schedule, non-queued run may block a re-send.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./legacy-dashboard-source.cjs');
const start = source.indexOf('  function _compactRunFor(sid) {');
const end = source.indexOf('  // Context size for the before/after headline.', start);
assert.ok(start >= 0 && end > start, '_compactRunFor block present');

function blocks(run) {
  const ctx = vm.createContext({_compactRun: run});
  vm.runInContext(source.slice(start, end), ctx);
  return ctx._compactRunBlocksResend('s1');
}
const run = (over) => Object.assign({sid: 's1', stage: 'working', slow: false, queued: false}, over);

test('a live on-schedule run blocks a duplicate /compact', () => {
  assert.equal(blocks(run()), true);
  assert.equal(blocks(run({stage: 'requested'})), true);
});

test('a queued /compact does not block (server dedupes the re-send)', () => {
  assert.equal(blocks(run({queued: true})), false);
});

test('a stalled run does not block: its card says re-running is safe', () => {
  assert.equal(blocks(run({slow: true})), false);
});

test('finished, failed, and unconfirmed runs never block', () => {
  for (const stage of ['done', 'failed', 'unconfirmed']) {
    assert.equal(blocks(run({stage})), false, stage);
  }
});

test('a run for another session does not block', () => {
  assert.equal(blocks(run({sid: 's2'})), false);
});

test('every re-send guard uses the narrow check, not _compactRunFor', () => {
  assert.doesNotMatch(source, /compactCommand && _compactRunFor\(sid\)/);
  assert.doesNotMatch(source, /compactAlreadyRunning = !!\(currentSession && _compactRunFor\(/);
});
