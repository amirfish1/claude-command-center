'use strict';
// Native queue-manager markup/listeners, shared renderer and restricted real API.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'queue-policy-'));
const app=require('./legacy-dashboard-source.cjs');
function between(a,b){const start=app.indexOf(a),end=app.indexOf(b,start+a.length);assert(start>=0&&end>start,a);return app.slice(start,end);}
const manager=between('  async function openQueueManager(', '  // Routes a ticket');
const efforts=between('  const CODEX_REASONING_LEVELS =','  function _normalizeModelId(');
const shortcut=between('  async function _openFqPlanPopover(', '  function _uxqRepaintStatusStrip()');
const nativeOpeners=between("      $health.addEventListener('click', (ev) => {\n        const btn = ev.target && ev.target.closest && ev.target.closest('[data-fq-plan-queue]');", "      $health.addEventListener('click', scopeFromRow);");
const report=between('  async function refreshSettingsQueues()', "  document.getElementById('settingsUsageRefresh')");
const reportOpener=between("  document.getElementById('settingsQueueTable')?.addEventListener", "  document.getElementById('settingsQueueScope')");
const fixture=`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><style>body{display:block;overflow:auto;padding:16px}button{max-width:100%}.upd-overlay{z-index:100}.settings-routing-table small{display:block}#settingsQueueTable{max-width:100%}</style></head><body><div id="health"><button id="queueChip" data-fq-plan-queue="DEMO">DEMO · next worker</button><button id="queueConfig" data-fq-config-queue="DEMO">Configure DEMO</button><button id="manualChip" data-fq-plan-queue="MANUAL">MANUAL · next worker</button></div><div id="settingsQueuePreviewBody" hidden></div><div id="settingsQueueTable"></div><script src="/static/settings-launch-preview.js"></script><script>
const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),escapeAttr=escapeHtml;
const MODEL_OPTIONS_BY_ENGINE={claude:[{id:'claude-sonnet-4-6',label:'Sonnet'}],codex:[{id:'gpt-5-codex',label:'Codex'}]},ENGINE_SUPPORTS_CUSTOM_MODEL={},sessionCostTier=()=>'',_normalizeModelId=v=>v,spawnEngineLabel=v=>v;
const spawnDefaultsState={blockedModels:new Set(),worker_engine:'codex',worker_model:'gpt-5-codex',worker_reasoning_effort:'high'},_uxqHealthCache={},_uxqItemsCache={};
const ENGINE_HUB_WORKER_ENGINES=['claude','codex'],MODEL_PICKER_ENGINE_GLYPHS={claude:'C',codex:'X'};
const getTopSpawnPicks=()=>[{engine:'codex',model:'gpt-5-codex',effort:'medium'}];
const getModelPillLabel=(e,m,f)=>(m||e+' default')+(f?' · '+f:'');
window.__underlyingEscapes=0;document.addEventListener('keydown',e=>{if(e.key==='Escape')window.__underlyingEscapes++;});
window.__toasts=[];const showOpToast=(...v)=>window.__toasts.push(v);window.__saved=0;function _renderQueuePanel(){window.__saved++;}
${efforts}
${manager}
${shortcut}
window.openQueueManager=openQueueManager;const $health=document.getElementById('health');
${nativeOpeners}
let settingsQueueBusy=false,settingsQueueSnapshot;async function settingsJSON(url){const r=await fetch(url);return r.json();}
${report}
${reportOpener}
window.__report=refreshSettingsQueues;
window.__events=[];window.addEventListener('ccc:queue-policy-changed',e=>window.__events.push(e.detail));
</script></body></html>`;
let child,browser,url,original,defaults,consumerAvailable;const file=path.join(scratch,'queue-config.json'),state=path.join(scratch,'spawn-defaults.json'),failFlag=path.join(scratch,'fail-write');
test.before(async()=>{
 fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);child=spawn('python3',['-u','tests/fixtures/queue_policy_server.py',scratch],{cwd:root,env:{...process.env,PYTHONPATH:root,CCC_EPHEMERAL:'1'}});let stderr='';child.stderr.on('data',d=>stderr+=d);
 const lines=readline.createInterface({input:child.stdout});const host=await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw new Error(stderr);})]);url=host.url;consumerAvailable=host.consumer_available;
 original=fs.readFileSync(file,'utf8');defaults=fs.readFileSync(state,'utf8');browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Queue-policy screenshots:',scratch);});
const loaded=()=>{const m=document.querySelector('.fq-config-composer');return !!m&&!!(m.querySelector('#fqRunsOn')||{textContent:''}).textContent&&m.querySelectorAll('#fqWorkerSeg .orch-tier-chip').length>=3;};
async function open(t,width=1440,trigger='#queueChip'){
 fs.writeFileSync(file,original);fs.writeFileSync(state,defaults);fs.rmSync(failFlag,{force:true});
 const ctx=await browser.createBrowserContext(),page=await ctx.newPage(),errors=[],posts=[];page.setDefaultTimeout(8000);await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
 page.on('request',req=>{if(req.method()==='POST')posts.push({url:req.url(),body:req.postData()?JSON.parse(req.postData()):null});return req.url().startsWith(url)?req.continue():req.abort();});
 t.after(async()=>{await ctx.close();assert.deepEqual(errors,[]);assert(posts.every(p=>p.url===url+'/api/queue/config-options'||(p.url===url+'/api/queue/config'&&!(p.body&&p.body.policy_action))));assert.equal(fs.readFileSync(state,'utf8'),defaults);});
 await page.goto(url,{waitUntil:'load'});await page.focus(trigger);await page.keyboard.press('Enter');await page.waitForFunction(loaded);
 // Initial focus lands in a requestAnimationFrame; let it fire first or it
 // steals focus a test already moved (seen on slow CI runners, CCC-716).
 await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));return{page,posts};
}
const read=()=>JSON.parse(fs.readFileSync(file,'utf8'));
async function save(page){
 const before=await page.evaluate(()=>window.__toasts.length);
 await page.click('[data-fq-config-save]');
 await page.waitForFunction(n=>!document.querySelector('.fq-config-composer')||window.__toasts.length>n,{},before);
 return page.evaluate(()=>({closed:!document.querySelector('.fq-config-composer'),toast:(window.__toasts.at(-1)||[''])[0]}));
}
test('queue chip opens one settings dialog: runs-on line, combo chips, every section, no writes',async t=>{
 const{page,posts}=await open(t);assert.equal(fs.readFileSync(file,'utf8'),original);
 assert.equal(await page.$eval('#fqConfigTitle',n=>n.textContent),'DEMO · queue settings');
 assert.match(await page.$eval('#fqRunsOn',n=>n.textContent),/Runs on claude · claude-sonnet-4-6 · high/);
 assert.match(await page.$eval('#fqRunsOnNote',n=>n.textContent),/Set on this queue: engine, model, effort/);
 // The whole queue form in one place — no nested worker-settings page.
 assert.equal(await page.$eval('#fqConfigPath',n=>!!n.getClientRects().length),true);
 assert.equal(await page.$eval('#fqConfigFallback',n=>n.value),'off');
 assert.equal(await page.$eval('#fqConfigHeadroom',n=>n.value),'off');
 assert.equal(await page.$$eval('.fq-config-composer .upd-actions button',ns=>ns.map(n=>n.textContent).join('|')),'Cancel|Save queue');
 const chips=await page.$$eval('#fqWorkerSeg .orch-tier-chip',ns=>ns.map(n=>({text:n.textContent,on:n.classList.contains('is-selected')})));
 assert.equal(chips.filter(c=>c.on).length,1);assert.match(chips.find(c=>c.on).text,/claude-sonnet-4-6/);
 assert.equal(chips.filter(c=>/Worker default|Custom/.test(c.text)).length,2);
 assert(posts.every(p=>p.url===url+'/api/queue/config-options'));
 await page.screenshot({path:path.join(scratch,'desktop-saved.png'),fullPage:true});
});
test('combo chip sets engine, model and effort; Save writes the full queue config',async t=>{
 const{page}=await open(t,390);
 await page.click('#fqWorkerSeg .orch-tier-chip[data-engine="codex"]');
 assert.equal(await page.$eval('#fqConfigEngine',n=>n.value),'codex');
 assert.equal(await page.$eval('#fqConfigModel',n=>n.value),'gpt-5-codex');
 assert.equal(await page.$eval('#fqConfigEffort',n=>n.value),'medium');
 assert.match(await page.$eval('#fqRunsOn',n=>n.textContent),/Runs on codex · gpt-5-codex · medium/);
 await page.screenshot({path:path.join(scratch,'phone-edit.png'),fullPage:true});
 const bounds=await page.$eval('.fq-config-dialog',n=>{const r=n.getBoundingClientRect();return{left:r.left,right:r.right,width:innerWidth,scroll:n.scrollWidth,client:n.clientWidth};});assert(bounds.left>=0&&bounds.right<=bounds.width);assert(bounds.scroll<=bounds.client+1);
 const result=await save(page);assert(result.closed);assert.match(result.toast,/Saved DEMO queue configuration/);
 const expected=JSON.parse(original);Object.assign(expected.DEMO,{engine:'codex',model:'gpt-5-codex',effort:'medium',backend:'file',product_gate:false});
 assert.deepEqual(read(),expected);assert.deepEqual(await page.evaluate(()=>window.__events),[{queue:'DEMO'}]);
});
test('Worker default chip clears the engine, model and effort pins in one Save',async t=>{
 const{page}=await open(t);
 await page.click('#fqWorkerSeg .orch-tier-chip:not([data-engine]):not([data-fq-custom])');
 assert.equal(await page.$eval('#fqConfigEngine',n=>n.value),'');assert.equal(await page.$eval('#fqConfigModel',n=>n.value),'');assert.equal(await page.$eval('#fqConfigEffort',n=>n.value),'');
 assert.match(await page.$eval('#fqRunsOnNote',n=>n.textContent),/Follows the shared worker defaults/);
 const result=await save(page);assert(result.closed);
 const expected=JSON.parse(original);delete expected.DEMO.engine;delete expected.DEMO.model;delete expected.DEMO.effort;Object.assign(expected.DEMO,{backend:'file',product_gate:false});
 assert.deepEqual(read(),expected);
});
test('fallback and headroom selects save through the same form',async t=>{
 const{page}=await open(t,1440,'#queueConfig');await page.select('#fqConfigFallback','');await page.select('#fqConfigHeadroom','');assert.equal(fs.readFileSync(file,'utf8'),original);
 const result=await save(page);assert(result.closed);
 const expected=JSON.parse(original);delete expected.DEMO.fallback_to_default_worker;delete expected.DEMO.headroom_dispatch;Object.assign(expected.DEMO,{backend:'file',product_gate:false});
 assert.deepEqual(read(),expected);
});
test('Custom… opens the manual pickers; a blocked model is rejected with a visible error',async t=>{
 const{page}=await open(t,390);await page.click('#fqWorkerSeg [data-fq-custom]');
 assert.equal(await page.$eval('#fqCustomDetails',n=>n.open),true);
 await page.select('#fqConfigModel','__custom__');assert.equal(await page.$eval('#fqConfigCustomModel',n=>n.hidden),false);
 await page.type('#fqConfigCustomModel','blocked');
 const result=await save(page);assert.equal(result.closed,false);assert.match(result.toast,/Model blocked by policy/);
 assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(await page.evaluate(()=>window.__events),[]);
});
test('queues changed elsewhere survive a merged-form save',async t=>{
 const{page}=await open(t);const cfg=read();cfg.EXTRA={auto_drain:false};fs.writeFileSync(file,JSON.stringify(cfg));
 const result=await save(page);assert(result.closed);
 const after=read();assert.deepEqual(after.EXTRA,{auto_drain:false});assert.equal(after.DEMO.engine,'claude');
});
test('publication failure stays visible and a retry saves',async t=>{
 const{page}=await open(t);await page.select('#fqConfigHeadroom','');fs.writeFileSync(failFlag,'1');
 let result=await save(page);assert.equal(result.closed,false);assert.match(result.toast,/simulated queue save failure/);
 assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(await page.evaluate(()=>window.__events),[]);
 fs.rmSync(failFlag);result=await save(page);assert(result.closed);assert.match(result.toast,/Saved DEMO/);assert(!Object.hasOwn(read().DEMO,'headroom_dispatch'));
});
test('manual queue inherits the shared worker defaults on the Worker default chip',async t=>{
 const{page}=await open(t,390,'#manualChip');
 assert.match(await page.$eval('#fqRunsOn',n=>n.textContent),/Runs on codex · gpt-5-codex · high/);
 assert.match(await page.$eval('#fqRunsOnNote',n=>n.textContent),/Follows the shared worker defaults/);
 const chips=await page.$$eval('#fqWorkerSeg .orch-tier-chip',ns=>ns.map(n=>({on:n.classList.contains('is-selected'),engine:n.dataset.engine||null})));
 assert.equal(chips.find(c=>c.on).engine,null);
 await page.select('#fqConfigHeadroom','off');const result=await save(page);assert(result.closed);
 assert.equal(read().MANUAL.headroom_dispatch,false);assert.deepEqual(read().DEMO,JSON.parse(original).DEMO);
});
test('a lost save response never claims success',async t=>{
 const{page}=await open(t);await page.select('#fqConfigFallback','');
 await page.evaluate(()=>{const native=window.fetch;window.fetch=async(u,init)=>{if(init&&init.method==='POST'&&String(u).endsWith('/api/queue/config'))throw new TypeError('Failed to fetch');return native(u,init);};});
 const result=await save(page);assert.equal(result.closed,false);assert.match(result.toast,/Could not save queue/);
 assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(await page.evaluate(()=>window.__events),[]);
});
test('Escape restores opener and Tab stays in dialog',async t=>{
 const{page}=await open(t);
 await page.$eval('.upd-actions',n=>Array.from(n.querySelectorAll('button')).filter(b=>!b.disabled).at(-1).focus());await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Close');await page.keyboard.down('Shift');await page.keyboard.press('Tab');await page.keyboard.up('Shift');assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Save queue');
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'queueChip');
});
test('cross-queue report retains bulk scope and shows manual/fallback/headroom exceptions',async t=>{
 const{page}=await open(t);await page.keyboard.press('Escape');await page.evaluate(()=>window.__report());const reportText=await page.$eval('#settingsQueueTable',n=>n.textContent);assert.match(reportText,/2 active queues/);assert.match(reportText,/MANUAL · manual/);assert.match(reportText,/Fallback: Off · override/);assert.match(reportText,/Headroom: Off · override/);assert.equal(await page.$$eval('[data-queue-policy]',n=>n.length),2);
 await page.focus('[data-queue-policy=MANUAL]');await page.keyboard.press('Enter');await page.waitForFunction(loaded);assert.match(await page.$eval('#fqConfigTitle',n=>n.textContent),/MANUAL · queue settings/);
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.matches('[data-queue-policy=MANUAL]')),true);assert.equal(await page.evaluate(()=>window.__underlyingEscapes),0);
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>window.__underlyingEscapes),1);
 // The merged dialog keeps the worker route in the same form — no nested editor.
 assert.match(app,/Run new workers on/);assert.match(app,/Follows the shared worker defaults/);assert.doesNotMatch(app,/Other queue settings/);
 assert.equal(fs.existsSync(path.join(root,'static/queue-policy.js')),false);assert.doesNotMatch(fs.readFileSync(path.join(root,'static/index.html'),'utf8'),/queue-policy/);
});
test('queue trigger replacement during loading or editing restores the matching control',async t=>{
 const{page}=await open(t);await page.keyboard.press('Escape');
 for(const trigger of ['#queueChip','#queueConfig']) for(const duringLoad of [true,false]) {
  await page.evaluate(()=>{const native=window.fetch;window.fetch=async(...args)=>{if(args[0]==='/api/queue/config-options'){window.__optionsWaiting=true;await new Promise(resolve=>window.__releaseOptions=resolve);}return native(...args);};});
  await page.focus(trigger);await page.keyboard.press('Enter');await page.waitForFunction(()=>window.__optionsWaiting);
  const replace=()=>page.$eval(trigger,n=>{const replacement=n.cloneNode(true);replacement.removeAttribute('id');replacement.dataset.replacement='true';n.replaceWith(replacement);});
  if(duringLoad)await replace();
  await page.evaluate(()=>{window.__releaseOptions();window.__optionsWaiting=false;});
  await page.waitForFunction(loaded);
  if(!duringLoad)await replace();
  await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.dataset.replacement),'true');
  await page.evaluate(id=>{document.activeElement.id=id;},trigger.slice(1));
  // Reload removes the temporary fetch gate before the next independent case.
  await page.reload({waitUntil:'load'});
 }
});
