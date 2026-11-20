const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = require('./legacy-dashboard-source.cjs');
const fn = name => {
  const start = app.indexOf('  function ' + name + '(');
  assert.ok(start >= 0, name);
  return app.slice(start, app.indexOf('\n  }\n', start) + 4);
};

function harness() {
  let now = 60000;
  const frames = [], paints = [];
  const context = vm.createContext({
    Date: {now: () => now},
    document: {getElementById: id => id === 'sidebarReadPause' ? null : ({value: ''}), querySelector: () => null},
    shouldPausePeriodicUiWork: () => false,
    appOpen: false, _dashboardCoveredByApp: () => context.appOpen,
    isConversationListScrollActive: () => false,
    requestAnimationFrame: callback => {frames.push(callback); return frames.length;},
    filterConversations: () => context.archiveData,
    renderSidebar: (rows, opts) => paints.push({rows, force: !!opts?.force}),
    conversationsData: [], archiveData: [{session_id: 'session-one', state: 'working'}],
    currentSession: null, _liveSessionsActivityLast: null,
    _sessionLiveOverlay: new Map(), _rememberLiveOverlay() {},
    _dashboardEventState: {pendingConversationPatches: new Map()},
  });
  const sessionStart = app.indexOf('  // Session patches for ids the archive list never contains');
  const sessionEnd = app.indexOf('  function scheduleDashboardInvalidation(', sessionStart);
  vm.runInContext(`let _ttsActive = true, _ttsPaused = false;
    let _lastPolledSidebarRenderAt = 0, _convRowActionHoverUntil = 0;
    let _sidebarRenderRaf = 0, _sidebarLifecycleRenderPending = false;
    let _sidebarRenderPendingWhilePaused = false;
    ${['_readAloudThrottlesSidebar', '_sidebarHasHoveredConvItem', '_pausePeriodicSidebarRender',
      '_flushDeferredSidebarRenderIfAny', '_scheduleSidebarRender', '_queueDashboardArchiveRender',
      '_applyDashboardConversationPatch'].map(fn).join('\n')}
    ${app.slice(sessionStart, sessionEnd)}
    this.pauseRead = () => { _ttsPaused = true; };
    this.stopRead = () => { _ttsActive = false; };
  `, context);
  return {context, paints, frames, time: t => {now = t;}, flush: () => {
    while (frames.length) frames.shift()();
  }};
}

test('ordinary event and poll refreshes stay paused throughout playback', () => {
  const h = harness();
  for (let elapsed = 0; elapsed <= 60000; elapsed += 5000) {
    h.time(60000 + elapsed);
    h.context._queueDashboardArchiveRender();
    h.context._scheduleSidebarRender();
    h.flush();
  }
  assert.equal(h.paints.length, 0, 'no periodic redraw even beyond the old 30s cadence');
});

test('passive promise results cannot opt into lifecycle priority', () => {
  const h = harness();
  h.context._queueDashboardArchiveRender(); h.flush();
  for (const result of [[], [{state: 'working'}], null, {lifecycle: 'true'}]) {
    h.context._queueDashboardArchiveRender(result); h.flush();
  }
  assert.equal(h.paints.length, 0);
});

test('a lifecycle event upgrades the queued frame and stays immediate during reads', () => {
  const h = harness();
  h.context._queueDashboardArchiveRender();
  h.context._queueDashboardArchiveRender({lifecycle: true});
  assert.equal(h.frames.length, 1);
  h.flush();
  assert.equal(h.paints[0].force, true);
  h.context._queueDashboardArchiveRender({lifecycle: true}); h.flush();
  assert.equal(h.paints.length, 2);
});

for (const endRead of ['pauseRead', 'stopRead']) {
  test(`${endRead} lets pending passive data paint without waiting 30 seconds`, () => {
    const h = harness();
    h.context._queueDashboardArchiveRender(); h.flush();
    h.time(65000);
    h.context._queueDashboardArchiveRender(); h.flush();
    assert.equal(h.paints.length, 0);
    h.context[endRead]();
    h.context._flushDeferredSidebarRenderIfAny(); h.flush();
    assert.equal(h.paints.length, 1);
  });
}

test('authoritative session state and conversation edits bypass passive read pauses', () => {
  const h = harness();
  h.context._queueDashboardArchiveRender(); h.flush();
  h.context._applyDashboardSessionPatch({entity: {id: 'session-one'}, patch: {state: 'waiting', question_waiting: true}});
  h.flush();
  assert.equal(h.paints.at(-1).force, true);
  assert.equal(h.paints.at(-1).rows[0].question_waiting, true);
  h.context._applyDashboardConversationPatch({entity: {id: 'session-one'}, patch: {display_name: 'Renamed'}});
  h.flush();
  assert.equal(h.paints.length, 2);
  assert.equal(h.paints.at(-1).rows[0].display_name, 'Renamed');
});

test('poll renders wait while an app covers the dashboard, then paint when it closes', () => {
  const h = harness();
  h.context.stopRead();
  h.context.appOpen = true;
  for (let elapsed = 0; elapsed <= 60000; elapsed += 5000) {
    h.time(60000 + elapsed);
    h.context._scheduleSidebarRender();
    h.flush();
  }
  assert.equal(h.paints.length, 0, 'the list under an open app is never rebuilt by a poll');
  h.context.appOpen = false;
  h.context._flushDeferredSidebarRenderIfAny(); h.flush();
  assert.equal(h.paints.length, 1, 'the held render lands once the app closes');
});

test('a lifecycle render under an open app waits and lands once on close', () => {
  const h = harness();
  h.context.stopRead();
  h.context.appOpen = true;
  h.context._scheduleSidebarRender({lifecycle: true}); h.flush();
  h.context._scheduleSidebarRender(); h.flush();
  assert.equal(h.paints.length, 0, 'nothing under an open app is rebuilt');
  h.context.appOpen = false;
  h.context._flushDeferredSidebarRenderIfAny(); h.flush();
  assert.deepEqual(h.paints.map(p => p.force), [true], 'the lifecycle render survives the wait');
});

test('an app closing mid-frame does not drop a render queued before it opened', () => {
  const h = harness();
  h.context.stopRead();
  h.context._scheduleSidebarRender({lifecycle: true});
  h.context.appOpen = true; h.flush();
  assert.equal(h.paints.length, 0);
  h.context.appOpen = false;
  h.context._flushDeferredSidebarRenderIfAny(); h.flush();
  assert.deepEqual(h.paints.map(p => p.force), [true]);
});

test('first list paint, saved-conversation restore and stuck recovery wait for the app to close', () => {
  const boot = app.slice(app.indexOf('  async function setArchiveMode('), app.indexOf('  (function wireArchiveMode('));
  assert.ok(boot.indexOf('await _whenDashboardUncovered();') > boot.indexOf('await refreshArchiveData();'));
  assert.ok(boot.indexOf('await _whenDashboardUncovered();') < boot.indexOf('selectConversation('),
    'the saved transcript must not load under the app');
  const render = fn('renderArchiveList');
  assert.ok(render.indexOf('_dashboardCoveredByApp()') > 0
    && render.indexOf('_dashboardCoveredByApp()') < render.indexOf('opts.force'), 'forced renders wait too');
  assert.ok(fn('_recoverArchiveRenderIfStuck').includes('if (_dashboardCoveredByApp()) return Promise.resolve();'));

  // The cover helpers themselves: the early app frame counts before the app
  // is marked open, and waiters resolve (without a second repaint) on close.
  let repaints = 0, frame = {dataset: {early: '1'}, hidden: false};
  const body = {classList: {has: false, contains() { return this.has; }}};
  const ctx = vm.createContext({
    document: {body, getElementById: () => frame},
    renderArchiveList: () => { repaints++; }, _archiveQuery: () => '',
    _flushDeferredSidebarRenderIfAny() {},
  });
  vm.runInContext(`let _dashboardUncoverWaiters = [], _archiveRenderDeferredForApp = false;
    ${['_dashboardCoveredByApp', '_whenDashboardUncovered', '_dashboardUncovered'].map(fn).join('\n')}
    this.covered = _dashboardCoveredByApp; this.wait = _whenDashboardUncovered;
    this.uncovered = _dashboardUncovered; this.defer = () => { _archiveRenderDeferredForApp = true; };`, ctx);
  assert.equal(ctx.covered(), true, 'a page opened as an app is covered before the frame is marked open');
  frame = null;
  assert.equal(ctx.covered(), false, 'an app that does not exist stops holding the list');
  frame = {dataset: {}, hidden: false};
  assert.equal(ctx.covered(), false, 'a prewarmed frame is not an open app');
  body.classList.has = true;
  assert.equal(ctx.covered(), true);
  let resolved = false;
  ctx.wait().then(() => { resolved = true; });
  ctx.defer(); body.classList.has = false; ctx.uncovered();
  assert.equal(repaints, 0, 'the boot waiter paints; no duplicate repaint');
  ctx.defer(); ctx.uncovered();
  assert.equal(repaints, 1, 'without a boot waiter the skipped render is repainted');
  return Promise.resolve().then(() => assert.equal(resolved, true));
});

test('the toolbar check is batched per frame and the queue digest skips a covered dashboard', () => {
  assert.ok(!app.includes('new MutationObserver(_refreshToolbarEmptiness)'),
    'a layout read per mutation forces a synchronous layout each time');
  assert.ok(/if \(!opts\.force && _dashboardCoveredByApp\(\)\) return uxFixesQueueMeta;/.test(
    app.slice(app.indexOf('  async function refreshUxFixesQueueMeta('), app.indexOf('_queueListFetch()', app.indexOf('  async function refreshUxFixesQueueMeta(')))));
  assert.ok(fn('shouldPauseSidebarRender').includes('if (_dashboardCoveredByApp()) return true;'));
});

test('active composer typing holds even lifecycle sidebar rebuilds until typing pauses or blurs, without blocking #convSearch', () => {
  const h = harness();
  vm.runInContext('var _lastTextKeyTs = 0;', h.context);
  const composer = {tagName: 'TEXTAREA', id: 'convInput'};
  h.context.document.activeElement = composer;
  h.time(60000);
  vm.runInContext('_lastTextKeyTs = 59800;', h.context);

  // Authoritative lifecycle events during active typing wait instead of running a 500ms+ rebuild mid-keystroke.
  h.context._applyDashboardSessionPatch({entity: {id: 'session-one'}, patch: {state: 'waiting', question_waiting: true}});
  h.context._scheduleSidebarRender({lifecycle: true});
  h.flush();
  assert.equal(h.paints.length, 0, 'lifecycle sidebar rebuild is held while typing in composer');
  h.context._flushDeferredSidebarRenderIfAny();
  h.flush();
  assert.equal(h.paints.length, 0, 'still within 1.5s typing window');

  // Once the 1.5s typing recency window expires, the held render flushes with its lifecycle force upgrade intact.
  h.time(61500);
  h.context._flushDeferredSidebarRenderIfAny();
  h.flush();
  assert.deepEqual(h.paints.map(p => p.force), [true]);

  // A keystroke landing after rAF was queued but before the frame runs also defers the frame.
  vm.runInContext('_lastTextKeyTs = 0;', h.context);
  h.context._scheduleSidebarRender({lifecycle: true});
  assert.equal(h.frames.length, 1);
  vm.runInContext('_lastTextKeyTs = 61400;', h.context);
  h.flush();
  assert.equal(h.paints.length, 1, 'mid-frame keystroke prevents sidebar rebuild on the typing frame');

  // Blurring the composer lets the held lifecycle render flush immediately.
  h.context.document.activeElement = null;
  h.context._flushDeferredSidebarRenderIfAny();
  h.flush();
  assert.equal(h.paints.length, 2);
  assert.equal(h.paints[1].force, true);

  // Typing in #convSearch must never be blocked so search filtering stays live.
  h.context.document.activeElement = {tagName: 'INPUT', type: 'search', id: 'convSearch'};
  vm.runInContext('_lastTextKeyTs = 61450;', h.context);
  h.context._scheduleSidebarRender({lifecycle: true});
  h.flush();
  assert.equal(h.paints.length, 3);
});



test('the visible pause cue refreshes once and returns to automatic pause', () => {
  const h = harness();
  const button = {onclick: null};
  const cue = {hidden: true, querySelector: () => button};
  h.context.document.getElementById = id => id === 'sidebarReadPause' ? cue : {value: ''};
  h.context._scheduleSidebarRender(); h.flush();
  assert.equal(cue.hidden, false);
  assert.equal(h.paints.length, 0);
  button.onclick(); h.flush();
  assert.equal(h.paints.length, 1);
  assert.equal(h.paints[0].force, true);
  h.time(300000);
  h.context._scheduleSidebarRender(); h.flush();
  assert.equal(h.paints.length, 1);
  h.context.stopRead();
  h.context._flushDeferredSidebarRenderIfAny(); h.flush();
  assert.equal(cue.hidden, true);
  assert.equal(h.paints.length, 2);
});

test('background archive timers and queue history use the passive scheduler', () => {
  const start = app.indexOf("setInterval(_gated('archiveTimes'");
  const end = app.indexOf("setInterval(_gated('uxFixesQueueMeta'", start);
  const timers = app.slice(start, end);
  assert.equal(timers.includes('renderArchiveList('), false);
  assert.equal((timers.match(/_scheduleSidebarRender\(/g) || []).length, 2);
  const queues = app.slice(app.indexOf('  async function _refreshSidebarWorkerState('), app.indexOf('  // Watchtower activity log panel'));
  assert.ok(queues.includes('_scheduleSidebarRender();'));
});
