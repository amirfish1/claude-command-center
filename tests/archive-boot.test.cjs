const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {functions} = require('./conversation-source.cjs');
const archiveLoader = functions(['_decodeArchiveRowLayouts', '_perfListDirect', 'loadArchiveAll']);
const boot = fs.readFileSync(require.resolve('../static/archive-boot.js'), 'utf8');
const defaults = fs.readFileSync(require.resolve('../static/conversation-defaults.js'), 'utf8');

function start({search='', framed=false, fail=false, saved=null}={}) {
  const reads=[], timers=[], cleared=[];
  const window={}; window.self=window; window.top=framed?{}:window;
  const response={ok:true};
  window.fetch=(url, init)=>{reads.push({url,init});if(window.__fetchImpl)return window.__fetchImpl(url,init);return fail?Promise.reject(Error('offline')):Promise.resolve(response);};
  let now=10;
  vm.runInNewContext(boot, {window, location:{search}, URLSearchParams, AbortController, Response, Date,
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
  const source = archiveLoader;
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

test('overlapped history fetches immediately but parses only after initial paint', async () => {
  const source = archiveLoader;
  let releasePaint, reads = 0, parses = 0;
  const afterPaint = new Promise(resolve => { releasePaint = resolve; });
  const response = {ok: true, status: 200, headers: {get: () => null},
    json: async () => { parses++; return {conversations: [{id: 'one'}]}; }};
  const state = {_archiveAllInflight: new Map(), _archiveAllEtag: new Map(),
    _archiveAllData: new Map(), _archiveAllDelta: new Map(), ARCHIVE_ALL_FETCH_TIMEOUT_MS: 45000};
  const context = {sharedState: state, window: {}, performance, URLSearchParams, AbortController,
    setTimeout: () => 0, clearTimeout() {}, _clientLog() {}, _scheduleArchiveSidebarCache() {},
    backgroundApiFetch: async () => { reads++; return response; }};
  vm.createContext(context);
  vm.runInContext(source, context);
  const loading = context.loadArchiveAll({window: '7d', afterPaint});
  await new Promise(setImmediate);
  assert.equal(reads, 1);
  assert.equal(parses, 0);
  assert.equal(state._archiveAllData.size, 0);
  releasePaint();
  assert.equal((await loading)[0].id, 'one');
  assert.equal(parses, 1);
  assert.equal(state._archiveAllInflight.size, 0);
});
test('list reads go to the reader port first and fall back to the dashboard (CCC-679)',async()=>{
  const run=start({search:'?ccc_app=demo'});
  const {window}=run; const dash=[];
  window.location={protocol:'http:',hostname:'127.0.0.1',origin:'http://127.0.0.1:8091'};
  const via=url=>{dash.push(url);return Promise.resolve({ok:true,status:200,via:'dash'});};
  // No port injected: the dashboard serves it.
  await window.__cccListFetch('/api/conversations/list?window=7d',{},via);
  assert.deepEqual(dash,['/api/conversations/list?window=7d']);
  assert.equal(run.reads.length,0);
  window.__CCC_LIST_READER_PORT__=18091;
  await window.__cccListFetch('/api/conversations/list?window=7d',{},via);
  assert.equal(run.reads.at(-1).url,'http://127.0.0.1:18091/api/conversations/list?window=7d');
  assert.equal(run.reads.at(-1).init.credentials,'include');
  assert.equal(dash.length,1);
  // Other URLs never go direct.
  await window.__cccListFetch('/api/sessions',{},via);
  assert.equal(dash.at(-1),'/api/sessions');
  // A decline (503) is retried on the dashboard and parks direct reads.
  run.response.ok=false; run.response.status=503;
  const res=await window.__cccListFetch('/api/conversations/list?window=8h',{},via);
  assert.equal(res.via,'dash');
  const before=run.reads.length;
  await window.__cccListFetch('/api/conversations/list?window=8h',{},via);
  assert.equal(run.reads.length,before,'parked after a decline');
});

test('a silent reader falls back; a busy live reader keeps its read (CCC-681)',async()=>{
  const tick=()=>new Promise(setImmediate);
  const setup=()=>{
    const run=start({search:'?ccc_app=demo'});
    run.window.location={protocol:'https:',hostname:'box.example',origin:'https://box.example'};
    run.window.__CCC_LIST_READER_PORT__=18091;
    run.dash=[];
    run.via=url=>{run.dash.push(url);return Promise.resolve({ok:true,status:200,via:'dash'});};
    run.fire=async()=>{await tick();await tick();run.timers.at(-1)();await tick();await tick();};
    return run;
  };
  const hang=init=>new Promise((_,reject)=>init.signal.addEventListener('abort',()=>reject(Error('aborted'))));
  const isPing=url=>url.endsWith('/api/conv-reader/ping');
  // Wedged connection: no headers and no ping answer -> dashboard.
  let run=setup();
  run.window.__fetchImpl=(url,init)=>hang(init);
  let res=run.window.__cccListFetch('/api/conversations/list?window=7d',{},run.via);
  await run.fire();          // patience elapsed: ping sent
  await run.fire();          // ping timed out: direct aborted
  assert.equal((await res).via,'dash');
  assert.ok(run.reads.some(r=>isPing(r.url)));
  // Parked: the next read goes straight to the dashboard.
  await run.window.__cccListFetch('/api/conversations/list?window=7d',{},run.via);
  assert.equal(run.dash.length,2);
  // Busy but alive (cold build): the ping answers, the read is kept.
  run=setup(); let release;
  run.window.__fetchImpl=(url,init)=>isPing(url)?Promise.resolve({ok:true,status:200})
    :new Promise(resolve=>{release=resolve;});
  res=run.window.__cccListFetch('/api/conversations/list?window=7d',{},run.via);
  await run.fire();
  await run.fire();
  release({ok:true,status:304});
  assert.equal((await res).status,304);
  assert.equal(run.dash.length,0,'a live reader never moves its build to the dashboard');
});
test('direct body stall falls back; healthy direct body keeps url and header time (CCC-681)',async()=>{
  const tick=()=>new Promise(setImmediate);
  const setup=()=>{
    const run=start({search:'?ccc_app=demo'});
    run.window.location={protocol:'https:',hostname:'box.example',origin:'https://box.example'};
    run.window.__CCC_LIST_READER_PORT__=18091;
    run.fire=async()=>{await tick();await tick();run.timers.at(-1)();await tick();await tick();};
    return run;
  };
  const streamed=(chunks,hang)=>(url,init)=>{
    if(url.endsWith('/api/conv-reader/ping'))
      return new Promise((_,reject)=>init.signal.addEventListener('abort',()=>reject(Error('aborted'))));
    const body=new ReadableStream({async pull(c){
      if(chunks.length){c.enqueue(new TextEncoder().encode(chunks.shift()));return;}
      if(hang) return new Promise((_,reject)=>init.signal.addEventListener('abort',()=>{c.error(Error('aborted'));reject(Error('aborted'));}));
      c.close();
    }});
    const r=new Response(body,{status:200,headers:{'ETag':'"e1"'}});
    Object.defineProperty(r,'url',{value:url});
    return Promise.resolve(r);
  };
  const dash=[];
  const via=url=>{dash.push(url);return Promise.resolve({ok:true,status:200,via:'dash'});};
  // Stalled body after headers, silent reader: falls back.
  let run=setup();
  run.window.__fetchImpl=streamed(['{"conv'],true);
  let res=run.window.__cccListFetch('/api/conversations/list?window=7d',{},via);
  await run.fire(); await run.fire();
  assert.equal((await res).via,'dash');
  // Healthy body: buffered copy keeps the reader URL, ETag and header time.
  run=setup();
  run.window.__fetchImpl=streamed(['{"conversations":','[{"id":"a"}]}'],false);
  res=await run.window.__cccListFetch('/api/conversations/list?window=7d',{},via);
  assert.equal(res.url,'https://box.example:18091/api/conversations/list?window=7d');
  assert.equal(res.headers.get('ETag'),'"e1"');
  assert.ok(Number.isFinite(res._cccHeadersAt));
  assert.deepEqual(await res.json(),{conversations:[{id:'a'}]});
  assert.equal(dash.length,1);
  // A caller abort propagates; it never retries on the dashboard.
  run=setup(); const ctl=new AbortController();
  run.window.__fetchImpl=(url,init)=>new Promise((_,reject)=>init.signal.addEventListener('abort',()=>reject(Error('aborted'))));
  res=run.window.__cccListFetch('/api/conversations/list?window=7d',{signal:ctl.signal},via);
  ctl.abort();
  await assert.rejects(res);
  assert.equal(dash.length,1);
});
