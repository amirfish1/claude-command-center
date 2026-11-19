const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {functionSource} = require('./conversation-source.cjs');
function setup({manualFrames = false, initial = [{session_id: 'recent'}]} = {}) {
  let resolveFull;
  const full = new Promise(resolve => { resolveFull = resolve; });
  const notice = { style: {}, setAttribute() {} };
  const paints = [], calls = [], deferred = [], events = [], releases = [], ready = [], frames = [];
  const state = { archiveData: [], archiveLoaded: false, _archiveFirstLoadedResolve: () => ready.push(true) };
  const context = {
    sharedState: state, window: {dispatchEvent: event => events.push(event.type)}, Event, AbortController, performance,
    document: { getElementById: id => id === 'archiveCompleteness' ? notice : null },
    _whenDashboardUncovered: async () => {}, _startArchiveProgressPoll() {}, _stopArchiveProgressPoll() {},
    _clientLog() {}, _archiveQuery: () => '', _archiveWindow: () => '7d',
    loadArchiveAll: async opts => {
      calls.push(opts);
      if (opts.initial) return initial;
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
  vm.runInContext(functionSource('_markArchiveFirstLoaded'), context);
  vm.runInContext(functionSource('_setArchiveCompleteness'), context);
  vm.runInContext(functionSource('refreshArchiveData'), context);
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
  assert.equal(f.calls[0].controller.signal.aborted, false, 'a useful preview stays in flight');
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

test('full download starts before recent rows arrive but cannot replace their first paint', async () => {
  let resolveInitial;
  const initial = new Promise(resolve => { resolveInitial = resolve; });
  const f = setup({manualFrames: true, initial}), task = f.context.refreshArchiveData();
  await initialPaint(f);
  assert.equal(f.calls[0].initial, true, 'recent rows request starts first');
  assert.ok(f.calls[1].afterPaint, 'full download overlaps the pending recent-row request');
  f.resolveFull([{session_id: 'recent'}, {session_id: 'older'}]);
  await new Promise(setImmediate);
  assert.deepEqual(f.paints, [], 'full rows do not bypass the pending initial response');
  resolveInitial([{session_id: 'recent'}]);
  await new Promise(setImmediate);
  assert.deepEqual(f.paints, [['recent']]);
  f.frames.shift()();
  await new Promise(setImmediate);
  assert.deepEqual(f.paints, [['recent']]);
  f.frames.shift()();
  await task;
  assert.deepEqual(f.paints, [['recent'], ['recent', 'older']]);
  assert.equal(f.calls.length, 2);
});

test('failed recent-row response releases the full read without claiming partial rows', async () => {
  const f = setup({initial: null}), task = f.context.refreshArchiveData();
  await initialPaint(f);
  assert.deepEqual(f.paints, []);
  f.resolveFull([{session_id: 'older'}]);
  await task;
  assert.deepEqual(f.paints, [['older']]);
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

test('an already validated full response paints once without an intermediate recent-row rebuild', async () => {
  const f = setup({manualFrames:true});
  f.context.loadArchiveAll = async opts => {
    f.calls.push(opts);
    if (opts.initial) return [{session_id:'recent'}];
    opts.onValidated();
    return [{session_id:'recent'},{session_id:'older'}];
  };
  await f.context.refreshArchiveData();
  assert.deepEqual(f.paints,[['recent','older']]);
  assert.equal(f.frames.length,0);
  assert.equal(f.context.window.__cccArchiveProgress.complete,true);
  assert.equal(f.notice.hidden,true);
});

test('a cached receipt from an earlier refresh never bypasses a pending current full response', async () => {
  const f = setup({manualFrames:true});
  f.context.window.__cccArchiveSnapshot = {validated:'/api/conversations/list?window=7d&stale_ok=1'};
  const loading=f.context.refreshArchiveData();
  await initialPaint(f);
  assert.deepEqual(f.paints,[['recent']]);
  assert.equal(f.context.window.__cccArchiveProgress.complete,false);
  f.frames.shift()(); await new Promise(setImmediate);
  f.frames.shift()(); f.resolveFull([{session_id:'recent'},{session_id:'older'}]);
  await loading;
  assert.equal(f.paints.length,2);
});

test('current validated full rows do not wait for a slow initial response or let it overwrite them later', async () => {
  let releaseInitial;
  const first=new Promise(resolve=>{releaseInitial=resolve;});
  const f=setup({manualFrames:true});
  f.context.loadArchiveAll = async opts => {
    f.calls.push(opts);
    if (opts.initial) return first;
    opts.onValidated();
    return [{session_id:'recent'},{session_id:'older'}];
  };
  await f.context.refreshArchiveData();
  assert.deepEqual(f.paints,[['recent','older']]);
  assert.equal(f.context.window.__cccArchiveProgress.complete,true);
  releaseInitial([{session_id:'obsolete-preview'}]);
  await new Promise(setImmediate);
  assert.deepEqual(f.paints,[['recent','older']]);
  assert.equal(f.frames.length,0);
  assert.equal(f.calls[0].controller.signal.reason,'archive-complete');
});


test('cached preview paints with an incomplete notice before a ready full response replaces it', async () => {
  const f = setup({manualFrames:true});
  f.state._archiveFromLocalCache = true;
  f.state.archiveData = [{session_id:'cached'}];
  f.context.setTimeout = (fn, ms) => ms === 0 ? setImmediate(fn) : 0;
  f.context.loadArchiveAll = async opts => {
    f.calls.push(opts);
    if (opts.initial) return [{session_id:'recent'}];
    opts.onValidated();
    return [{session_id:'recent'},{session_id:'older'}];
  };
  const task = f.context.refreshArchiveData();
  await new Promise(setImmediate);
  assert.match(f.notice.textContent, /Search and counts are incomplete/);
  assert.equal(f.context.window.__cccArchiveProgress.complete, false);
  assert.equal(f.calls.length, 0, 'a ready response must not overwrite the preview before paint');
  assert.equal(f.state.archiveData[0].session_id, 'cached');
  f.frames.shift()();
  assert.equal(f.calls.length, 0);
  f.frames.shift()();
  await task;
  assert.deepEqual(f.paints, [['recent','older']]);
  assert.equal(f.context.window.__cccArchiveProgress.complete, true);
  assert.equal(f.notice.hidden, true);
});
