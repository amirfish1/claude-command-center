const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/conversation-core.js', 'utf8');
function extract(name) {
  const at = source.indexOf('function ' + name + '(');
  let i = source.indexOf('{', source.indexOf(') {', at)), depth = 0;
  for (; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(at, i + 1);
  }
  throw Error('missing ' + name);
}
function setup() {
  let resolveFull;
  const full = new Promise(resolve => { resolveFull = resolve; });
  const notice = { style: {}, setAttribute() {} };
  const paints = [], calls = [], deferred = [];
  const state = { archiveData: [], archiveLoaded: false };
  const context = {
    sharedState: state, window: {},
    document: { getElementById: id => id === 'archiveCompleteness' ? notice : null },
    _whenDashboardUncovered: async () => {}, _startArchiveProgressPoll() {}, _stopArchiveProgressPoll() {},
    _clientLog() {}, _archiveQuery: () => '', _archiveWindow: () => '7d',
    loadArchiveAll: async opts => { calls.push(opts); return opts.initial ? [{session_id: 'recent'}] : full; },
    _applyPendingDashboardConversationPatches: rows => rows,
    _mergeArchivePrSnapshot: rows => rows, _mergeIntoMasterRows() {}, _resetArchiveUnknownPatchIds() {},
    hostServices: { renderArchiveList: () => paints.push(state.archiveData.map(r => r.session_id)) },
    refreshLiveSessionsActivity: async () => {}, _queueDashboardArchiveRender() {},
    _markArchiveFirstLoaded() {}, _hydrateArchiveSideData() {}, _scheduleArchiveStaleRetry() { context.retryScheduled = true; },
    requestAnimationFrame: fn => setImmediate(fn), setTimeout: () => 0,
    queueMicrotask: fn => deferred.push(fn),
  };
  vm.createContext(context);
  vm.runInContext(extract('_setArchiveCompleteness'), context);
  vm.runInContext('async ' + extract('refreshArchiveData'), context);
  return { context, state, notice, paints, calls, deferred, resolveFull };
}
async function initialPaint(f) {
  for (let n = 0; n < 20 && f.calls.length < 2; n++) await new Promise(setImmediate);
  assert.equal(f.calls.length, 2);
}
test('recent rows paint while complete request is still pending, then full rows replace them', async () => {
  const f = setup(), task = f.context.refreshArchiveData();
  await initialPaint(f);
  assert.deepEqual(f.paints, [['recent']]);
  assert.match(f.notice.textContent, /Search and counts are incomplete/);
  assert.equal(f.context.window.__cccArchiveProgress.complete, false);
  f.resolveFull([{session_id:'recent'}, {session_id:'older'}]);
  await task;
  assert.deepEqual(f.paints, [['recent'], ['recent', 'older']]);
  assert.equal(f.notice.hidden, true);
  assert.equal(f.context.window.__cccArchiveProgress.complete, true);
});
test('full failure retains usable rows and explicitly incomplete results', async () => {
  const f = setup(), task = f.context.refreshArchiveData();
  await initialPaint(f);
  f.resolveFull(null); await task;
  assert.equal(f.state.archiveData[0].session_id, 'recent');
  assert.match(f.notice.textContent, /could not load.*incomplete/);
  assert.equal(f.context.retryScheduled, true);
  assert.equal(f.context.window.__cccArchiveProgress.complete, false);
});
test('obsolete full response cannot replace a newly selected window', async () => {
  const f = setup(), task = f.context.refreshArchiveData();
  await initialPaint(f);
  f.context._archiveWindow = () => '1d';
  f.resolveFull([{session_id:'obsolete'}]); await task;
  assert.equal(f.state.archiveData[0].session_id, 'recent');
  assert.equal(f.deferred.length, 1, 'new selected window is queued next');
});
