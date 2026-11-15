const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
function fn(name) {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }\n', start) + 5);
}
const sid = '11111111-2222-4333-8444-555555555555';
const flush = () => new Promise(resolve => setImmediate(resolve));

test('Claude landing replaces the placeholder identity before an archive refresh that never resolves', async () => {
  const card = {id:'spawning-123',session_id:'spawning-123',expected_session_id:sid,pending_spawn:true};
  const timers = [], binds = [];
  const ctx = vm.createContext({
    Date, Map, Array, Object, String, pendingSpawns:new Map([['123',card]]), conversationsData:[card],
    _resolvedPlaceholderTargets:new Map(), currentConversation:card.id,
    splitState:{panes:[{id:'primary',conversationId:card.id}]}, activePaneId:()=>'primary',
    setTimeout:(fn,ms)=>timers.push({fn,ms}),
    fetch:async()=>({ok:true,json:async()=>({landed:true})}),
    refreshArchiveData:()=>new Promise(()=>{}), renderArchiveList(){},
    rebindCurrentSelectionToRealCard:real=>{binds.push(real.id);ctx.currentConversation=real.id;},
    $convSearch:{value:''},
  });
  vm.runInContext(['_pendingSpawnStillWaiting','_watchPendingSpawnRegistration',
    'resolvePlaceholderPanes','resolvedSpawnConversationId','spawningSelectionStillPending'].map(fn).join('\n'),ctx);
  ctx._watchPendingSpawnRegistration('123',card.id);
  timers.shift().fn();
  await flush();
  assert.equal(card.id,sid);
  assert.equal(card.session_id,sid);
  assert.equal(card.pending_spawn,false);
  assert.deepEqual(binds,[sid]);
  assert.equal(ctx.pendingSpawns.has('123'),true,'archive chase still owns the bookkeeping entry');
  assert.equal(ctx.resolvedSpawnConversationId('spawning-123'),sid);
  assert.equal(ctx.spawningSelectionStillPending('spawning-123'),false,'landing must release the global poll pause');
  card.spawn_failed=true;
  card.id='spawning-123';
  assert.equal(ctx.resolvedSpawnConversationId(card.id),card.id,'a failed card must retain its recovery controls');
  card.spawn_failed=false;
  card._landedResolved=false;
  card.pending_spawn=true;
  ctx._resolvedPlaceholderTargets.clear();
  assert.equal(ctx.resolvedSpawnConversationId(card.id),card.id,'a preassigned UUID is not proof of landing');
  assert.equal(ctx.spawningSelectionStillPending(card.id),true);
});

test('reopening a landed placeholder renders the saved conversation while it remains in the pending map', async () => {
  const marker='\n})();\n\n/* === Hero: Fleet Pulse === */';
  const exposed=app.replace(marker, `
    window.handoffTest={selectConversation,activePaneId,getConvViewForPane,paneByPaneId,
      seed(){const c={id:'spawning-123',session_id:'${sid}',expected_session_id:'${sid}',source:'interactive',
        display_name:'Resume test',pending_spawn:false,_landedResolved:true,first_message:'Inspect this report'};
        conversationsData=[c];pendingSpawns.set('123',c);_resolvedPlaceholderTargets.set(c.id,'${sid}');}};
  `+marker);
  const requests=[];
  const browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page=await browser.newPage();
    await page.setViewport({width:1280,height:850});
    await page.setRequestInterception(true);
    page.on('request',r=>{
      const url=new URL(r.url());
      if(r.isNavigationRequest())return r.respond({status:200,contentType:'text/html',body:fs.readFileSync(require.resolve('../static/index.html'),'utf8')});
      if(url.pathname==='/static/app.js')return r.respond({status:200,contentType:'application/javascript',body:exposed});
      if(url.pathname==='/static/app.css')return r.respond({status:200,contentType:'text/css',body:fs.readFileSync(require.resolve('../static/app.css'),'utf8')});
      if(url.pathname.startsWith('/api/conversations/'))requests.push(url.pathname);
      if(url.pathname==='/api/conversations/'+sid)return r.respond({status:200,contentType:'application/json',body:JSON.stringify({
        engine:'claude',last_line:12,first_line:1,truncated_before:false,events:[
          {type:'user_text',line:1,text:'Inspect this report'},
          {type:'assistant',line:12,blocks:[{kind:'text',text:'The saved reply is still here.'}]}]})});
      return r.respond({status:200,contentType:'application/json',body:'[]'});
    });
    await page.goto('http://ccc-handoff.test/',{waitUntil:'load'});
    await page.waitForFunction(()=>!!window.handoffTest);
    await page.evaluate(async()=>{handoffTest.seed();await handoffTest.selectConversation('spawning-123');});
    await page.waitForFunction(()=>document.querySelector('.assistant-text')?.textContent.includes('saved reply'));
    assert.equal(await page.evaluate(()=>handoffTest.paneByPaneId(handoffTest.activePaneId()).conversationId),sid);
    assert.ok(requests.some(url=>url==='/api/conversations/'+sid));
    assert.ok(requests.every(url=>!url.includes('spawning-')));
    assert.equal(await page.$('.optimistic-echo-note'),null);
    const reply=await page.$('.assistant-text');
    await reply.scrollIntoView();
    assert.equal(await reply.evaluate(el=>{const r=el.getBoundingClientRect();return el.checkVisibility()&&r.width>0&&r.top>=0&&r.bottom<=innerHeight;}),true);
    if(process.env.CCC_HANDOFF_SCREENSHOT)await page.screenshot({path:process.env.CCC_HANDOFF_SCREENSHOT});
  } finally {await browser.close();}
});
