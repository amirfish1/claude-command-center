const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = require('./legacy-dashboard-source.cjs');
test('queue event bursts coalesce into one delayed archive refresh', async () => {
  let refreshes = 0;
  const timers = [];
  const context = vm.createContext({
    _dashboardEventState:{invalidations:new Map([['q1',{resource:'queue',id:'ONE'}],['q2',{resource:'queue',id:'TWO'}]])},
    _uxqItemsPromise:null, _wtWorkersPromise:null, _uxqItemsVersion:0,
    _uxqHealthAppliedSeq:0,_uxqHealthReqSeq:0,_wtWorkersVersion:0,
    _uxqItemsCache:{ts:1},_uxqHealthCache:{ts:1},_wtWorkersCache:{ts:1},
    _queuePanelIsVisible:()=>false, _archiveRefreshPromise:null,
    _queueDashboardArchiveRender:()=>{},
    refreshArchiveData:()=>{refreshes++;return Promise.resolve();},
    setTimeout:(callback, delay)=>{timers.push({callback, delay}); return timers.length;},
  });
  const start=source.indexOf('  function _flushDashboardInvalidations()');
  const end=source.indexOf('  function applyDashboardEvent(',start);
  vm.runInContext(source.slice(start,end),context);
  context._flushDashboardInvalidations();
  context._dashboardEventState.invalidations.set('q3',{resource:'queue',id:'THREE'});
  context._flushDashboardInvalidations();
  await Promise.resolve();
  assert.equal(refreshes,0);
  assert.equal(timers.length,1);
  assert.equal(timers[0].delay,250);
  timers[0].callback();
  await Promise.resolve();
  assert.equal(refreshes,1);
  assert.equal(context._dashboardEventState.invalidations.size,0);
});

test('passive queue and archive invalidations defer fetches and repaints while typing in composer', async () => {
  let archiveRefreshes = 0;
  let queueRenders = 0;
  let itemsFetches = 0;
  const timers = [];
  let now = 5000;
  const composerEl = { tagName: 'TEXTAREA', id: 'convInput', closest: () => null };
  const context = vm.createContext({
    Date: { now: () => now },
    document: { activeElement: composerEl },
    _lastTextKeyTs: 4500,
    _dashboardEventState: {
      invalidations: new Map([
        ['queue:ONE', { resource: 'queue', id: 'ONE' }],
        ['sessions:S1', { resource: 'sessions', id: 'S1' }],
      ]),
    },
    _uxqItemsPromise: null,
    _wtWorkersPromise: null,
    _uxqItemsVersion: 0,
    _uxqHealthAppliedSeq: 0,
    _uxqHealthReqSeq: 0,
    _wtWorkersVersion: 0,
    _uxqItemsCache: { ts: 100 },
    _uxqHealthCache: { ts: 100 },
    _wtWorkersCache: { ts: 100 },
    _queuePanelIsVisible: () => true,
    _fetchUxqItems: () => { itemsFetches++; return Promise.resolve([]); },
    _fetchUxqHealth: () => Promise.resolve({}),
    _fetchWtWorkers: () => Promise.resolve([]),
    _renderQueuePanel: () => { queueRenders++; },
    refreshLiveSessionsActivity: () => Promise.resolve(),
    _archiveRefreshPromise: null,
    _queueDashboardArchiveRender: () => {},
    refreshArchiveData: () => { archiveRefreshes++; return Promise.resolve(); },
    setTimeout: (callback, delay) => { timers.push({ callback, delay }); return timers.length; },
  });
  const start = source.indexOf('  function _flushDashboardInvalidations()');
  const end = source.indexOf('  function applyDashboardEvent(', start);
  vm.runInContext(source.slice(start, end), context);

  context._flushDashboardInvalidations();
  await Promise.resolve();
  assert.equal(context._uxqItemsCache.ts, 0, 'queue cache expires immediately');
  assert.equal(context._uxqHealthCache.ts, 0, 'health cache expires immediately');
  assert.equal(context._wtWorkersCache.ts, 0, 'workers cache expires immediately');
  assert.equal(itemsFetches, 0, 'no multi-MB queue refetch while typing');
  assert.equal(queueRenders, 0, 'no synchronous queue board render while typing');
  assert.equal(timers.length, 0, 'archive refresh timer deferred while typing');
  assert.equal(context._dashboardEventState.deferredTypingResources.has('queue'), true);
  assert.equal(context._dashboardEventState.deferredTypingResources.has('archive'), true);
  assert.equal(context._dashboardEventState.deferredTypingResources.has('sessions'), true);

  // Once typing pauses (>1500ms elapsed), replaying deferred resources refreshes normally.
  now = 7000;
  for (const res of context._dashboardEventState.deferredTypingResources) {
    context._dashboardEventState.invalidations.set(res + ':', { resource: res, id: null });
  }
  context._dashboardEventState.deferredTypingResources.clear();
  context._flushDashboardInvalidations();
  await new Promise(r => setImmediate(r));
  assert.equal(itemsFetches, 1);
  assert.equal(queueRenders, 1);
  assert.equal(timers.length, 1);
  timers[0].callback();
  await Promise.resolve();
  assert.equal(archiveRefreshes, 1);
});
