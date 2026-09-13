const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Slice _applyDashboardSessionPatch (plus the unknown-id bookkeeping declared
// right above it) out of app.js and run it against stubs, no browser needed.
const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  // Session patches for ids the archive list never contains');
const end = app.indexOf('  function scheduleDashboardInvalidation(resource, id) {');
assert.ok(start > 0 && end > start, 'could not slice _applyDashboardSessionPatch out of app.js');
const src = app.slice(start, end);

function harness(rows) {
  const ctx = {
    archiveData: rows,
    conversationsData: [],
    _liveSessionsActivityLast: null,
    _sessionLiveOverlay: new Map(),
    _rememberLiveOverlay() {},
    renders: 0,
    invalidations: [],
    _queueDashboardArchiveRender() { ctx.renders += 1; },
    scheduleDashboardInvalidation(resource, id) { ctx.invalidations.push(resource + ':' + id); },
  };
  vm.createContext(ctx);
  vm.runInContext(src + '\nthis._applyDashboardSessionPatch = _applyDashboardSessionPatch;'
    + '\nthis._resetArchiveUnknownPatchIds = _resetArchiveUnknownPatchIds;', ctx);
  return ctx;
}
const patch = (id, state) => ({ entity: { id, type: 'session' }, patch: { state } });

test('an ended patch for a session the list never had does not refetch the archive', () => {
  const ctx = harness([{ session_id: 'known' }]);
  ctx._applyDashboardSessionPatch(patch('ghost', 'ended'));
  assert.deepEqual(ctx.invalidations, []);
});

test('a live patch for an unknown session refetches once, not on every state change', () => {
  const ctx = harness([{ session_id: 'known' }]);
  ctx._applyDashboardSessionPatch(patch('ghost', 'idle'));
  ctx._applyDashboardSessionPatch(patch('ghost', 'working'));
  ctx._applyDashboardSessionPatch(patch('ghost', 'idle'));
  assert.deepEqual(ctx.invalidations, ['archive:ghost']);
  ctx._applyDashboardSessionPatch(patch('other', 'working'));
  assert.deepEqual(ctx.invalidations, ['archive:ghost', 'archive:other']);
});

test('a known session is patched in place and renders, no refetch', () => {
  const ctx = harness([{ session_id: 'known' }]);
  ctx._applyDashboardSessionPatch(patch('known', 'working'));
  assert.deepEqual(ctx.invalidations, []);
  assert.equal(ctx.renders, 1);
  assert.equal(ctx.archiveData[0].state, 'working');
});

test('a fresh archive window forgets the misses so the next live patch may refetch', () => {
  const ctx = harness([{ session_id: 'known' }]);
  ctx._applyDashboardSessionPatch(patch('ghost', 'idle'));
  ctx._resetArchiveUnknownPatchIds();
  ctx._applyDashboardSessionPatch(patch('ghost', 'idle'));
  assert.deepEqual(ctx.invalidations, ['archive:ghost', 'archive:ghost']);
});
