const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const boot = fs.readFileSync(require.resolve('../static/archive-boot.js'), 'utf8');
const defaults = fs.readFileSync(require.resolve('../static/conversation-defaults.js'), 'utf8');

function start({search='', framed=false, fail=false, saved=null}={}) {
  const reads=[], timers=[], cleared=[];
  const window={}; window.self=window; window.top=framed?{}:window;
  const response={ok:true};
  window.fetch=(url, init)=>{reads.push({url,init});return fail?Promise.reject(Error('offline')):Promise.resolve(response);};
  let now=10;
  vm.runInNewContext(boot, {window, location:{search}, URLSearchParams, AbortController,
    localStorage:{getItem:()=>saved}, performance:{now:()=>now+=10},
    setTimeout:fn=>(timers.push(fn),timers.length), clearTimeout:id=>cleared.push(id)});
  return {window, reads, timers, cleared, response};
}

test('dashboard read starts before body/application initialization and is reusable', async()=>{
  for(const search of ['', '?ccc_app=sessions', '?ccc_app=queues']) {
    const run=start({search});
    assert.equal(run.reads.length,1);
    assert.match(run.reads[0].url,/initial=1$/);
    assert.equal(await run.window.__cccArchiveBootstrap.promise,run.response);
    assert.equal(run.response._cccArchiveTiming.start,20);
    assert.equal(run.response._cccArchiveTiming.headers,30);
    assert.deepEqual(run.cleared,[1]);
  }
});
test('early read uses the same saved archive window as the normal loader',()=>{
  for(const saved of ['8h','1d','7d','all','bad']) {
    const run=start({saved});
    assert.equal(new URL(run.reads[0].url,'http://localhost').searchParams.get('window'),saved==='bad'?'7d':saved);
  }
});
test('app pages, conversation popouts and embedded pages leave the read to their existing gate',()=>{
  for(const opts of [{search:'?ccc_app=demo'},{search:'?ccc_popout=conversation'},
    {search:'?popout=conversation'},{framed:true}]) {
    const run=start(opts); assert.equal(run.reads.length,0);
    assert.equal(run.window.__cccArchiveBootstrap,undefined);
  }
});
test('failed early read resolves safely and timeout aborts its own request',async()=>{
  const run=start({fail:true}); run.timers[0]();
  assert.equal(run.reads[0].init.signal.aborted,true);
  assert.equal(await run.window.__cccArchiveBootstrap.promise,null);
  assert.deepEqual(run.cleared,[1]);
});
test('normal loader adopts the early response once without issuing another request',async()=>{
  const run=start(); let reads=0;
  const window=run.window;
  vm.runInNewContext(defaults,{window});
  const state={_whenDashboardUncovered:async()=>{},_archiveBootstrapUrl:window.__cccArchiveBootstrap.url,
    backgroundApiFetch:async()=>{reads++;return run.response;}};
  const initializers=window.CCCConversationDefaults.createInitializers({state});
  assert.equal(await initializers._archiveBootstrapFetchPromise(),run.response);
  assert.equal(reads,0);
  assert.equal(window.__cccArchiveBootstrap,undefined);
});
test('list telemetry includes early request time before the loader initialized',async()=>{
  const core=fs.readFileSync(require.resolve('../static/conversation-core.js'),'utf8');
  const start=core.indexOf('  async function loadArchiveAll(');
  const source=core.slice(start,core.indexOf('\n  }\n',start)+4);
  const events=[];
  const response={ok:true,status:200,_cccArchiveTiming:{start:100,headers:200},
    headers:{get:()=>null},json:async()=>({conversations:[{id:'one'}]})};
  const state={_archiveAllInflight:new Map(),_archiveAllEtag:new Map(),_archiveAllData:new Map(),
    _archiveAllDelta:new Map(),_archiveBootstrapConsumed:false,
    _archiveBootstrapUrl:'/api/conversations/list?window=7d&stale_ok=1&initial=1',
    _archiveBootstrapFetchPromise:Promise.resolve(response),ARCHIVE_ALL_FETCH_TIMEOUT_MS:45000};
  let now=1000;
  const context={sharedState:state,window:{__cccPerfRow(){}},performance:{now:()=>now+=100},
    URLSearchParams,AbortController,setTimeout:()=>0,clearTimeout(){},
    _archiveWindow:()=> '7d',_clientLog(){},
    _perfActiveElapsed:(a,b)=>b-a,_perfLongTaskMs:()=>0,_convResourceTiming:()=>null,
    _perfEvent:(kind,ms,extra)=>events.push({kind,ms,...extra}),
    backgroundApiFetch:()=>{throw Error('duplicate request');}};
  vm.createContext(context); vm.runInContext(source,context);
  assert.equal((await context.loadArchiveAll({initial:true})).length,1);
  const event=events.find(e=>e.kind==='list_read');
  assert.equal(event.detail.start_nav_ms,100);
  assert.equal(event.detail.ttfb_ms,100);
  assert(event.ms>=1000,'time before application initialization stays in the metric');
});
