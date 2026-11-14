const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
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
    document: {getElementById: () => ({value: ''}), querySelector: () => null},
    shouldPausePeriodicUiWork: () => false,
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

test('event refreshes and poll refreshes share one 30-second read cadence', () => {
  const h = harness();
  for (let elapsed = 0; elapsed <= 60000; elapsed += 5000) {
    h.time(60000 + elapsed);
    h.context._queueDashboardArchiveRender();
    h.context._scheduleSidebarRender();
    h.flush();
  }
  assert.equal(h.paints.length, 3, 'only t=0, 30s and 60s may rebuild');
});

test('passive promise results cannot opt into lifecycle priority', () => {
  const h = harness();
  h.context._queueDashboardArchiveRender(); h.flush();
  for (const result of [[], [{state: 'working'}], null, {lifecycle: 'true'}]) {
    h.context._queueDashboardArchiveRender(result); h.flush();
  }
  assert.equal(h.paints.length, 1);
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
    assert.equal(h.paints.length, 1);
    h.context[endRead]();
    h.context._flushDeferredSidebarRenderIfAny(); h.flush();
    assert.equal(h.paints.length, 2);
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
  assert.equal(h.paints.length, 3);
  assert.equal(h.paints.at(-1).rows[0].display_name, 'Renamed');
});
