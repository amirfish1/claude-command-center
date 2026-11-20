const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const vm = require('node:vm');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const boot = fs.readFileSync('static/archive-boot.js', 'utf8');
const {functions} = require('./conversation-source.cjs');
const archive = functions(['_mergeArchiveListDelta', 'loadArchiveAll']);
const fullUrl = '/api/conversations/list?window=7d&stale_ok=1';
const rows = [{session_id:'recent', nested:{items:[1]}}, {session_id:'older'}];

function setup({snapshot, response, early} = {}) {
  const reads = [], saves = [];
  const state = {_archiveAllInflight:new Map(), _archiveAllData:new Map(),
    _archiveAllEtag:new Map(), _archiveAllDelta:new Map(), ARCHIVE_ALL_FETCH_TIMEOUT_MS:45000};
  const cache = snapshot ? {url:snapshot.url, read:Promise.resolve(snapshot),
    fetch:early, save:value => saves.push(value)} : undefined;
  const context = {sharedState:state, window:{__cccArchiveSnapshot:cache},
    performance, URLSearchParams, AbortController, setTimeout:()=>0, clearTimeout(){},
    _archiveWindow:()=> '7d', _clientLog(){}, _scheduleArchiveSidebarCache(){},
    backgroundApiFetch:async(url, init) => { reads.push({url, init}); return response; }};
  vm.createContext(context); vm.runInContext(archive, context);
  return {context, state, reads, saves};
}
function reply(body, status=200) {
  return {ok:status===200, status, headers:{get:()=>null}, json:async()=>body};
}

test('a persisted base still waits for validation, then merges the current ordered delta', async () => {
  let resolve;
  const snapshot = {url:fullUrl, rows, delta:'old', etag:'"old"'};
  const f = setup({snapshot, early:new Promise(r => {resolve=r;})});
  const loading = f.context.loadArchiveAll();
  await new Promise(setImmediate);
  assert.equal(f.state._archiveAllData.get(fullUrl), rows);
  assert.equal(f.context.window.__cccArchiveProgress, undefined, 'disk data alone never claims readiness');
  resolve(reply({delta:true, ids:['older','recent','new'], delta_token:'current',
    conversations:[{session_id:'recent', modified:42}, {session_id:'new'}]}));
  const result = await loading;
  assert.deepEqual(Array.from(result, r=>r.session_id), ['older','recent','new']);
  assert.equal(result[1].modified,42);
  assert.equal(f.reads.length,0,'the early conditional response is consumed once');
  assert.equal(f.saves[0].delta,'current');
  assert.equal(f.saves[0].rows,result,'data and token are saved as the same envelope');
  assert.equal(f.state._archiveAllEtag.has(fullUrl),false);
  f.context.window.__cccArchiveSnapshot.fetch = Promise.reject(Error('must not reuse consumed response'));
  // No second consumer should observe this promise at all.
  f.context.window.__cccArchiveSnapshot.fetch.catch(()=>{});
  f.context.backgroundApiFetch = async(url, init) => {f.reads.push({url,init});return reply(null,304);};
  await f.context.loadArchiveAll();
  assert.equal(f.reads.length,1);
  assert.match(f.reads[0].url,/since=current$/);
});

test('a fresh early full body needs no saved base and bypasses the preview fence', async () => {
  const f = setup({response:reply({conversations:[]})});
  const response = reply(null);
  response._cccArchiveParsedBody = Promise.resolve({conversations:rows, delta_token:'fresh'});
  response.json = () => {throw Error('early full body must not parse twice');};
  f.context.window.__cccArchiveSnapshot = {
    url:fullUrl, read:Promise.resolve(null), fetch:Promise.resolve(response), save() {},
  };
  let validated = 0;
  const result = await f.context.loadArchiveAll({
    afterPaint:new Promise(() => {}), onValidated:() => validated++,
  });
  assert.deepEqual(Array.from(result), rows);
  assert.equal(validated,1);
  assert.equal(f.reads.length,0,'a fresh full boot response is consumed only once');
  assert.equal(f.state._archiveAllDelta.get(fullUrl),'fresh');
  assert.equal(f.context.window.__cccArchiveProgress,undefined,'only the renderer marks completeness');
});

test('304 reuses the exact validated rows; an expired server token replaces the full base', async () => {
  const snapshot = {url:fullUrl, rows, delta:'old', etag:'"old"'};
  const same = setup({snapshot, early:Promise.resolve(reply(null,304))});
  assert.deepEqual(Array.from(await same.context.loadArchiveAll()),rows);
  const changed = setup({snapshot, early:Promise.resolve(reply({conversations:[{session_id:'replacement'}],delta_token:'new'}))});
  assert.deepEqual(Array.from(await changed.context.loadArchiveAll(), r=>r.session_id),['replacement']);
  assert.equal(changed.state._archiveAllDelta.get(fullUrl),'new');
});

test('an unmergeable early delta restores the preview before awaiting a full resync', async () => {
  const snapshot = {url:fullUrl, rows, delta:'old'};
  const f = setup({snapshot, early:Promise.resolve(reply({
    delta:true, ids:['missing-base-row'], conversations:[], delta_token:'new',
  }))});
  const order = [];
  let finish;
  f.context.window.__cccArchiveSnapshot.onResync = () => order.push('restore-preview');
  f.context.backgroundApiFetch = () => {
    order.push('full-resync');
    return new Promise(resolve => {finish=resolve;});
  };
  const loading = f.context.loadArchiveAll();
  await new Promise(setImmediate);
  assert.deepEqual(order, ['restore-preview', 'full-resync']);
  assert.equal(f.context.window.__cccArchiveProgress, undefined);
  finish(reply(null,500));
  assert.equal(await loading, null);
  assert.equal(f.state._archiveAllData.get(fullUrl), rows, 'the failed resync does not replace the saved base');
});

test('failed early requests retry normally; mismatched windows and initial reads never adopt the base', async () => {
  const snapshot = {url:fullUrl, rows, delta:'old', etag:'"old"'};
  const fallback = setup({snapshot, early:Promise.resolve(null), response:reply(null,304)});
  assert.equal((await fallback.context.loadArchiveAll()).length,2);
  assert.match(fallback.reads[0].url,/since=old$/);
  assert.equal(fallback.reads[0].init.headers['If-None-Match'],'"old"');
  for (const opts of [{window:'1d'}, {initial:true}, {includePrs:true}, {staleOk:false}]) {
    const f = setup({snapshot, early:Promise.resolve(reply(null,304)), response:reply({conversations:[]})});
    await f.context.loadArchiveAll(opts);
    assert.equal(f.reads.length,1);
    assert.ok(!f.reads[0].url.includes('since='));
    assert.equal(f.reads[0].init.headers,undefined);
  }
});

test('parsed full and delta replies validate before a pending preview paint without parsing twice', {timeout:1000}, async () => {
  const snapshot = {url:fullUrl, rows, delta:'old', etag:'"old"'};
  for (const body of [
    {conversations:[{session_id:'replacement'}], delta_token:'new'},
    {delta:true, conversations:[{session_id:'recent', changed:true}],
      ids:['older','recent'], delta_token:'new'},
  ]) {
    const response=reply(null);
    response._cccArchiveParsedBody=Promise.resolve(body);
    response.json=()=>{throw Error('response body was already consumed');};
    const f=setup({snapshot,early:Promise.resolve(response)});
    let validated=0;
    const result=await f.context.loadArchiveAll({afterPaint:new Promise(()=>{}),onValidated:()=>validated++});
    assert.deepEqual(Array.from(result,r=>r.session_id),body.ids||['replacement']);
    assert.equal(validated,1);
    assert.equal(f.state._archiveAllDelta.get(fullUrl),'new');
  }
  const bad=reply(null);
  bad._cccArchiveParsedBody=Promise.resolve({wrong:'shape'});
  const f=setup({snapshot,early:Promise.resolve(bad)});
  let validated=false;
  assert.equal(await f.context.loadArchiveAll({afterPaint:new Promise(()=>{}),onValidated:()=>{validated=true;}}),null);
  assert.equal(validated,false,'malformed bodies never win the completeness race');
});

test('superseding an early preview aborts it without a replacement request and retains its timing', async () => {
  const f=setup({snapshot:{url:fullUrl,rows,delta:'old'}}), controller=new AbortController(), events=[];
  let release, cancellations=0;
  const helper=f.context.window.__cccArchiveSnapshot;
  helper.recentStart=100;
  helper.cancelRecent=()=>{cancellations++;release(null);};
  f.state._archiveBootstrapUrl=fullUrl+'&initial=1';
  f.state._archiveBootstrapFetchPromise=new Promise(resolve=>{release=resolve;});
  Object.assign(f.context,{
    performance:{now:()=>1000},
    _perfActiveElapsed:(a,b)=>b-a,_perfLongTaskMs:()=>0,_convResourceTiming:()=>null,
    _perfEvent:(kind,ms,extra)=>events.push({kind,ms,...extra}),
  });
  f.context.window.__cccPerfRow=()=>{};
  const loading=f.context.loadArchiveAll({initial:true,controller});
  await new Promise(setImmediate);
  controller._cccArchiveSupersededAt=500;
  controller.abort('archive-complete');
  assert.equal(await loading,null);
  assert.equal(cancellations,1);
  assert.equal(f.reads.length,0,'an aborted bootstrap must not retry as another preview');
  const metric=events.find(e=>e.kind==='list_read');
  assert.equal(metric.detail.status,'superseded');
  assert.equal(metric.detail.start_nav_ms,100,'the original early request remains in the measurement');
  assert.equal(metric.ms,400,'the cancelled read ends at the actual abort');
  assert.equal(metric.detail.settle_delay_ms,500,'later handler delay remains visible');
});

let browser, server, origin, slowPreview=false, coldFullFailure='';
before(async () => {
  server = http.createServer((req,res) => {
    if (req.url==='/archive-boot.js') {res.setHeader('Content-Type','application/javascript');res.end(boot);return;}
    if (req.url.startsWith('/api/conversations/list')) {
      if (coldFullFailure && !req.url.includes('initial=1') && !req.url.includes('since=')) {
        res.writeHead(coldFullFailure === 'http' ? 500 : 200);
        res.end(coldFullFailure === 'http' ? '' : 'invalid JSON');return;
      }
      if (req.url.includes('since=error')) {res.writeHead(503);res.end();return;}
      if (req.url.includes('since=bad-json')) {res.end('broken JSON');return;}
      if (req.url.includes('since=hang') || slowPreview && req.url.includes('initial=1')) {
        res.writeHead(200,{'Content-Type':'application/json'});res.flushHeaders();
        const timer=setTimeout(()=>res.end(JSON.stringify({conversations:rows})),1000);
        res.on('close',()=>clearTimeout(timer));return;
      }
      if (req.url.includes('since=valid')) {res.writeHead(304);res.end();return;}
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify({conversations:rows}));return;
    }
    res.setHeader('Content-Type','text/html');res.end('<script src="/archive-boot.js"></script>');
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  origin='http://127.0.0.1:'+server.address().port;
  browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
after(async () => {await browser?.close();await new Promise(r=>server.close(r));});

async function seed(page,delta) {
  await page.evaluate(({url,rows,delta})=>{
    window.__cccArchiveSettled=true;
    window.__cccArchiveSnapshot.save({url,rows,delta,etag:'"example"'});
  },{url:fullUrl,rows,delta});
  await page.waitForFunction(async delta=>{
    const db=await new Promise(resolve=>{const r=indexedDB.open('ccc-archive-response',1);r.onsuccess=()=>resolve(r.result);});
    try {return await new Promise(resolve=>{const r=db.transaction('snapshot').objectStore('snapshot').get('last');r.onsuccess=()=>resolve(r.result?.delta===delta && !!localStorage.getItem('ccc-archive-response-at:7d'));});}
    finally {db.close();}
  },{},delta);
}

test('an intentionally unsent preview never starts a replacement request', async () => {
  const f=setup({snapshot:{url:fullUrl,rows},response:reply({conversations:rows})});
  f.state._archiveBootstrapUrl=fullUrl+'&initial=1';
  f.state._archiveBootstrapFetchPromise=Promise.resolve(null);
  f.context.window.__cccArchiveSnapshot.skippedRecent=true;
  assert.equal(await f.context.loadArchiveAll({initial:true}),null);
  assert.equal(f.reads.length,0);
  assert.equal(f.state._archiveAllData.has(fullUrl+'&initial=1'),false);
  await f.context.loadArchiveAll({initial:true,window:'1d'});
  assert.equal(f.reads.length,1,'a different scope still uses its own preview');
});

test('a fresh profile prefetches a parsed full response while retaining its recent read', async () => {
  const context=await browser.createBrowserContext(),page=await context.newPage();
  try {
    await page.goto(origin,{waitUntil:'load'});
    const state=await page.evaluate(async()=>{
      const helper=window.__cccArchiveSnapshot;
      const base=await helper.read;
      const response=await helper.fetch;
      return {base,body:await response._cccArchiveParsedBody,
        recent:(await (await window.__cccArchiveBootstrap.promise).json()).conversations,
        ready:!!window.__cccArchiveProgress?.complete};
    });
    assert.equal(state.base,null);
    assert.deepEqual(state.body.conversations,rows);
    assert.deepEqual(state.recent,rows);
    assert.equal(state.ready,false);
  } finally {await context.close();}
});

test('failed fresh full prefetches preserve the ordinary recent response', async () => {
  for (const failure of ['http','json']) {
    const context=await browser.createBrowserContext(),page=await context.newPage();
    coldFullFailure=failure;
    try {
      await page.goto(origin,{waitUntil:'load'});
      assert.deepEqual(await page.evaluate(async()=>{
        await window.__cccArchiveSnapshot.fetch;
        return (await (await window.__cccArchiveBootstrap.promise).json()).conversations;
      }),rows);
      assert.equal(await page.evaluate(()=>!!window.__cccArchiveProgress?.complete),false);
    } finally {coldFullFailure='';await context.close();}
  }
});

test('a committed snapshot hint avoids an unsent preview for 304 and parsed 200 responses', async () => {
  const context=await browser.createBrowserContext(),page=await context.newPage();
  try {
    await page.evaluateOnNewDocument(()=>{
      window.__archiveReads=[];
      const fetch=window.fetch.bind(window);
      window.fetch=(url,...args)=>{window.__archiveReads.push(String(url));return fetch(url,...args);};
    });
    await page.goto(origin,{waitUntil:'load'});
    assert.equal(await page.evaluate(()=>window.__archiveReads.filter(u=>u.includes('initial=1')).length),1);
    for (const delta of ['valid','refresh']) {
      await seed(page,delta);
      await page.reload({waitUntil:'load'});
      assert.deepEqual(await page.evaluate(async()=>{
        const helper=window.__cccArchiveSnapshot,response=await helper.fetch;
        if(response.status!==304)await response._cccArchiveParsedBody;
        return {status:response.status,skipped:helper.skippedRecent,
          preview:await window.__cccArchiveBootstrap.promise,
          reads:window.__archiveReads.filter(u=>u.includes('initial=1')).length,
          ready:!!window.__cccArchiveProgress};
      }),{status:delta==='valid'?304:200,skipped:true,preview:null,reads:0,ready:false});
    }
  } finally {await context.close();}
});

test('the active loader requests a recent preview when it has no saved rows to show', async () => {
  for (const useSavedPreview of [false, true]) {
    const f = setup({snapshot:{url:fullUrl, rows}});
    const helper = f.context.window.__cccArchiveSnapshot;
    const response = reply({conversations:rows});
    let finish, starts = 0;
    f.state._archiveBootstrapUrl = fullUrl + '&initial=1';
    f.state._archiveBootstrapFetchPromise = new Promise(resolve => { finish = resolve; });
    helper.startRecent = () => { starts++; finish(response); };
    helper.cancelRecent = () => {};
    const loading = f.context.loadArchiveAll({initial:true, useSavedPreview});
    if (useSavedPreview) {
      assert.equal(starts, 0);
      helper.skippedRecent = true;
      finish(null);
      assert.equal(await loading, null);
    } else {
      assert.equal((await loading).length, rows.length);
      assert.equal(starts, 1);
    }
    assert.equal(f.reads.length, 0, 'the application reuses the boot request');
  }
});

test('a slow validated full read holds the preview until the active view requests it', async () => {
  const context=await browser.createBrowserContext(),page=await context.newPage();
  try {
    await page.goto(origin,{waitUntil:'load'});
    await seed(page,'hang');
    await page.evaluateOnNewDocument(()=>{
      const set=window.setTimeout;
      window.setTimeout=(fn,ms,...args)=>set(fn,ms===1000?60:ms,...args);
    });
    await page.reload({waitUntil:'load'});
    assert.deepEqual(await page.evaluate(async()=>{
      const helper=window.__cccArchiveSnapshot;
      await helper.read;
      await new Promise(resolve => setTimeout(resolve, 150));
      const waited=helper.recentStart===undefined;
      helper.startRecent();
      const recent=await window.__cccArchiveBootstrap.promise;
      const rows=(await recent.json()).conversations;
      await helper.fetch;
      return {waited,count:rows.length,skipped:!!helper.skippedRecent};
    }),{waited:true,count:2,skipped:false});
  } finally {await context.close();}
});

test('missing cached bodies and opt-out ignore a remaining scheduling hint', async () => {
  const context=await browser.createBrowserContext(),page=await context.newPage();
  try {
    await page.goto(origin,{waitUntil:'load'});
    await page.evaluate(()=>window.__cccArchiveSnapshot.read);
    await page.evaluate(()=>localStorage.setItem('ccc-archive-response-at:7d',String(Date.now())));
    await page.reload({waitUntil:'load'});
    assert.equal(await page.evaluate(async()=>{
      const recent=await window.__cccArchiveBootstrap.promise;
      return (await recent.json()).conversations.length;
    }),2);
    await seed(page,'valid');
    await page.evaluate(()=>localStorage.setItem('ccc-conv-cache','0'));
    await page.reload({waitUntil:'load'});
    assert.deepEqual(await page.evaluate(async()=>({helper:!!window.__cccArchiveSnapshot,
      count:(await (await window.__cccArchiveBootstrap.promise).json()).conversations.length})),{helper:false,count:2});
  } finally {await context.close();}
});

test('failed or malformed complete replies release the ordinary preview', async () => {
  const context=await browser.createBrowserContext(),page=await context.newPage();
  try {
    await page.goto(origin,{waitUntil:'load'});
    for (const delta of ['error','bad-json']) {
      await seed(page,delta);
      await page.reload({waitUntil:'load'});
      assert.deepEqual(await page.evaluate(async()=>({
        count:(await (await window.__cccArchiveBootstrap.promise).json()).conversations.length,
        skipped:!!window.__cccArchiveSnapshot.skippedRecent,
      })),{count:2,skipped:false});
    }
  } finally {await context.close();}
});

test('a real full response is parsed once during prefetch and its body retains the timeout', async () => {
  const context=await browser.createBrowserContext(),page=await context.newPage();
  try {
    await page.goto(origin,{waitUntil:'load'});
    await seed(page,'refresh');
    await page.reload({waitUntil:'load'});
    assert.deepEqual(await page.evaluate(async()=>{
      const r=await window.__cccArchiveSnapshot.fetch,d=await r._cccArchiveParsedBody;
      return {status:r.status,used:r.bodyUsed,count:d.conversations.length};
    }),{status:200,used:true,count:2});
    await seed(page,'hang');
    await page.evaluateOnNewDocument(()=>{
      const original=window.setTimeout;
      window.setTimeout=(fn,ms,...args)=>original(fn,ms===45000?250:ms,...args);
    });
    await page.reload({waitUntil:'load'});
    assert.equal(await page.evaluate(async()=>{
      const r=await window.__cccArchiveSnapshot.fetch;
      try {await r._cccArchiveParsedBody;return 'not aborted';}catch(e){return e.name;}
    }),'AbortError');
  } finally {await context.close();}
});

test('cancelling a superseded preview aborts the actual early response body', async () => {
  const context=await browser.createBrowserContext(),page=await context.newPage();
  try {
    slowPreview=true;
    await page.goto(origin,{waitUntil:'domcontentloaded'});
    assert.equal(await page.evaluate(async()=>{
      const r=await window.__cccArchiveBootstrap.promise,body=r.json();
      window.__cccArchiveSnapshot.cancelRecent();
      try {await body;return 'not aborted';}catch(e){return e.name;}
    }),'AbortError');
  } finally {slowPreview=false;await context.close();}
});

test('real IndexedDB retains one scoped snapshot and a reload obtains an actual server 304', async () => {
  const context=await browser.createBrowserContext(), page=await context.newPage();
  try {
    await page.goto(origin,{waitUntil:'load'});
    await page.evaluate(async({url,rows})=>{
      window.__cccArchiveSettled=true;
      window.__cccArchiveSnapshot.save({url,rows,delta:'valid',etag:'"valid"'});
    },{url:fullUrl,rows});
    await page.waitForFunction(async()=>{
      const db=await new Promise(resolve=>{const r=indexedDB.open('ccc-archive-response',1);r.onsuccess=()=>resolve(r.result)});
      try {return await new Promise(resolve=>{const r=db.transaction('snapshot').objectStore('snapshot').get('last');r.onsuccess=()=>resolve(!!r.result);});}
      finally {db.close();}
    });
    assert.deepEqual(await page.evaluate(async()=>{
      const db=await new Promise(resolve=>{const r=indexedDB.open('ccc-archive-response',1);r.onsuccess=()=>resolve(r.result);});
      try {return await new Promise(resolve=>{
        const r=db.transaction('snapshot').objectStore('snapshot').get('last');
        r.onsuccess=()=>resolve({hasRows:Object.hasOwn(r.result,'rows'), decoded:JSON.parse(r.result.rowsJson),
          bytes:r.result.bytes, textBytes:r.result.rowsJson.length*2});
      });} finally {db.close();}
    }),{hasRows:false,decoded:rows,bytes:JSON.stringify(rows).length*2,textBytes:JSON.stringify(rows).length*2});
    await page.reload({waitUntil:'load'});
    assert.deepEqual(await page.evaluate(async()=>{
      const helper=window.__cccArchiveSnapshot, saved=await helper.read, response=await helper.fetch;
      return {count:saved.rows.length,delta:saved.delta,status:response.status};
    }),{count:2,delta:'valid',status:304});
    await page.evaluate(()=>localStorage.setItem('ccc-conv-cache','0'));
    await page.reload({waitUntil:'load'});
    assert.equal(await page.evaluate(()=>!!window.__cccArchiveSnapshot),false,'existing cache opt-out applies');
  } finally {await context.close();}
});

test('expired, future-dated, oversized, malformed and other-window records cannot seed validation', async () => {
  const context=await browser.createBrowserContext(), page=await context.newPage();
  try {
    await page.evaluateOnNewDocument(()=>{
      const fetch=window.fetch.bind(window);
      window.__archiveRequests=[];
      window.fetch=(url,init)=>{window.__archiveRequests.push({url,headers:init?.headers});return fetch(url,init);};
    });
    await page.goto(origin,{waitUntil:'load'});
    await page.evaluate(()=>window.__cccArchiveSnapshot.read);
    const base={url:fullUrl, rows, bytes:200, delta:'valid', at:Date.now()};
    for (const patch of [{at:Date.now()-86400001},{at:Date.now()+86400000},
      {bytes:32*1024*1024+1},{rows:[null]},{url:fullUrl.replace('7d','1d')},
      {rowsJson:'broken JSON'},{rowsJson:'null'},{rowsJson:'{}'},{rowsJson:'[null]'}]) {
      await page.evaluate(async value=>{
        const db=await new Promise(resolve=>{const r=indexedDB.open('ccc-archive-response',1);r.onsuccess=()=>resolve(r.result)});
        try {await new Promise((resolve,reject)=>{const tx=db.transaction('snapshot','readwrite');tx.objectStore('snapshot').put(value,'last');tx.oncomplete=resolve;tx.onerror=reject;});}
        finally {db.close();}
      },{...base,...patch});
      await page.reload({waitUntil:'load'});
      assert.deepEqual(await page.evaluate(async()=>{
        const read=await window.__cccArchiveSnapshot.read;
        const response=await window.__cccArchiveSnapshot.fetch;
        return {read,status:response.status,rows:(await response._cccArchiveParsedBody).conversations,
          conditional:window.__archiveRequests.some(r=>r.url.includes('since=') || r.headers?.['If-None-Match'])};
      }),{read:null,status:200,rows,conditional:false});
    }
  } finally {await context.close();}
});

test('legacy object snapshots still seed the current server validation', async () => {
  const context=await browser.createBrowserContext(),page=await context.newPage();
  try {
    await page.goto(origin,{waitUntil:'load'});
    await page.evaluate(()=>window.__cccArchiveSnapshot.read);
    await page.evaluate(async value=>{
      const db=await new Promise(resolve=>{const r=indexedDB.open('ccc-archive-response',1);r.onsuccess=()=>resolve(r.result);});
      try {await new Promise((resolve,reject)=>{
        const tx=db.transaction('snapshot','readwrite');tx.objectStore('snapshot').put(value,'last');
        tx.oncomplete=resolve;tx.onerror=reject;
      });} finally {db.close();}
    },{url:fullUrl,rows,bytes:JSON.stringify(rows).length*2,delta:'valid',at:Date.now()});
    await page.reload({waitUntil:'load'});
    assert.deepEqual(await page.evaluate(async()=>({rows:(await window.__cccArchiveSnapshot.read).rows,
      status:(await window.__cccArchiveSnapshot.fetch).status})),{rows,status:304});
  } finally {await context.close();}
});
