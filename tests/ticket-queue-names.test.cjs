const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'), vm=require('node:vm');
const app=require('./legacy-dashboard-source.cjs');
const start=app.indexOf('  async function _ticketQueueNames()');
const source=app.slice(start,app.indexOf('  // Shared by every ticket-add',start));
test('cold ticket picker uses configuration, never slow worker health',async()=>{
  const calls=[];
  const ctx={_uxqHealthCache:{},AbortController,setTimeout,clearTimeout,
    _fetchUxqHealth:()=>{throw new Error('must not request health');},
    fetch:async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>({ok:true,queues:[{queue:'TEST'},{queue:'TEST'},{queue:'OTHER'}]})};}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  assert.deepEqual(Array.from(await ctx._ticketQueueNames()),['TEST','OTHER']);
  assert.equal(calls[0].url,'/api/queue/config-options');
  assert.equal(calls[0].opts.cccPriority,'user');
  ctx._uxqHealthCache.queues=[{queue:'CACHED'}];
  assert.deepEqual(Array.from(await ctx._ticketQueueNames()),['CACHED']);
  assert.equal(calls.length,1);
});
