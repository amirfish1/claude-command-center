const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/app.js','utf8');
const start=source.indexOf('  async function awaitQueuedSpawnResult(');
const end=source.indexOf('\n  async function spawnFromInlineInput(',start);
function harness(items){
  const calls=[];
  const context=vm.createContext({Date,Promise,Object,AbortSignal,encodeURIComponent,
    setTimeout:resolve=>{resolve();},
    fetch:async(url,init)=>{calls.push({url,init});return{json:async()=>({graph:{items:[items.shift()]}})};},
  });
  vm.runInContext(source.slice(start,end),context);
  return {context,calls};
}
test('a spawn queued during drain follows the same durable work receipt until accepted',async()=>{
  const h=harness([{id:'work-fixture',state:'queued'},{id:'work-fixture',state:'completed',result:{ok:true,session_id:'new',spawn_id:'spawn-new'}}]);
  const result=await h.context.awaitQueuedSpawnResult({ok:true,queued:true,work_id:'work-fixture'});
  assert.equal(result.session_id,'new');assert.equal(result.queued,false);
  assert.equal(h.calls.length,2);
  assert.ok(h.calls.every(c=>c.url==='/api/control-plane/graph?root_id=work-fixture'&&!c.init.method&&c.init.cccPriority==='user'));
});
test('a definitive work failure is shown without resubmitting the paid spawn',async()=>{
  const h=harness([{id:'work-fixture',state:'failed',last_error:'engine unavailable'}]);
  const result=await h.context.awaitQueuedSpawnResult({ok:true,queued:true,work_id:'work-fixture'});
  assert.equal(result.ok,false);assert.equal(result.error,'engine unavailable');assert.equal(h.calls.length,1);
});
test('an accepted session does not need a receipt poll',async()=>{
  const h=harness([]),data={ok:true,session_id:'new',work_id:'work-fixture'};
  assert.equal(await h.context.awaitQueuedSpawnResult(data),data);assert.equal(h.calls.length,0);
});
