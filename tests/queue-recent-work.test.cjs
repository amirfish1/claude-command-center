const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
function recentWork(items) {
  const start = app.indexOf('  function _uxqRecentWorkItems(');
  assert.notEqual(start, -1, 'recent work selector exists');
  const end = app.indexOf('\n  function ', start + 1);
  const ctx = { items };
  vm.createContext(ctx);
  return vm.runInContext(app.slice(start, end) + '\n_uxqRecentWorkItems(items)', ctx);
}

test('recent work uses actual work timestamps, ignores filing/editing, and preserves input order', () => {
  const items = [
    { ref: 'TEST-1', closed_at: '2026-01-01T10:00:00Z', status: 'closed', updated_at: '2026-01-05T00:00:00Z' },
    { ref: 'TEST-2', claimed_at: '2026-01-02T10:00:00Z', claimed_by: 'worker-2', status: 'in_progress' },
    { ref: 'TEST-3', created_at: '2026-01-03T10:00:00Z', updated_at: '2026-01-04T00:00:00Z', status: 'open' },
    { ref: 'TEST-4', closed_at: 'invalid', claimed_at: 'invalid' },
  ];
  const rows = recentWork(items);
  assert.deepEqual(Array.from(rows, row => row.item.ref), ['TEST-2', 'TEST-1']);
  assert.equal(rows[0].resolved, false);
  assert.equal(rows[0].worker, 'worker-2');
  assert.equal(rows[1].resolved, true);
  assert.deepEqual(items.map(item => item.ref), ['TEST-1', 'TEST-2', 'TEST-3', 'TEST-4']);
});

test('progress and historical claims survive a reopen without claiming it is resolved', () => {
  const rows = recentWork([
    { ref: 'TEST-1', status: 'open', timeline: [
      { event: 'claim', at: '2026-01-01T10:00:00Z', by: { worker: 'worker-1' } },
      { event: 'close', at: '2026-01-01T11:00:00Z', by: { worker: 'worker-1' } },
      { event: 'reopen', at: '2026-01-01T12:00:00Z' },
    ] },
    { ref: 'TEST-2', status: 'in_progress', claimed_at: '2026-01-01T09:00:00Z', timeline: [
      { event: 'progress', at: '2026-01-01T13:00:00Z', by: { worker: 'worker-2' }, text: 'Verified the fix' },
      { event: 'edit', at: '2026-01-02T13:00:00Z' },
    ] },
  ]);
  assert.deepEqual(Array.from(rows, row => row.item.ref), ['TEST-2', 'TEST-1']);
  assert.equal(rows[0].at, '2026-01-01T13:00:00Z');
  assert.equal(rows[0].worker, 'worker-2');
  assert.equal(rows[0].summary, 'Verified the fix');
  assert.equal(rows[1].resolved, false);
});

test('at most ten latest issues are shown, with resolution summaries and actor', () => {
  const items = Array.from({ length: 12 }, (_, index) => ({
    ref: `TEST-${index + 1}`, status: 'closed',
    closed_at: `2026-01-${String(index + 1).padStart(2, '0')}T10:00:00Z`,
    resolution: { summary: ['Fixed issue', 'Verified behavior'] },
    timeline: [{ event: 'close', at: `2026-01-${String(index + 1).padStart(2, '0')}T10:00:00Z`, by: { worker: 'closer' } }],
  }));
  const rows = recentWork(items);
  assert.equal(rows.length, 10);
  assert.equal(rows[0].item.ref, 'TEST-12');
  assert.equal(rows[9].item.ref, 'TEST-3');
  assert.equal(rows[0].worker, 'closer');
  assert.equal(rows[0].summary, 'Fixed issue\nVerified behavior');
  assert.equal(recentWork(null).length, 0);
});

test('list cache history and progress notes supply work times and close actor', () => {
  const rows = recentWork([
    { ref: 'TEST-1', status: 'closed', claimed_by: 'initial-worker', closed_by: 'finishing-worker',
      closed_at: '2026-01-02T12:00:00Z', resolution: { summary: 'Completed' } },
    { ref: 'TEST-2', status: 'open', history: [
      { event: 'claim', at: '2026-01-02T13:00:00Z', worker: 'history-worker' },
    ] },
    { ref: 'TEST-3', status: 'in_progress', progress_notes: [
      { at: '2026-01-02T14:00:00Z', by: 'progress-worker', text: 'Checked behavior' },
    ] },
  ]);
  assert.deepEqual(Array.from(rows, row => row.item.ref), ['TEST-3', 'TEST-2', 'TEST-1']);
  assert.equal(rows[0].worker, 'progress-worker');
  assert.equal(rows[0].summary, 'Checked behavior');
  assert.equal(rows[1].worker, 'history-worker');
  assert.equal(rows[2].worker, 'finishing-worker');
});
