const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
test('queue events refresh worker session rows once even with queue panel closed', async () => {
  let refreshes = 0;
  const context = vm.createContext({
    _dashboardEventState:{invalidations:new Map([['q1',{resource:'queue',id:'ONE'}],['q2',{resource:'queue',id:'TWO'}]])},
    _uxqItemsPromise:null, _wtWorkersPromise:null, _uxqItemsVersion:0,
    _uxqHealthAppliedSeq:0,_uxqHealthReqSeq:0,_wtWorkersVersion:0,
    _uxqItemsCache:{ts:1},_uxqHealthCache:{ts:1},_wtWorkersCache:{ts:1},
    _queuePanelIsVisible:()=>false, _archiveRefreshPromise:null,
    _queueDashboardArchiveRender:()=>{},
    refreshArchiveData:()=>{refreshes++;return Promise.resolve();},
  });
  const start=source.indexOf('  function _flushDashboardInvalidations()');
  const end=source.indexOf('  function applyDashboardEvent(',start);
  vm.runInContext(source.slice(start,end),context);
  context._flushDashboardInvalidations();
  await Promise.resolve();
  assert.equal(refreshes,1);
  assert.equal(context._dashboardEventState.invalidations.size,0);
});
