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
function setup({manualFrames = false} = {}) {
  let resolveFull;
  const full = new Promise(resolve => { resolveFull = resolve; });
  const notice = { style: {}, setAttribute() {} };
  const paints = [], calls = [], deferred = [], events = [], releases = [], ready = [], frames = [];
  const state = { archiveData: [], archiveLoaded: false, _archiveFirstLoadedResolve: () => ready.push(true) };
  const context = {
    sharedState: state, window: {dispatchEvent: event => events.push(event.type)}, Event,
    document: { getElementById: id => id === 'archiveCompleteness' ? notice : null },
    _whenDashboardUncovered: async () => {}, _startArchiveProgressPoll() {}, _stopArchiveProgressPoll() {},
    _clientLog() {}, _archiveQuery: () => '', _archiveWindow: () => '7d',
    loadArchiveAll: async opts => {
      calls.push(opts);
      if (opts.initial) return [{session_id: 'recent'}];
      if (opts.afterPaint) await opts.afterPaint;
      return full;
    },
    _applyPendingDashboardConversationPatches: rows => rows,
    _mergeArchivePrSnapshot: rows => rows, _mergeIntoMasterRows() {}, _resetArchiveUnknownPatchIds() {},
    hostServices: { renderArchiveList: () => paints.push(state.archiveData.map(r => r.session_id)) },
    refreshLiveSessionsActivity: async () => {}, _queueDashboardArchiveRender() {},
    hideLoadingOverlay() {}, _releaseStartupApiReads() { releases.push(true); },
    _hydrateArchiveSideData() {}, _scheduleArchiveStaleRetry() { context.retryScheduled = true; },
    requestAnimationFrame: fn => manualFrames ? frames.push(fn) : setImmediate(fn), setTimeout: () => 0,
    queueMicrotask: fn => deferred.push(fn),
  };
  vm.createContext(context);
  vm.runInContext(extract('_markArchiveFirstLoaded'), context);
  vm.runInContext(extract('_setArchiveCompleteness'), context);
  vm.runInContext('async ' + extract('refreshArchiveData'), context);
  return { context, state, notice, paints, calls, deferred, events, releases, ready, frames, resolveFull };
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
  assert.deepEqual(f.events, [], 'recent rows do not release competing startup work');
  f.context._markArchiveFirstLoaded();
  assert.deepEqual(f.ready, [true], 'first-row readiness does not wait for full history');
  assert.deepEqual(f.releases, [], 'optional probes wait while the full read is pending');
  f.resolveFull([{session_id:'recent'}, {session_id:'older'}]);
  await task;
  assert.deepEqual(f.paints, [['recent'], ['recent', 'older']]);
  assert.equal(f.notice.hidden, true);
  assert.equal(f.context.window.__cccArchiveProgress.complete, true);
  assert.deepEqual(f.events, ['ccc:archive-settled']);
  assert.equal(f.context.window.__cccArchiveSettled, true);
  assert.ok(f.releases.length, 'full completion releases the startup backlog even after initial readiness');
});
test('history starts before initial paint and cannot replace it until both frames finish', async () => {
  const f = setup({manualFrames: true}), task = f.context.refreshArchiveData();
  await initialPaint(f);
  assert.ok(f.calls[1].afterPaint, 'history is already in flight before either frame');
  f.resolveFull([{session_id: 'recent'}, {session_id: 'older'}]);
  assert.deepEqual(f.paints, [['recent']]);
  f.frames.shift()();
  await new Promise(setImmediate);
  assert.deepEqual(f.paints, [['recent']], 'an immediately available response waits through first frame');
  assert.equal(f.notice.hidden, false);
  f.frames.shift()();
  await task;
  assert.deepEqual(f.paints, [['recent'], ['recent', 'older']]);
  assert.equal(f.notice.hidden, true);
  assert.equal(f.calls.length, 2, 'the overlap does not issue a second complete read');
});
test('an initial render error releases the prefetched response barrier', async () => {
  const f = setup();
  f.context.hostServices.renderArchiveList = () => { throw Error('render failed'); };
  await assert.rejects(f.context.refreshArchiveData(), /render failed/);
  f.resolveFull([]);
  await f.calls[1].afterPaint;
  assert.equal(f.state._archiveRefreshPromise, null);
});
test('full failure retains usable rows and explicitly incomplete results', async () => {
  const f = setup(), task = f.context.refreshArchiveData();
  await initialPaint(f);
  f.resolveFull(null); await task;
  assert.equal(f.state.archiveData[0].session_id, 'recent');
  assert.match(f.notice.textContent, /could not load.*incomplete/);
  assert.equal(f.context.retryScheduled, true);
  assert.equal(f.context.window.__cccArchiveProgress.complete, false);
  assert.deepEqual(f.events, ['ccc:archive-settled'], 'failure also releases deferred startup work');
  assert.ok(f.releases.length);
});
test('obsolete full response cannot replace a newly selected window', async () => {
  const f = setup(), task = f.context.refreshArchiveData();
  await initialPaint(f);
  f.context._archiveWindow = () => '1d';
  f.resolveFull([{session_id:'obsolete'}]); await task;
  assert.equal(f.state.archiveData[0].session_id, 'recent');
  assert.equal(f.deferred.length, 1, 'new selected window is queued next');
  assert.deepEqual(f.events, [], 'an obsolete response is not the selected archive settling');
});

test('retaining rows after an empty full response still releases deferred startup work', async () => {
  const f = setup(), task = f.context.refreshArchiveData();
  await initialPaint(f);
  f.resolveFull([]); await task;
  assert.equal(f.state.archiveData[0].session_id, 'recent');
  assert.deepEqual(f.events, ['ccc:archive-settled']);
  assert.equal(f.context.window.__cccArchiveProgress.complete, false, 'settling does not claim complete data');
  assert.ok(f.releases.length);
});

test('a persisted preview resolves readiness without starting optional network work', () => {
  const f = setup();
  f.state._archiveFromLocalCache = true;
  f.context._markArchiveFirstLoaded();
  assert.deepEqual(f.ready, [true]);
  assert.deepEqual(f.releases, []);
  f.state._archiveFromLocalCache = false;
  f.context.window.__cccArchiveProgress = {complete: true};
  f.context._markArchiveFirstLoaded();
  assert.ok(f.releases.length);
});
