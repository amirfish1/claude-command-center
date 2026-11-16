const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app-shell-boot.js'), 'utf8');

function boot({search='?ccc_app=demo', saved=null, apps=[], framed=false, provided=null}={}) {
  const frames=[], reads=[], listeners=new Map(), storage=new Map(), timers=[], classes=new Set();
  if (saved !== null) storage.set('ccc-rail-apps-v1', JSON.stringify(saved));
  const window = {addEventListener:(key, fn)=>listeners.set(key, fn),
    removeEventListener:key=>listeners.delete(key), dispatchEvent(){}};
  window.self=window; window.top=framed ? {} : window;
  const document = {body:null, documentElement:{appendChild:frame=>frames.push(frame),classList:{add:name=>classes.add(name)}},
    getElementById:id=>id==='cccAppStartup' && provided ? {textContent:JSON.stringify(provided)} : frames.find(f=>f.id===id),
    createElement:()=>({dataset:{},style:{},contentWindow:{},addEventListener(){}})};
  const fetch=async url=>{reads.push(url);return {ok:true,json:async()=>({apps})};};
  window.fetch=fetch;
  vm.runInNewContext(source, {window, document, URL, URLSearchParams, AbortSignal, Event,
    setTimeout:fn=>(timers.push(fn),timers.length), clearTimeout(){},
    location:{origin:'http://localhost:8090',search},matchMedia:()=>({matches:false}),
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
    fetch});
  return {window,frames,reads,listeners,storage,timers,classes};
}
const app={id:'demo',label:'Demo',nav:'/view/demo'};

test('server app data starts a fresh browser without a discovery round trip',()=>{
  const run=boot({provided:app});
  assert.deepEqual(run.reads,[]);
  assert.equal(run.frames[0].src,'/view/demo');
  assert.equal(run.classes.has('ccc-mobile-app-open'),true);
});
test('current server data wins over a stale saved route',()=>{
  const run=boot({provided:app,saved:[{...app,nav:'/view/old'}]});
  assert.equal(run.frames[0].src,'/view/demo');
  const disabled=boot({provided:{...app,enabled:false},saved:[app]});
  assert.equal(disabled.frames.length,0);
});

test('rail adoption retains the route from current HTML when its saved catalog is stale or unavailable',()=>{
  const rail=fs.readFileSync(require('node:path').join(__dirname,'../static/app-rail.js'),'utf8');
  const start=rail.indexOf('  function cachedApps()');
  const helper=rail.slice(start,rail.indexOf('\n  }',start)+4);
  for(const saved of [null,[{...app,nav:'/old',icon:'D'},{id:'sessions',nav:'/'}]]) {
    const context={window:{__cccStartupApp:app},APPS_CACHE_KEY:'test',
      localStorage:{getItem:()=>{if(saved===null)throw Error('unavailable');return JSON.stringify(saved);}}};
    vm.runInNewContext(helper+'\nresult=cachedApps();',context);
    const selected=context.result.find(candidate=>candidate.id===app.id);
    assert.equal(selected.nav,app.nav);
    if(saved) {assert.equal(selected.icon,'D');assert(context.result.some(candidate=>candidate.id==='sessions'));}
  }
});

test('saved app starts before body/styles without another discovery read',()=>{
  const run=boot({saved:[app]});
  assert.equal(run.frames.length,1); assert.equal(run.reads.length,0);
  assert.equal(run.frames[0].src,'/view/demo');
  assert.equal(run.frames[0].dataset.waitFrom,'0');
});
test('a new browser discovers and starts its app before dashboard boot',async()=>{
  const run=boot({apps:[app]});
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(run.reads,['/api/apps']);assert.equal(run.frames.length,1);
  assert.deepEqual(JSON.parse(run.storage.get('ccc-rail-apps-v1')),[app]);
});
test('only the selected same-origin frame releases the startup hold',async()=>{
  const run=boot({saved:[app]});let ready=false;
  run.window.__cccAppFrameReady.then(()=>ready=true);
  const notify=run.listeners.get('message'),frame=run.frames[0];
  notify({origin:'http://elsewhere.test',source:frame.contentWindow,data:{type:'ccc:app-fully-ready'}});
  notify({origin:'http://localhost:8090',source:{},data:{type:'ccc:app-fully-ready'}});
  await Promise.resolve();assert.equal(ready,false);
  notify({origin:'http://localhost:8090',source:frame.contentWindow,data:{type:'ccc:app-fully-ready'}});
  await Promise.resolve();assert.equal(ready,true);
});
test('unknown, disabled and unsafe targets do not create a frame',async()=>{
  for(const candidate of [null,{...app,enabled:false},{...app,nav:'https://elsewhere.test/'},{...app,nav:'javascript:alert(1)'},{...app,nav:'/'}]){
    const run=boot({apps:candidate?[candidate]:[]});
    await new Promise(resolve=>setImmediate(resolve));
    await run.window.__cccAppFrameReady;
    assert.equal(run.frames.length,0);
    assert.equal(run.classes.has('ccc-mobile-app-open'),false);
  }
});
test('normal dashboards, core apps and framed pages keep their normal boot',()=>{
  for(const opts of [{search:''},{search:'?ccc_app=sessions'},{search:'?ccc_app=queues'},{framed:true}]){
    const run=boot(opts);assert.equal(run.frames.length,0);assert.equal(run.reads.length,0);
    assert.equal(run.window.__cccAppFrameReady,undefined);
  }
});

test('background reads and dashboard work wait for the app, but writes do not',async()=>{
  const run=boot({saved:[app]});let started=false;
  run.window.__cccDeferDashboardBoot(()=>started=true);
  const read=run.window.fetch('/api/features');
  await run.window.fetch('/api/action',{method:'POST'});
  assert.deepEqual(run.reads,['/api/action']);assert.equal(started,false);
  run.listeners.get('message')({origin:'http://localhost:8090',source:run.frames[0].contentWindow,data:{type:'ccc:app-fully-ready'}});
  await read;
  assert.equal(started,true);assert.equal(run.window.__cccDeferDashboardBoot,undefined);
  assert.deepEqual(run.reads,['/api/action','/api/features']);
});
test('a stalled app releases dashboard initialization and reads at the bound',async()=>{
  const run=boot({saved:[app]});let started=false;
  run.window.__cccDeferDashboardBoot(()=>started=true);
  const read=run.window.fetch('/api/features');
  run.timers[0]();await read;
  assert.equal(started,true);assert.deepEqual(run.reads,['/api/features']);
});

test('late document hooks run after closure constants initialize',async()=>{
  const appSource=fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
  const start=appSource.indexOf('  function onDashboardDocumentReady(');
  const helper=appSource.slice(start,appSource.indexOf('\n  }',start)+4);
  const output=[];
  vm.runInNewContext(helper+'\nonDashboardDocumentReady(()=>output.push(later)); const later=42;',
    {document:{readyState:'complete'},queueMicrotask,output});
  assert.deepEqual(output,[]);await Promise.resolve();assert.deepEqual(output,[42]);
});
