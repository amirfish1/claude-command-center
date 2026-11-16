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
const fixture=`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/queue-policy.css"><style>body{display:block;overflow:auto;padding:16px}button{max-width:100%}.upd-overlay{z-index:100}.settings-routing-table small{display:block}#settingsQueueTable{max-width:100%}</style></head><body><div id="health"><button id="queueChip" data-fq-plan-queue="DEMO">DEMO · next worker</button><button id="queueConfig" data-fq-config-queue="DEMO">Configure DEMO</button><button id="manualChip" data-fq-plan-queue="MANUAL">MANUAL · next worker</button></div><div id="settingsQueuePreviewBody" hidden></div><div id="settingsQueueTable"></div><script src="/static/settings-launch-preview.js"></script><script src="/static/queue-policy.js"></script><script>
const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),escapeAttr=escapeHtml;
const MODEL_OPTIONS_BY_ENGINE={claude:[{id:'claude-sonnet-4-6',label:'Sonnet'}],codex:[{id:'gpt-5-codex',label:'Codex'}]},ENGINE_SUPPORTS_CUSTOM_MODEL={},sessionCostTier=()=>'',_normalizeModelId=v=>v,spawnEngineLabel=v=>v;
const spawnDefaultsState={blockedModels:new Set()},_uxqHealthCache={},_uxqItemsCache={};
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
const loaded=()=>{const n=document.querySelector('.queue-policy-now');return !!n&&!/Reading/.test(n.textContent)&&!document.querySelector('#fqConfigEngine').disabled;};
async function open(t,width=1440,trigger='#queueChip'){
 fs.writeFileSync(file,original);fs.writeFileSync(state,defaults);fs.rmSync(failFlag,{force:true});
 const ctx=await browser.createBrowserContext(),page=await ctx.newPage(),errors=[],posts=[];page.setDefaultTimeout(8000);await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
 page.on('request',req=>{if(req.method()==='POST')posts.push({url:req.url(),body:req.postData()?JSON.parse(req.postData()):null});return req.url().startsWith(url)?req.continue():req.abort();});
 t.after(async()=>{await ctx.close();assert.deepEqual(errors,[]);assert(posts.every(p=>p.url===url+'/api/queue/config-options'||(p.url===url+'/api/queue/config'&&['read','preview','apply'].includes(p.body.policy_action))));assert.equal(fs.readFileSync(state,'utf8'),defaults);});
 await page.goto(url,{waitUntil:'load'});await page.focus(trigger);await page.keyboard.press('Enter');await page.waitForFunction(loaded);return{page,posts};
}
const text=page=>page.$eval('.queue-policy-status',n=>n.textContent);
const read=()=>JSON.parse(fs.readFileSync(file,'utf8'));
async function save(page){await page.click('[data-fq-config-save]');await page.waitForFunction(()=>/Saved:|Not saved|Nothing changed|could not be confirmed/.test(document.querySelector('.queue-policy-status').textContent));}
test('queue chip opens a short editor: current route, pickers, one Save, no writes until Save',async t=>{
 const{page,posts}=await open(t);assert.equal(fs.readFileSync(file,'utf8'),original);assert.equal(await page.$eval('#fqConfigTitle',n=>n.textContent),'DEMO · worker settings');
 assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));
 const now=await page.$eval('.queue-policy-saved',n=>n.textContent);assert.match(now,/claude · claude-sonnet-4-6 · high/);assert.match(now,/Set on this queue: engine, model, effort/);
 assert.equal(await page.$eval('#fqConfigPath',n=>!!n.getClientRects().length),false);assert.equal(await page.$eval('.fq-config-roles',n=>!!n.getClientRects().length),false);
 // The noisy layers stay out of this dialog.
 for(const sel of ['details.choice-eligibility','.settings-scope-container','.settings-launch-details'])assert.equal(await page.$$eval('.queue-policy-composer '+sel,ns=>ns.some(n=>n.getClientRects().length)),false,sel);
 assert.equal(await page.$$eval('.queue-policy-composer .upd-actions button',ns=>ns.map(n=>n.textContent).join('|')),'Other queue settings|Cancel|Save');
 await page.screenshot({path:path.join(scratch,'desktop-saved.png'),fullPage:true});
 await page.select('#fqConfigEffort','low');assert.equal(fs.readFileSync(file,'utf8'),original);assert.equal(await page.$eval('[data-fq-config-save]',n=>n.disabled),false);assert(posts.every(p=>p.body?.policy_action!=='apply'));
});
test('Default in More options clears only that exception and saves in one click',async t=>{
 const{page}=await open(t,1440,'#queueConfig');assert(await page.$eval('.queue-policy-more',n=>n.open));await page.select('#fqConfigFallback','');assert.equal(fs.readFileSync(file,'utf8'),original);await save(page);
 assert.match(await text(page),/Saved: Fallback off → default/);
 const expected=JSON.parse(original);delete expected.DEMO.fallback_to_default_worker;assert.deepEqual(read(),expected);assert.deepEqual(await page.evaluate(()=>window.__events),[{queue:'DEMO'}]);
});
test('phone keyboard engine/model/effort change saves together and fits the screen',async t=>{
 const{page}=await open(t,390);await page.focus('#fqEngineSeg [data-engine="codex"]');await page.keyboard.press('Enter');
 await page.select('#fqConfigModel','gpt-5-codex');await page.select('#fqConfigEffort','medium');
 await page.screenshot({path:path.join(scratch,'phone-edit.png'),fullPage:true});
 const bounds=await page.$eval('.fq-config-dialog',n=>{const r=n.getBoundingClientRect();return{left:r.left,right:r.right,width:innerWidth,scroll:n.scrollWidth,client:n.clientWidth};});assert(bounds.left>=0&&bounds.right<=bounds.width);assert(bounds.scroll<=bounds.client+1);
 await page.focus('[data-fq-config-save]');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('.queue-policy-status').textContent.includes('Saved:'));
 const expected=JSON.parse(original);Object.assign(expected.DEMO,{engine:'codex',model:'gpt-5-codex',effort:'medium'});assert.deepEqual(read(),expected);
 assert.match(await page.$eval('.queue-policy-now',n=>n.textContent),/codex · gpt-5-codex · medium/);
 await page.keyboard.press('Escape');await page.waitForSelector('.fq-config-composer',{hidden:true});assert.equal(await page.evaluate(()=>document.activeElement.id),'queueChip');
});
test('a queue edited elsewhere is not overwritten; the editor reloads the latest',async t=>{
 const{page}=await open(t,390);await page.select('#fqConfigEffort','low');const cfg=read();cfg.DEMO.effort='max';fs.writeFileSync(file,JSON.stringify(cfg));const before=fs.readFileSync(file,'utf8');
 // Revision check happens at preview; force staleness between preview and apply too.
 await save(page);assert.match(await text(page),/changed elsewhere|Not saved/);assert.equal(fs.readFileSync(file,'utf8'),before);assert.deepEqual(await page.evaluate(()=>window.__events),[]);assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));
});
test('publication failure stays visible and a retry saves',async t=>{
 const{page}=await open(t);await page.select('#fqConfigHeadroom','');fs.writeFileSync(failFlag,'1');await save(page);
 assert.match(await text(page),/simulated queue save failure/);assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(await page.evaluate(()=>window.__events),[]);fs.rmSync(failFlag);await save(page);assert.match(await text(page),/Saved:/);assert(!Object.hasOwn(read().DEMO,'headroom_dispatch'));
});
test('manual queue shows inherited defaults and saves an explicit Off; no-op stays unsaved',async t=>{
 const{page}=await open(t,390,'#manualChip');assert.match(await page.$eval('.queue-policy-saved',n=>n.textContent),/codex/);assert.match(await page.$eval('.queue-policy-saved',n=>n.textContent),/Using the worker defaults/);
 await page.select('#fqConfigModel','');assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));
 await page.select('#fqConfigHeadroom','off');await save(page);assert.equal(read().MANUAL.headroom_dispatch,false);assert.deepEqual(read().DEMO,JSON.parse(original).DEMO);
});
test('Escape restores opener and Tab stays in dialog',async t=>{
 const{page}=await open(t);
 await page.$eval('.upd-actions',n=>Array.from(n.querySelectorAll('button')).filter(b=>!b.disabled).at(-1).focus());await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Close');await page.keyboard.down('Shift');await page.keyboard.press('Tab');await page.keyboard.up('Shift');assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Cancel');
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'queueChip');
});
test('cross-queue report retains bulk scope and shows manual/fallback/headroom exceptions',async t=>{
 const{page}=await open(t);await page.keyboard.press('Escape');await page.evaluate(()=>window.__report());const reportText=await page.$eval('#settingsQueueTable',n=>n.textContent);assert.match(reportText,/2 active queues/);assert.match(reportText,/MANUAL · manual/);assert.match(reportText,/Fallback: Off · override/);assert.match(reportText,/Headroom: Off · override/);assert.equal(await page.$$eval('[data-queue-policy]',n=>n.length),2);
 await page.focus('[data-queue-policy=MANUAL]');await page.keyboard.press('Enter');await page.waitForFunction(loaded);assert.match(await page.$eval('#fqConfigTitle',n=>n.textContent),/MANUAL/);
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.matches('[data-queue-policy=MANUAL]')),true);assert.equal(await page.evaluate(()=>window.__underlyingEscapes),0);
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>window.__underlyingEscapes),1);
 assert.match(app,/Clear engine, model and effort pins for:/);assert.match(app,/fallback rules and running workers stay as they are/);
});

test('other queue settings opens the full editor without writes',async t=>{
 const{page}=await open(t);await page.evaluate(()=>Array.from(document.querySelectorAll('.upd-actions button')).find(b=>b.textContent==='Other queue settings').click());await page.waitForFunction(()=>document.querySelector('#fqConfigTitle')?.textContent==='Queue configuration');
 assert.equal(await page.$$eval('#fqConfigEngine',n=>n.length),1);assert.equal(await page.$eval('#fqConfigPath',n=>!!n.getClientRects().length),true);assert.equal(await page.$eval('[data-fq-config-save]',n=>n.textContent),'Save queue');assert.equal(fs.readFileSync(file,'utf8'),original);await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'queueChip');
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

test('blocked custom model is rejected before any write with a visible error',async t=>{
 const{page}=await open(t,390);await page.select('#fqConfigModel','__custom__');await page.type('#fqConfigCustomModel','blocked');await save(page);assert.match(await text(page),/Not saved: Model blocked by policy/);assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(await page.evaluate(()=>window.__events),[]);
});

test('lost apply response never claims success',async t=>{
 const{page}=await open(t);await page.select('#fqConfigModel','');
 await page.evaluate(()=>{const native=window.fetch;window.fetch=async(url,init)=>{if(init?.body&&JSON.parse(init.body).policy_action==='apply')throw new TypeError('Failed to fetch');return native(url,init);};});
 await save(page);assert.match(await text(page),/could not be confirmed/);assert.deepEqual(await page.evaluate(()=>window.__events),[]);assert.equal(fs.readFileSync(file,'utf8'),original);
});

test('Default engine removes the engine pin and the model/effort pins tied to it',async t=>{
 const{page}=await open(t);await page.click('#fqEngineSeg [data-engine=""]');await save(page);assert.match(await text(page),/Saved: .*Engine claude → default/);
 const expected=JSON.parse(original);delete expected.DEMO.engine;delete expected.DEMO.model;delete expected.DEMO.effort;assert.deepEqual(read(),expected);
});
