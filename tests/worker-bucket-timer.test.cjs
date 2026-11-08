// A Workers row that shows the WIP timer chip (live overlay working_since)
// must be bucketed "working", not "idle" (BECKY-3372).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const src = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const m = src.match(/function workerStateBucket\(c\) \{[\s\S]*?\n  \}\n/);
assert.ok(m, 'workerStateBucket not found');
function make(overlay) {
  const sessionIsActivelyWorking = (r) => !!r && String(r.state || '').toLowerCase() === 'working';
  const _applyLiveOverlayToRow = (c) => Object.assign({}, c, overlay);
  return new Function('sessionIsActivelyWorking', '_applyLiveOverlayToRow',
    m[0] + '; return workerStateBucket;')(sessionIsActivelyWorking, _applyLiveOverlayToRow);
}
test('timer chip (working_since) on an alive row => working', () => {
  const b = make({ is_live: true, working_since: 1794157000 });
  assert.equal(b({ is_live: true, state: 'idle' }), 'working');
});
test('overlay state working => working', () => {
  assert.equal(make({ is_live: true, state: 'working' })({ is_live: true, state: 'idle' }), 'working');
});
test('no timer => standing by; waiting on owner => needs-you', () => {
  assert.equal(make({ is_live: true })({ is_live: true, state: 'idle' }), 'idle');
  assert.equal(make({ is_live: true, working_since: 5, question_waiting: true })({ is_live: true }), 'needs-you');
  assert.equal(make({ is_live: false })({ is_live: false }), 'done');
});
