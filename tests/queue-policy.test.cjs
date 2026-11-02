'use strict';
// Native queue-manager markup/listeners, shared renderer and restricted real API.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'queue-policy-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8');
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
async function open(t,width=1440,trigger='#queueChip'){
 fs.writeFileSync(file,original);fs.writeFileSync(state,defaults);fs.rmSync(failFlag,{force:true});
 const ctx=await browser.createBrowserContext(),page=await ctx.newPage(),errors=[],posts=[];page.setDefaultTimeout(8000);await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
 page.on('request',req=>{if(req.method()==='POST')posts.push({url:req.url(),body:req.postData()?JSON.parse(req.postData()):null});return req.url().startsWith(url)?req.continue():req.abort();});
 t.after(async()=>{await ctx.close();assert.deepEqual(errors,[]);assert(posts.every(p=>p.url===url+'/api/queue/config-options'||(p.url===url+'/api/queue/config'&&['read','preview','apply'].includes(p.body.policy_action))));assert.equal(fs.readFileSync(state,'utf8'),defaults);});
 await page.goto(url,{waitUntil:'load'});await page.focus(trigger);await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('.queue-policy-status')?.textContent.includes('Saved queue policy loaded'));return{page,posts};
}
const text=page=>page.$eval('.queue-policy-status',n=>n.textContent);
const read=()=>JSON.parse(fs.readFileSync(file,'utf8'));
async function preview(page){await page.click('.queue-policy-composer .upd-actions button:nth-last-child(2)');await page.waitForFunction(()=>/Preview ready|No changes to save|Preview failed/.test(document.querySelector('.queue-policy-status').textContent));}
async function apply(page){await page.click('[data-fq-config-save]');await page.waitForFunction(()=>/Saved queue exceptions|Not applied/.test(document.querySelector('.queue-policy-status').textContent));}
test('queue chip opens canonical staged controls, saved consumer sources and explicit Off without writes',async t=>{
 const{page,posts}=await open(t);assert.equal(fs.readFileSync(file,'utf8'),original);assert.equal(await page.$eval('#fqConfigTitle',n=>n.textContent),'DEMO · worker policy');
 assert(await page.$eval('#fqConfigEngine',n=>n.disabled));assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));
 const saved=await page.$eval('.queue-policy-saved',n=>n.textContent);assert.match(saved,consumerAvailable?/Resolved by launch consumers/:/Configured only/);assert.match(saved,/claude/);assert.match(saved,/Off · Queue override/);
 assert.equal(await page.$eval('#fqConfigPath',n=>!!n.getClientRects().length),false);assert.equal(await page.$eval('.fq-config-roles',n=>!!n.getClientRects().length),false);
 await page.screenshot({path:path.join(scratch,'desktop-saved.png'),fullPage:true});
 await page.click('[aria-label="Override Model"]');assert.equal(fs.readFileSync(file,'utf8'),original);assert.equal(await page.$eval('#fqConfigModel',n=>n.disabled),false);assert(posts.every(p=>p.body?.policy_action!=='apply'));
});
test('Follow default stages only the chosen exception, previews then applies atomically',async t=>{
 const{page}=await open(t,1440,'#queueConfig');await page.click('[aria-label="Follow default for Failure fallback"]');assert.equal(fs.readFileSync(file,'utf8'),original);await preview(page);
 assert.match(await page.$eval('.queue-policy-preview',n=>n.textContent),/Off · explicit override → Follow default \(unset\)/);assert.match(await text(page),/Preview ready/);await apply(page);
 const expected=JSON.parse(original);delete expected.DEMO.fallback_to_default_worker;assert.deepEqual(read(),expected);assert.deepEqual(await page.evaluate(()=>window.__events),[{queue:'DEMO'}]);assert.match(await page.$eval('.queue-policy-saved',n=>n.textContent),/On · Worker policy/);
});
test('phone native keyboard override, invalidation, preview and engine/model/effort save',async t=>{
 const{page}=await open(t,390);await page.focus('[aria-label="Override Engine"]');await page.keyboard.press('Enter');await page.focus('#fqEngineSeg [data-engine="codex"]');await page.keyboard.press('Enter');
 await page.focus('[aria-label="Override Model"]');await page.keyboard.press('Enter');await page.select('#fqConfigModel','gpt-5-codex');await page.focus('[aria-label="Override Effort"]');await page.keyboard.press('Enter');await page.select('#fqConfigEffort','low');await preview(page);
 assert.match(await page.$eval('.queue-policy-preview',n=>n.textContent),/DEMO only/);assert.equal(await page.$eval('[data-fq-config-save]',n=>n.disabled),false);
 await page.select('#fqConfigEffort','medium');assert.equal(await page.$eval('[data-fq-config-save]',n=>n.disabled),true);await preview(page);
 await page.screenshot({path:path.join(scratch,'phone-preview.png'),fullPage:true});
 const bounds=await page.$eval('.fq-config-dialog',n=>{const r=n.getBoundingClientRect();return{left:r.left,right:r.right,width:innerWidth,scroll:n.scrollWidth,client:n.clientWidth};});assert(bounds.left>=0&&bounds.right<=bounds.width);assert(bounds.scroll<=bounds.client+1);
 await page.focus('[data-fq-config-save]');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('.queue-policy-status').textContent.includes('Saved queue exceptions'));
 const expected=JSON.parse(original);Object.assign(expected.DEMO,{engine:'codex',model:'gpt-5-codex',effort:'medium'});assert.deepEqual(read(),expected);
 await page.keyboard.press('Escape');await page.waitForSelector('.fq-config-composer',{hidden:true});assert.equal(await page.evaluate(()=>document.activeElement.id),'queueChip');
});
test('stale queue edit rejects apply with visible phone error and no partial saves',async t=>{
 const{page}=await open(t,390);await page.click('[aria-label="Follow default for Model"]');await preview(page);const cfg=read();cfg.MANUAL.future=true;fs.writeFileSync(file,JSON.stringify(cfg));const before=fs.readFileSync(file,'utf8');await apply(page);
 assert.match(await text(page),/changed.*Refresh/);assert.equal(fs.readFileSync(file,'utf8'),before);assert.deepEqual(await page.evaluate(()=>window.__events),[]);assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));
 await page.$eval('.queue-policy-status',n=>n.scrollIntoView({block:'center'}));await page.screenshot({path:path.join(scratch,'phone-stale.png'),fullPage:true});
});
test('publication failure remains visible and retry requires new preview',async t=>{
 const{page}=await open(t);await page.click('[aria-label="Follow default for Headroom dispatch"]');await preview(page);fs.writeFileSync(failFlag,'1');await apply(page);
 assert.match(await text(page),/simulated queue save failure/);assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(await page.evaluate(()=>window.__events),[]);assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));fs.rmSync(failFlag);await preview(page);await apply(page);assert(!Object.hasOwn(read().DEMO,'headroom_dispatch'));
});
test('manual queue inherits saved defaults, supports explicit Off and skips no-op dispatch',async t=>{
 const{page}=await open(t,390,'#manualChip');assert.match(await page.$eval('.queue-policy-saved',n=>n.textContent),/codex/);assert.match(await page.$eval('.queue-policy-saved',n=>n.textContent),/manual launch only/);
 await page.click('[aria-label="Follow default for Model"]');await preview(page);assert.match(await text(page),/No changes to save/);assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));assert.deepEqual(await page.evaluate(()=>window.__events),[]);
 await page.click('[aria-label="Override Headroom dispatch"]');await page.select('#fqConfigHeadroom','off');await preview(page);await apply(page);assert.equal(read().MANUAL.headroom_dispatch,false);assert.deepEqual(read().DEMO,JSON.parse(original).DEMO);
});
test('read failure can retry, Escape restores opener and Tab stays in dialog',async t=>{
 const{page}=await open(t);await page.focus('[data-fq-config-save]'); // disabled; use final enabled action instead
 await page.$eval('.upd-actions',n=>Array.from(n.querySelectorAll('button')).filter(b=>!b.disabled).at(-1).focus());await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Close');await page.keyboard.down('Shift');await page.keyboard.press('Tab');await page.keyboard.up('Shift');assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Preview changes');
 const cfg=read();delete cfg.DEMO;fs.writeFileSync(file,JSON.stringify(cfg));await page.click('.upd-actions button:nth-last-child(3)');await page.waitForFunction(()=>document.querySelector('.queue-policy-status').textContent.includes('Could not read'));assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));assert.match(await page.$eval('.queue-policy-saved',n=>n.textContent),/unavailable/);
 fs.writeFileSync(file,original);await page.click('.upd-actions button:nth-last-child(3)');await page.waitForFunction(()=>document.querySelector('.queue-policy-status').textContent.includes('Saved queue policy loaded'));await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'queueChip');
});
test('cross-queue report retains bulk scope and shows manual/fallback/headroom exceptions',async t=>{
 const{page}=await open(t);await page.keyboard.press('Escape');await page.evaluate(()=>window.__report());const reportText=await page.$eval('#settingsQueueTable',n=>n.textContent);assert.match(reportText,/2 active queues/);assert.match(reportText,/MANUAL · manual/);assert.match(reportText,/Fallback: Off · override/);assert.match(reportText,/Headroom: Off · override/);assert.equal(await page.$$eval('[data-queue-policy]',n=>n.length),2);
 await page.focus('[data-queue-policy=MANUAL]');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('.queue-policy-status')?.textContent.includes('Saved queue policy loaded'));assert.match(await page.$eval('#fqConfigTitle',n=>n.textContent),/MANUAL/);
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.matches('[data-queue-policy=MANUAL]')),true);assert.equal(await page.evaluate(()=>window.__underlyingEscapes),0);
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>window.__underlyingEscapes),1);
 assert.match(app,/Clear engine, model and effort pins for:/);assert.match(app,/fallback rules and running workers stay as they are/);
});

test('other queue configuration keeps the same canonical editor and can return without writes',async t=>{
 const{page}=await open(t);await page.evaluate(()=>Array.from(document.querySelectorAll('.upd-actions button')).find(b=>b.textContent==='Other queue configuration').click());await page.waitForFunction(()=>document.querySelector('#fqConfigTitle')?.textContent==='Queue configuration');
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
  await page.waitForFunction(()=>document.querySelector('.queue-policy-status')?.textContent.includes('Saved queue policy loaded'));
  if(!duringLoad)await replace();
  await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.dataset.replacement),'true');
  await page.evaluate(id=>{document.activeElement.id=id;},trigger.slice(1));
  // Reload removes the temporary fetch gate before the next independent case.
  await page.reload({waitUntil:'load'});
 }
});

test('blocked custom model rejects preview before all writes with a visible error',async t=>{
 const{page}=await open(t,390);await page.click('[aria-label="Override Model"]');await page.select('#fqConfigModel','__custom__');await page.type('#fqConfigCustomModel','blocked');await preview(page);assert.match(await text(page),/Preview failed: Model blocked by policy/);assert.equal(fs.readFileSync(file,'utf8'),original);assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));assert.deepEqual(await page.evaluate(()=>window.__events),[]);
});

test('lost apply response never claims success or certain non-application',async t=>{
 const{page}=await open(t);await page.click('[aria-label="Follow default for Model"]');await preview(page);await page.setOfflineMode(true);await page.click('[data-fq-config-save]');await page.waitForFunction(()=>document.querySelector('.queue-policy-status').textContent.includes('Save could not be confirmed'));
 assert.deepEqual(await page.evaluate(()=>window.__events),[]);assert(await page.$eval('[data-fq-config-save]',n=>n.disabled));await page.setOfflineMode(false);await page.click('.upd-actions button:nth-last-child(3)');await page.waitForFunction(()=>document.querySelector('.queue-policy-status').textContent.includes('Saved queue policy loaded'));assert.equal(fs.readFileSync(file,'utf8'),original);
});

test('Follow default for engine removes only that pin and retains model/effort exceptions',async t=>{
 const{page}=await open(t);await page.click('[aria-label="Follow default for Engine"]');await preview(page);const reviewed=await page.$eval('.queue-policy-preview ul',n=>n.textContent);assert.match(reviewed,/Engine: claude → Follow default/);assert.doesNotMatch(reviewed,/Model:|Effort:/);await apply(page);const expected=JSON.parse(original);delete expected.DEMO.engine;assert.deepEqual(read(),expected);
});
