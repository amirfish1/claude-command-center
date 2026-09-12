const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
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
