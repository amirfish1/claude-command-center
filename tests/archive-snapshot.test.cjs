const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const vm = require('node:vm');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const boot = fs.readFileSync('static/archive-boot.js', 'utf8');
const {functions} = require('./conversation-source.cjs');
const archive = functions(['_decodeArchiveRowLayouts', '_mergeArchiveListDelta',
  '_decodeArchiveHistoryLayer', 'loadArchiveAll']);
const fullUrl = '/api/conversations/list?window=7d&stale_ok=1';
const rows = [{session_id:'recent', nested:{items:[1]}}, {session_id:'older'}];

function setup({snapshot, response, early} = {}) {
  const reads = [], saves = [], savedDeltas = [], savedLayouts = [];
  const state = {_archiveAllInflight:new Map(), _archiveAllData:new Map(),
    _archiveAllEtag:new Map(), _archiveAllDelta:new Map(), ARCHIVE_ALL_FETCH_TIMEOUT_MS:45000};
  const cache = snapshot ? {url:snapshot.url, read:Promise.resolve(snapshot),
    fetch:early, save(value, delta, packed) {
      saves.push(value); savedDeltas.push(delta); savedLayouts.push(packed);
    }} : undefined;
  const context = {sharedState:state, window:{__cccArchiveSnapshot:cache},
    performance, URLSearchParams, AbortController, setTimeout:()=>0, clearTimeout(){},
    _archiveWindow:()=> '7d', _clientLog(){}, _scheduleArchiveSidebarCache(){},
    backgroundApiFetch:async(url, init) => { reads.push({url, init}); return response; }};
  vm.createContext(context);
  context.window.top = context;
  vm.runInContext(boot, context);
  vm.runInContext(archive, context);
  return {context, state, reads, saves, savedDeltas, savedLayouts};
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
  assert.equal(f.savedDeltas[0].from,'old');
  assert.deepEqual(f.savedDeltas[0].ids,['older','recent','new']);
  assert.deepEqual(f.savedDeltas[0].changes,[{session_id:'recent', modified:42}, {session_id:'new'}]);
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
  assert.equal(f.state._archiveAllDelta.get(fullUrl).token,'fresh');
  assert.equal(f.context.window.__cccArchiveProgress,undefined,'only the renderer marks completeness');
});

test('All history requests reconstruct ordered rows from shared layouts before caching', async () => {
  const allUrl = '/api/conversations/list?window=all&stale_ok=1';
  const body = {row_layouts:[['session_id', 'nested'], ['session_id']],
    conversations:[[0, ['recent', {items:[1]}]], [1, ['older']]], delta_token:'history'};
  for (const early of [false, true]) {
    const f = setup({response:reply(body), snapshot:{url:allUrl},
      early:early ? Promise.resolve(reply(body)) : undefined});
    let validated = 0;
    const loaded = await f.context.loadArchiveAll({window:'all', onValidated:() => validated++});
    assert.equal(JSON.stringify(loaded),JSON.stringify(rows));
    assert.equal(f.state._archiveAllData.get(allUrl),loaded);
    assert.equal(f.state._archiveAllDelta.get(allUrl).token,'history');
    assert.equal(validated,1);
    assert.deepEqual(f.reads.map(read => read.url), early ? [] : [
      allUrl + '&row_layouts=1&order_spans=1&history_layer=1&wide_order_spans=1',
    ]);
    if (!early) assert.equal(f.reads[0].init.headers['X-CCC-Abort-On-Disconnect'], '1');
    assert.deepEqual(Array.from(f.savedLayouts[0]),[body.row_layouts,body.conversations]);
  }
  const invalid = setup({response:reply({row_layouts:[['__proto__']], conversations:[[0, [{}]]]})});
  assert.equal(await invalid.context.loadArchiveAll({window:'all'}),null);
  assert.equal(invalid.state._archiveAllData.size,0);
});

test('the complete-list loader transfers the early packed buffer to the snapshot save', async () => {
  const url = '/api/conversations/list?window=all&stale_ok=1';
  const payload = {
    row_layouts:[['session_id']], conversations:[[0, ['recent']]], delta_token:'current',
  };
  const bytes = new TextEncoder().encode(JSON.stringify(payload)).buffer;
  const response = reply(payload);
  response._cccArchiveParsedBody = Promise.resolve(payload);
  response._cccArchivePackedBytes = bytes;
  const f = setup({snapshot:{url}, early:Promise.resolve(response)});
  const loaded = await f.context.loadArchiveAll({window:'all'});
  assert.equal(loaded[0].session_id,'recent');
  assert.equal(f.savedLayouts[0],bytes);
  assert.equal(response._cccArchivePackedBytes,undefined);
});

test('a history layer validates independently of the saved browser order', async () => {
  const url = '/api/conversations/list?window=all&stale_ok=1';
  const order = 'a'.repeat(64), nextOrder = 'b'.repeat(64);
  const base = {window:'all', row_layouts:[['session_id']], count:2,
    conversations:[[0, ['one']], [0, ['two']]], delta_token:'base', order_token:order};
  const patch = {window:'all', delta:true, since:'base', delta_token:'next', order_token:nextOrder,
    count:3, ids:[], base_order:order, order_spans:[[1,1], 'three', [0,1]],
    row_layouts:[['session_id','name'],['session_id']],
    conversations:[[0, ['one','new']], [1, ['three']]]};
  const response = reply({history_layer:1, base, patch});
  const bytes = new TextEncoder().encode(JSON.stringify({history_layer:1, base, patch})).buffer;
  response._cccArchivePackedBytes = bytes;
  const saved = {url, rows:[{session_id:'unrelated'}], delta:'other'};
  const f = setup({snapshot:saved, response});
  const loaded = await f.context.loadArchiveAll({window:'all'});
  assert.deepEqual(Array.from(loaded, row=>row.session_id), ['two','three','one']);
  assert.equal(loaded[2].name,'new');
  assert.equal(f.state._archiveAllDelta.get(url).token,'next');
  assert.equal(f.savedDeltas[0],null);
  assert.equal(f.savedLayouts[0],bytes);
});

test('a seven-day layer commits its current validator and rejects another window', async () => {
  const base = {window:'7d', count:2, conversations:rows, delta_token:'base', order_token:'a'.repeat(64)};
  const patch = {window:'7d', count:2, delta:true, since:'base', delta_token:'current',
    ids:['older','new'], conversations:[{session_id:'new'}], order_token:'b'.repeat(64)};
  const response = reply({history_layer:1, base, patch});
  const f = setup({response});
  const loaded = await f.context.loadArchiveAll();
  assert.deepEqual(Array.from(loaded, row => row.session_id), ['older','new']);
  assert.equal(f.state._archiveAllDelta.get(fullUrl).token,'current');
  f.context.backgroundApiFetch = async url => { f.reads.push({url}); return reply(null,304); };
  assert.deepEqual(Array.from(await f.context.loadArchiveAll(), row => row.session_id), ['older','new']);
  assert.equal(new URL(f.reads.at(-1).url, 'http://localhost').searchParams.get('since'), 'current');

  const invalid = setup();
  invalid.context.backgroundApiFetch = async url => url.includes('history_layer=1')
    ? reply({history_layer:1, base:{...base, window:'all'}, patch:{...patch, window:'all'}})
    : reply({conversations:rows});
  assert.equal(JSON.stringify(await invalid.context.loadArchiveAll()), JSON.stringify(rows));
});

test('a malformed history layer retries the ordinary request exactly once', async () => {
  const url = '/api/conversations/list?window=all&stale_ok=1';
  const f = setup();
  f.context.backgroundApiFetch = async target => {
    f.reads.push(target);
    if (target.includes('history_layer=1')) {
      return reply({history_layer:1, base:{row_layouts:[['__proto__']], conversations:[[0, [{}]]]}});
    }
    return reply({conversations:rows});
  };
  assert.equal(JSON.stringify(await f.context.loadArchiveAll({window:'all'})),JSON.stringify(rows));
  const ordinary = url + '&row_layouts=1&order_spans=1';
  assert.deepEqual(f.reads,[ordinary + '&history_layer=1&wide_order_spans=1',ordinary]);
});

test('a saved-order delta merges against its validated base and persists the full order', async () => {
  const order = 'a'.repeat(64);
  const snapshot = {url:fullUrl, rows, delta:'old', order};
  const response = reply({
    delta:true, since:'old', ids:[], same_order:true, order_token:order,
    count:rows.length, conversations:[{session_id:rows[0].session_id, modified:42}],
    delta_token:'new',
  });
  response._cccArchiveSavedOrder = order;
  const f = setup({snapshot, early:Promise.resolve(response)});
  const result = await f.context.loadArchiveAll();
  assert.deepEqual(Array.from(result, row => row.session_id), rows.map(row => row.session_id));
  assert.equal(result[0].modified,42);
  assert.equal(f.saves[0].order,order);
  assert.equal(f.savedDeltas[0].ids,null);
  assert.equal(f.reads.length,0);
});

test('same-document polls use the current base order and update it after a reorder', async () => {
  const firstOrder = 'a'.repeat(64);
  const secondOrder = 'b'.repeat(64);
  const f = setup({snapshot:{url:fullUrl},
    response:reply({conversations:rows, delta_token:'first', order_token:firstOrder})});
  await f.context.loadArchiveAll();
  const replies = [
    reply({delta:true, since:'first', ids:['older','recent'], order_token:secondOrder,
      conversations:[], delta_token:'second'}),
    reply({delta:true, since:'second', ids:[], same_order:true, order_token:secondOrder,
      count:2, conversations:[{session_id:'older', modified:42}], delta_token:'third'}),
    reply({conversations:rows, delta_token:'fourth', order_token:firstOrder}),
  ];
  f.context.backgroundApiFetch = async url => {
    f.reads.push({url});
    return replies.shift();
  };
  await f.context.loadArchiveAll();
  assert.equal(f.reads[1].url, fullUrl + '&row_layouts=1&since=first&saved_order=' + firstOrder);
  const result = await f.context.loadArchiveAll();
  assert.equal(f.reads[2].url, fullUrl + '&row_layouts=1&since=second&saved_order=' + secondOrder);
  assert.deepEqual(Array.from(result, row => row.session_id), ['older','recent']);
  assert.equal(result[0].modified, 42);
  assert.equal(f.savedDeltas[2].ids, null);

  f.state._archiveAllData.set(fullUrl, rows.slice());
  await f.context.loadArchiveAll();
  assert.equal(f.reads[3].url, fullUrl + '&row_layouts=1&history_layer=1&wide_order_spans=1', 'a different row base cannot use the old delta token');
});

test('an All order delta reconstructs slices and survives the next saved-order poll', async () => {
  const allUrl = '/api/conversations/list?window=all&stale_ok=1';
  const order = 'a'.repeat(64);
  const nextOrder = 'b'.repeat(64);
  const base = {url:allUrl, rows:[...rows, {session_id:'removed'}], delta:'old', order};
  const changed = {session_id:'new'};
  const updated = {session_id:'recent', modified:42};
  const delta = {
    delta:true, since:'old', ids:[], base_order:order, order_token:nextOrder,
    order_spans:[[1,1], 'new', [0,1]], count:3, delta_token:'new',
    conversations:[changed, updated],
  };
  for (const body of [delta, {...delta, base_order:nextOrder},
    {...delta, order_spans:[[0,2], [0,1]]},
    {...delta, order_spans:[[1,1], 'unknown', [0,1]]},
    {...delta, order_spans:[[1,3]]},
  ]) {
    const early = reply(body);
    early._cccArchiveSavedOrder = order;
    const f = setup({snapshot:base, early:Promise.resolve(early),
      response:reply({conversations:rows})});
    const result = await f.context.loadArchiveAll({window:'all'});
    if (body !== delta) {
      assert.deepEqual(Array.from(result),rows);
      assert.equal(f.reads.length,1,'an invalid patch triggers one full resync');
      assert.equal(f.savedDeltas[0],null);
      continue;
    }
    assert.deepEqual(Array.from(result),[rows[1],changed,updated]);
    assert.deepEqual(Array.from(f.savedDeltas[0].ids),['older','new','recent']);
    assert.equal(f.savedDeltas[0].spans,delta.order_spans);
    assert.equal(f.savedDeltas[0].baseOrder,order);
    assert.equal(f.reads.length,0);
    f.context.backgroundApiFetch = async url => {
      const params = new URL(url,'http://localhost').searchParams;
      assert.equal(params.get('order_spans'),'1');
      assert.equal(params.get('history_layer'),'1');
      assert.equal(params.get('wide_order_spans'),'1');
      assert.equal(params.get('since'),'new');
      assert.equal(params.get('saved_order'),nextOrder);
      return reply(null,304);
    };
    assert.deepEqual(Array.from(await f.context.loadArchiveAll({window:'all'})),
      [rows[1],changed,updated]);
  }
});

test('the All decoder applies a wide saved reorder and rejects an oversized patch', () => {
  const {context} = setup();
  const merge = context.window.CCCArchiveWire._mergeArchiveListDelta;
  const order = 'a'.repeat(64);
  const previous = Array.from({length:4300}, (_, index) => ({session_id:String(index)}));
  const spans = Array.from({length:512}, (_, index) => [2 * index, 1]);
  spans.push(...Array.from({length:512}, (_, index) => [2 * index + 1, 1]), [1024, 3276]);
  const delta = {ids:[], conversations:[], count:previous.length, base_order:order, order_spans:spans};
  const expected = spans.flatMap(([start, count]) => previous.slice(start, start + count));
  assert.deepEqual(Array.from(merge(previous, delta, order)), expected);
  const layered = context.window.CCCArchiveWire._decodeArchiveHistoryLayer({
    history_layer:1,
    base:{window:'all', conversations:previous, count:previous.length, order_token:order, delta_token:'base'},
    patch:{...delta, delta:true, window:'all', order_token:'b'.repeat(64), since:'base', delta_token:'next'},
  });
  assert.deepEqual(Array.from(layered.conversations), expected);
  const oversized = previous.map((_, index) => [index, 1]);
  assert.equal(merge(previous, {...delta, order_spans:oversized}, order), null);
});

test('304 reuses the exact validated rows; an expired server token replaces the full base', async () => {
  const snapshot = {url:fullUrl, rows, delta:'old', etag:'"old"'};
  const same = setup({snapshot, early:Promise.resolve(reply(null,304))});
  assert.deepEqual(Array.from(await same.context.loadArchiveAll()),rows);
  const changed = setup({snapshot, early:Promise.resolve(reply({conversations:[{session_id:'replacement'}],delta_token:'new'}))});
  assert.deepEqual(Array.from(await changed.context.loadArchiveAll(), r=>r.session_id),['replacement']);
  assert.equal(changed.state._archiveAllDelta.get(fullUrl).token,'new');
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
    if (opts.window) assert.equal(f.reads[0].init.headers['X-CCC-Abort-On-Disconnect'], '1');
    else assert.equal(f.reads[0].init.headers, undefined);
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
    assert.equal(f.state._archiveAllDelta.get(fullUrl).token,'new');
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

const allPackedBody = JSON.stringify({
  row_layouts:[['session_id', 'name'], ['session_id']],
  conversations:[[0, ['recent', 'שלום']], [1, ['older']]],
  delta_token:'valid',
}, null, 1);
const sevenDayPackedBody = JSON.stringify({
  history_layer:1,
  base:{window:'7d', count:1, delta_token:'base', order_token:'a'.repeat(64),
    conversations:[{session_id:'older'}]},
  patch:{window:'7d', count:2, delta:true, since:'base', delta_token:'valid',
    order_token:'b'.repeat(64), ids:['recent','older'],
    row_layouts:[['session_id','name']], conversations:[[0,['recent','שלום']]]},
}, null, 1);
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
      if (req.url.includes('row_layouts=1') && req.url.includes('window=all')) {
        res.setHeader('Content-Type','application/json');res.end(allPackedBody);return;
      }
      if (req.headers['x-test-seven-day-layer'] && req.url.includes('history_layer=1')
          && req.url.includes('window=7d')) {
        res.setHeader('Content-Type','application/json');res.end(sevenDayPackedBody);return;
      }
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify({conversations:rows}));return;
    }
    res.setHeader('Content-Type','text/html');res.end('<script src="/archive-boot.js"></script>');
  });
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  origin='http://127.0.0.1:'+server.address().port;
  browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
after(async () => {await browser?.close();await new Promise(r=>server.close(r));});

async function waitForSnapshot(page, delta, key = 'last', windowName = '7d') {
  await page.waitForFunction(async (delta, key, windowName)=>{
    const db=await new Promise(resolve=>{const r=indexedDB.open('ccc-archive-response',1);r.onsuccess=()=>resolve(r.result);});
    try {return await new Promise(resolve=>{const r=db.transaction('snapshot').objectStore('snapshot').get(key);r.onsuccess=()=>resolve(r.result?.delta===delta && !!localStorage.getItem('ccc-archive-response-at:'+windowName));});}
    finally {db.close();}
  },{},delta,key,windowName);
}

test('saved spans retain the original body through a same-order update', async () => {
  const context = await browser.createBrowserContext(), page = await context.newPage();
  try {
    await page.goto(origin);
    await page.evaluate(rows => {
      const snapshot = window.__cccArchiveSnapshot;
      window.__cccArchiveSettled = true;
      snapshot.save({url:snapshot.url, rows, delta:'original', order:'first-order'});
    }, rows);
    await waitForSnapshot(page, 'original');
    await page.evaluate(() => {
      const snapshot = window.__cccArchiveSnapshot;
      const older = {session_id:'older'};
      const added = {session_id:'added', name:'new'};
      snapshot.save({url:snapshot.url, rows:[older,added], delta:'reordered', order:'second-order'},
        {from:'original', ids:['older','added'], spans:[[1,1],'added'],
          baseOrder:'first-order', changes:[added]});
    });
    await waitForSnapshot(page, 'reordered', 'header');
    await page.evaluate(() => {
      const snapshot = window.__cccArchiveSnapshot;
      const older = {session_id:'older', name:'updated'};
      const added = {session_id:'added', name:'new'};
      snapshot.save({url:snapshot.url, rows:[older,added], delta:'valid', order:'second-order'},
        {from:'reordered', ids:null, changes:[older]});
    });
    await waitForSnapshot(page, 'valid', 'header');
    await waitForSnapshot(page, 'original', 'last');
    await page.reload();
    assert.deepEqual(await page.evaluate(async () => {
      const snapshot = window.__cccArchiveSnapshot;
      const saved = await snapshot.read;
      return {rows:saved?.rows, order:saved?.order, status:(await snapshot.fetch)?.status};
    }), {rows:[{session_id:'older',name:'updated'},{session_id:'added',name:'new'}],
      order:'second-order', status:304});
  } finally {await context.close();}
});

test('All reload keeps a compact snapshot when a delta overtakes the first save', {timeout:10000}, async () => {
  const url = '/api/conversations/list?window=all&stale_ok=1';
  const context = await browser.createBrowserContext(), page = await context.newPage();
  try {
    page.setDefaultTimeout(6000);
    await page.evaluateOnNewDocument(() => localStorage.setItem('ccc-archive-window', 'all'));
    const cold = page.waitForRequest(req => req.url().includes('row_layouts=1') && !req.url().includes('since='));
    await page.goto(origin, {waitUntil:'load'});
    assert.equal((await cold).headers()['x-ccc-abort-on-disconnect'], '1');
    const count = 9000;
    const expandedBytes = await page.evaluate(({url, count}) => {
      const repeatedKey = 'field'.repeat(400);
      const rows = Array.from({length:count}, (_, i) => ({session_id:String(i), [repeatedKey]:i}));
      const changed = {...rows[0], name:'שלום'};
      const latest = [...rows.slice(1), changed];
      const helper = window.__cccArchiveSnapshot;
      helper.save({url, rows, delta:'old'}, null,
        [[['session_id', repeatedKey]], rows.map(row => [0, Object.values(row)])]);
      helper.save({url, rows:latest, delta:'valid'},
        {from:'old', ids:latest.map(row => row.session_id), changes:[changed]});
      window.__cccArchiveSettled = true;
      window.dispatchEvent(new Event('ccc:archive-settled'));
      return 2 * JSON.stringify(latest).length;
    }, {url, count});
    assert(expandedBytes > 32 * 1024 * 1024);
    await waitForSnapshot(page, 'valid', 'last', 'all');
    await page.reload({waitUntil:'load'});
    const restored = await page.evaluate(async count => {
      const helper = window.__cccArchiveSnapshot;
      const saved = await helper.read;
      const idsAndValuesMatch = saved?.rows.every((row, i) => {
        const id = (i + 1) % count;
        return row.session_id === String(id) && row['field'.repeat(400)] === id;
      });
      return {count:saved?.rows.length, idsAndValuesMatch,
        name:saved?.rows.at(-1).name, status:(await helper.fetch)?.status};
    }, count);
    assert.deepEqual(restored, {count, idsAndValuesMatch:true, name:'שלום', status:304});
  } finally {await context.close();}
});

for (const large of [false, true]) {
  test(`All reload validates an ordered patch over a packed ${large ? 'Blob' : 'ArrayBuffer'} base`, async () => {
    const allUrl = '/api/conversations/list?window=all&stale_ok=1';
    const context = await browser.createBrowserContext(), page = await context.newPage();
    try {
      await page.evaluateOnNewDocument(() => {
        localStorage.setItem('ccc-archive-window', 'all');
        if (sessionStorage.getItem('fail-snapshot-blob')) {
          Blob.prototype.arrayBuffer = async () => { throw new Error('Unreadable snapshot'); };
        }
      });
      await page.goto(origin, {waitUntil:'load'});
      await page.evaluate(({url, rows, large}) => {
        window.__cccArchiveSettled = true;
        window.__cccArchiveSnapshot.save({url, rows, delta:'old'}, null,
          [[['session_id', 'nested'], ['session_id']], [[0, ['recent', {
            items:[large ? 'é'.repeat(600000) : 1],
          }]], [1, ['older']]]]);
      }, {url:allUrl, rows, large});
      await waitForSnapshot(page, 'old', 'last', 'all');
      const storedAsBlob = await page.evaluate(async () => {
        const db = await new Promise(resolve => {
          const open = indexedDB.open('ccc-archive-response', 1);
          open.onsuccess = () => resolve(open.result);
        });
        try {
          return await new Promise(resolve => {
            const read = db.transaction('snapshot').objectStore('snapshot').get('last');
            read.onsuccess = () => resolve(read.result.packedRows instanceof Blob);
          });
        } finally {db.close();}
      });
      assert.equal(storedAsBlob, large);
      await page.evaluate(url => {
        const changed = {session_id:'recent', name:'שלום'};
        window.__cccArchiveSnapshot.save({url, rows:[{session_id:'older'},changed], delta:'reordered', order:'order'},
          {from:'old', ids:['older','recent'], changes:[changed]});
      }, allUrl);
      await waitForSnapshot(page, 'reordered', 'header', 'all');
      await page.evaluate(url => {
        const changed = {session_id:'older', name:'updated'};
        window.__cccArchiveSnapshot.save({url, rows:[changed,{session_id:'recent',name:'שלום'}],
          delta:'valid', order:'order'}, {from:'reordered', ids:null, changes:[changed]});
      }, allUrl);
      await waitForSnapshot(page, 'valid', 'header', 'all');
      const conditional = page.waitForRequest(req => req.url().includes('since=valid'));
      await page.reload({waitUntil:'load'});
      assert.equal((await conditional).headers()['x-ccc-abort-on-disconnect'],'1');
      const state = await page.evaluate(async () => {
        const h = window.__cccArchiveSnapshot;
        const value = await h.read;
        return {rows:value?.rows, status:(await h.fetch)?.status};
      });
      assert.deepEqual(state,{rows:[{session_id:'older',name:'updated'},{session_id:'recent',name:'שלום'}],status:304});
      if (large) {
        await page.evaluate(() => sessionStorage.setItem('fail-snapshot-blob', '1'));
        await page.reload({waitUntil:'load'});
        assert.deepEqual(await page.evaluate(async () => ({
          saved:await window.__cccArchiveSnapshot.read,
          early:await window.__cccArchiveSnapshot.fetch,
          recent:(await window.__cccArchiveBootstrap.promise)?.status,
        })), {saved:null, early:null, recent:200});
      }
    } finally {await context.close();}
  });
}

for (const [windowName, wireBody] of [['all', allPackedBody], ['7d', sevenDayPackedBody]]) {
  test(`Startup stores the original ${windowName} response bytes and restores them on reload`, async () => {
    const context = await browser.createBrowserContext(), page = await context.newPage();
    try {
      if (windowName === '7d') await page.setExtraHTTPHeaders({'x-test-seven-day-layer':'1'});
      await page.evaluateOnNewDocument(windowName => {
        localStorage.setItem('ccc-archive-window', windowName);
        window.addEventListener('DOMContentLoaded', () => {
          window.__cccArchiveSnapshot.consumed = true;
        }, {once:true});
      }, windowName);
      await page.goto(origin, {waitUntil:'load'});
      const original = await page.evaluate(async () => {
        const snapshot = window.__cccArchiveSnapshot;
        const response = await snapshot.fetch;
        const payload = await response._cccArchiveParsedBody;
        const bytes = response._cccArchivePackedBytes;
        window.__cccArchiveSettled = true;
        const rows = [{session_id:'recent',name:'שלום'}, {session_id:'older'}];
        const delta = payload.history_layer ? payload.patch.delta_token : payload.delta_token;
        snapshot.save({url:snapshot.url, delta, rows}, null, bytes);
        return new TextDecoder().decode(bytes);
      });
      assert.equal(original,wireBody);
      await waitForSnapshot(page, 'valid', 'last', windowName);
      await page.reload({waitUntil:'load'});
      const restored = await page.evaluate(async () => {
        const snapshot = window.__cccArchiveSnapshot;
        const saved = await snapshot.read;
        return {rows:saved?.rows, bytes:saved?.bytes, status:(await snapshot.fetch)?.status};
      });
      assert.deepEqual(restored,{
        rows:[{session_id:'recent',name:'שלום'}, {session_id:'older'}],
        bytes:Buffer.byteLength(wireBody), status:304,
      });
    } finally {await context.close();}
  });
}

async function seed(page,delta) {
  await page.evaluate(({url,rows,delta})=>{
    window.__cccArchiveSettled=true;
    window.__cccArchiveSnapshot.save({url,rows,delta,etag:'"example"'});
  },{url:fullUrl,rows,delta});
  await waitForSnapshot(page, delta);
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

test('All startup opts in to compact layouts and order for the complete response only', async () => {
  const context = await browser.createBrowserContext(), page = await context.newPage();
  try {
    await page.evaluateOnNewDocument(() => localStorage.setItem('ccc-archive-window', 'all'));
    await page.goto(origin, {waitUntil:'load'});
    const urls = await page.evaluate(async () => [
      (await window.__cccArchiveBootstrap.promise).url,
      (await window.__cccArchiveSnapshot.fetch).url,
    ]);
    const [recent, complete] = urls.map(url => new URL(url).searchParams);
    assert.equal(recent.get('window'),'all');
    assert.equal(recent.get('initial'),'1');
    assert.equal(recent.has('row_layouts'),false);
    assert.equal(recent.has('order_spans'),false);
    assert.equal(complete.get('window'),'all');
    assert.equal(complete.get('row_layouts'),'1');
    assert.equal(complete.get('order_spans'),'1');
    assert.equal(complete.has('initial'),false);
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

test('a stale All validator cannot adopt a 304 for a different IndexedDB base', async () => {
  const context = await browser.createBrowserContext(), page = await context.newPage();
  try {
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-archive-window', 'all');
      const fetch = window.fetch;
      window.archiveBootRequests = [];
      window.fetch = (url, init) => {
        window.archiveBootRequests.push({url, signal:init.signal});
        return fetch(url, init);
      };
    });
    await page.goto(origin, {waitUntil:'load'});
    await page.waitForFunction(() => window.archiveBootRequests.some(({url}) => url.includes('history_layer=1')));
    assert.equal(await page.evaluate(() => {
      const request = window.archiveBootRequests.find(({url}) => url.includes('history_layer=1'));
      return new URL(request.url, location.origin).searchParams.get('wide_order_spans');
    }), '1');
    await page.evaluate(rows => {
      const snapshot = window.__cccArchiveSnapshot;
      window.__cccArchiveSettled = true;
      snapshot.save({url:snapshot.url, rows, delta:'newer'});
    }, rows);
    await waitForSnapshot(page, 'newer', 'last', 'all');
    await page.evaluate(() => {
      const key = 'ccc-archive-response-at:all';
      const hint = JSON.parse(localStorage.getItem(key));
      // The server answers 304 for this old token, as it can after a
      // different tab commits a newer IndexedDB snapshot.
      localStorage.setItem(key, JSON.stringify({...hint, delta:'valid'}));
    });
    await page.reload({waitUntil:'load'});
    const result = await page.evaluate(async () => {
      const snapshot = window.__cccArchiveSnapshot;
      const early = await snapshot.fetch;
      const request = window.archiveBootRequests.find(entry => entry.url.includes('since=valid'));
      const recent = await window.__cccArchiveBootstrap.promise;
      return {
        speculative:!!request, cancelled:request?.signal.aborted, adopted:!!early,
        stored:(await snapshot.read)?.delta, recent:(await recent.json()).conversations.length,
      };
    });
    assert.deepEqual(result, {
      speculative:true, cancelled:true, adopted:false, stored:'newer', recent:rows.length,
    });
  } finally {await context.close();}
});

test('a committed All header can validate while the saved rows are still reading', async () => {
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  try {
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-archive-window', 'all');
      if (!sessionStorage.getItem('slow-snapshot-read')) return;
      const get = IDBObjectStore.prototype.get;
      IDBObjectStore.prototype.get = function (key) {
        const request = get.call(this, key);
        if (key === 'last' && this.transaction.mode === 'readonly') {
          Object.defineProperty(request, 'onsuccess', {
            set(handler) {
              request.addEventListener('success', () => {
                setTimeout(() => handler.call(request), 750);
              }, {once:true});
            },
          });
        }
        return request;
      };
    });
    await page.goto(origin, {waitUntil:'load'});
    await page.evaluate(rows => {
      const snapshot = window.__cccArchiveSnapshot;
      window.__cccArchiveSettled = true;
      snapshot.save({url:snapshot.url, rows, delta:'valid'});
    }, rows);
    await waitForSnapshot(page, 'valid', 'last', 'all');
    await page.evaluate(() => sessionStorage.setItem('slow-snapshot-read', '1'));
    await page.reload({waitUntil:'load'});
    assert.deepEqual(await page.evaluate(async () => {
      const snapshot = window.__cccArchiveSnapshot;
      return {rows:(await snapshot.read)?.rows, status:(await snapshot.fetch)?.status};
    }), {rows, status:304});
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

test('the bounded complete boot request opts into disconnect detection', async () => {
  const context = await browser.createBrowserContext(), page = await context.newPage();
  try {
    const request = page.waitForRequest(r => {
      const url = new URL(r.url());
      return url.origin + url.pathname === origin + '/api/conversations/list'
        && url.searchParams.get('window') === '7d'
        && url.searchParams.get('history_layer') === '1'
        && !url.searchParams.has('initial');
    });
    await page.goto(origin, {waitUntil:'domcontentloaded'});
    assert.equal((await request).headers()['x-ccc-abort-on-disconnect'], '1');
  } finally {
    await context.close();
  }
});

test('real IndexedDB retains one scoped snapshot and a reload obtains an actual server 304', async () => {
  const context=await browser.createBrowserContext(), page=await context.newPage();
  try {
    await page.goto(origin,{waitUntil:'load'});
    await page.evaluate(async({url,rows})=>{
      window.__cccArchiveSettled=true;
      window.__cccArchiveSnapshot.save({url,rows,delta:'valid',etag:'"valid"',order:'a'.repeat(64)});
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
      return {count:saved.rows.length,delta:saved.delta,status:response.status,
        order:new URL(response.url).searchParams.get('saved_order')};
    }),{count:2,delta:'valid',status:304,order:'a'.repeat(64)});
    await page.evaluate(()=>localStorage.setItem('ccc-conv-cache','0'));
    await page.reload({waitUntil:'load'});
    assert.equal(await page.evaluate(()=>!!window.__cccArchiveSnapshot),false,'existing cache opt-out applies');
  } finally {await context.close();}
});

test('successive deltas preserve ordering, replacements and removals without rewriting the base', async () => {
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  try {
    await page.goto(origin);
    await seed(page, 'original');
    const changed = {session_id:'recent', nested:{items:[2]}};
    const added = {id:'new'};
    const saveDelta = (from, token, rows, changes) => page.evaluate(({url,rows,from,token,changes})=>{
      window.__cccArchiveSettled = true;
      const ids = rows.map(row => row.session_id || row.id);
      window.__cccArchiveSnapshot.save({url, rows, delta:token}, {from, ids, changes});
    }, {url:fullUrl, rows, from, token, changes});

    await saveDelta('original', 'middle', [rows[1], added, changed], [added, changed]);
    await waitForSnapshot(page, 'middle', 'header');
    await saveDelta('middle', 'valid', [changed, added], []);
    await waitForSnapshot(page, 'valid', 'header');
    await waitForSnapshot(page, 'original');
    await page.reload();
    assert.deepEqual(await page.evaluate(async ()=>{
      const helper = window.__cccArchiveSnapshot;
      return {rows:(await helper.read).rows, status:(await helper.fetch).status};
    }), {rows:[changed, added], status:304});

    // A different tab's commit (or a skipped idle save) breaks the chain.
    await saveDelta('missing-predecessor', 'replacement', [added], []);
    await waitForSnapshot(page, 'replacement');
    await page.reload();
    assert.deepEqual(await page.evaluate(async ()=>(await window.__cccArchiveSnapshot.read).rows), [added]);
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
