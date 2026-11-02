'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'settings-changes-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
function between(a,b){const i=app.indexOf(a),j=app.indexOf(b,i+a.length);assert(i>=0&&j>i,a);return app.slice(i,j);}
const manager=between('  async function openQueueManager(', '  // Routes a ticket');
const efforts=between('  const CODEX_REASONING_LEVELS =','  function _normalizeModelId(');
const integration=between('  window.cccSettingsChanges?.mount({','  // Trigger check on load');
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/settings-changes.css"><link rel="stylesheet" href="/static/queue-policy.css"><style>body{display:block}#settingsModal .settings-modal{width:min(1100px,96vw)}#settingsPane>.settings-section:not(.is-active-section){display:none!important}#settingsPane>.settings-section.is-active-section{display:block!important}.context-fixture{position:fixed;top:40px;left:20px;width:calc(100vw - 40px);max-width:600px;background:var(--surface);padding:12px;box-sizing:border-box}</style><body><script src="/static/settings-changes.js"></script><script src="/static/queue-policy.js"></script><script src="/static/settings-routing-policy.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');document.body.append(parsed.getElementById('settingsModal'));
const $settingsModal=document.getElementById('settingsModal'),$settingsPane=document.getElementById('settingsPane');let settingsSearchFocusTimer;window.currentConversation='example-conversation';
function openSettingsModal(){$settingsModal.hidden=false;$settingsModal.classList.add('open');}function closeSettingsModal(){$settingsModal.hidden=true;$settingsModal.classList.remove('open');}function clearSettingsSearch(){}
function setActiveSettingsRailSection(section){document.querySelectorAll('#settingsPane>.settings-section').forEach(n=>n.classList.toggle('is-active-section',n.dataset.sectionId===section));document.querySelector('#settingsSection-'+section+'>details')?.setAttribute('open','');}
const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),escapeAttr=escapeHtml;
const MODEL_OPTIONS_BY_ENGINE={claude:[{id:'auto',label:'Auto'}],codex:[{id:'auto',label:'Auto'}]},ENGINE_SUPPORTS_CUSTOM_MODEL={},sessionCostTier=()=>'',_normalizeModelId=v=>v,spawnEngineLabel=v=>v;
const spawnDefaultsState={blockedModels:new Set()},_uxqHealthCache={},_uxqItemsCache={};window.__toasts=[];const showOpToast=(...v)=>window.__toasts.push(v);function _renderQueuePanel(){}
${efforts}
${manager}
window.cccRoutingPolicy.mount({navigate:()=>setActiveSettingsRailSection('routing'),models:()=>[{id:'auto',label:'Auto'}],label:v=>v,saved:()=>{}});
// Existing contextual module's full native composition has dedicated browser
// coverage. These inert hosts check routing/return without opening real tasks.
window.cccSettingsContext={redirect:route=>{closeSettingsModal();const id=route==='reader'?'settingsModalFontSlot':route==='queue-view'?'contextQueuePreferences':'contextListPreferences';let host=document.getElementById(id);if(!host){host=document.createElement('section');host.id=id;document.body.append(host);}document.body.append(host);host.className='context-fixture';host.replaceChildren();const b=document.createElement('button');b.textContent=route+' native controls';host.append(b);b.focus();return true;}};
${integration}
document.addEventListener('click',e=>{const tab=e.target.closest('[data-section-target]');if(tab)setActiveSettingsRailSection(tab.dataset.sectionTarget);});window.__show=()=>{openSettingsModal();setActiveSettingsRailSection('preferences');};
</script><script src="/static/settings-preferences.js"></script><script>window.__show();</script>`;
let child,browser,url;const defaults=path.join(scratch,'spawn-defaults.json'),queues=path.join(scratch,'queue-config.json');
const policy={engine:'claude',auto_compact_k:310,worker_engine:'codex',worker_model:'auto',worker_inherit_session:true,worker_overrides:{worker_engine:'claude',worker_model:'default'},worker_fallback:{enabled:false,models:[]},model_profiles:{fast:{models:[{engine:'claude',model:'auto',effort:'high'}]}}};
const config={MANUAL:{auto_drain:false,engine:'codex',model:'auto',effort:'low',fallback_to_default_worker:false,headroom_dispatch:true,repo_path:'/tmp/example-repository',opaque:'not-a-secret'}};
test.before(async()=>{fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);fs.writeFileSync(defaults,JSON.stringify(policy));fs.writeFileSync(queues,JSON.stringify(config));child=spawn('python3',['-u','tests/fixtures/settings_changes_server.py',scratch],{cwd:root,env:{...process.env,PYTHONPATH:root,CCC_EPHEMERAL:'1'}});let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});url=(await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw Error(stderr);})])).url;browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Customization screenshots:',scratch);});
async function open(t,{width=1440,seed={},fail=false,empty=false,malformed=false}={}){
 fs.writeFileSync(defaults,empty?'{}':JSON.stringify(policy));fs.writeFileSync(queues,malformed?'{':empty?'{}':JSON.stringify(config));fs.rmSync(path.join(scratch,'fail'),{force:true});if(fail)fs.writeFileSync(path.join(scratch,'fail'),'1');
 const context=await browser.createBrowserContext(),page=await context.newPage(),errors=[],requests=[];page.setDefaultTimeout(8000);t.after(async()=>{await context.close();assert.deepEqual(errors,[]);});await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);page.on('request',r=>{if(new URL(r.url()).origin!==url){r.abort();return;}requests.push({method:r.method(),path:new URL(r.url()).pathname,body:r.postData()});r.continue();});await page.evaluateOnNewDocument(seed=>{for(const[k,v]of Object.entries(seed))localStorage.setItem(k,v);},seed);await page.goto(url);await page.waitForFunction(()=>document.getElementById('settingsChangesStatus')?.textContent.includes('snapshot')||document.getElementById('settingsChangesStatus')?.textContent.includes('unavailable')).catch(async error=>{throw Error(JSON.stringify({errors,status:await page.evaluate(()=>document.getElementById('settingsChangesStatus')?.textContent),review:await page.$('#settingsChanges')!==null,requests}));});return {page,requests};
}
async function reveal(page,scope){await page.$$eval('[data-change-scope="'+scope+'"] details',ns=>ns.forEach(n=>n.open=true));}
for(const width of [1440,390])test('actual customization review across all three scopes without writes '+width,async t=>{
 const{page,requests}=await open(t,{width,seed:{'ccc-theme':'light','ccc-conv-font-scale':'1.2','ccc-notify-enabled':'0','ccc-notify-tasks':'1','private-account':'not-a-secret'}});assert.equal(await page.$$eval('[data-change-scope]',ns=>ns.length),3);assert.match(await page.$eval('#settingsChanges',n=>n.textContent),/MANUAL/);assert.match(await page.$eval('#settingsChanges',n=>n.textContent),/Retained worker engine/);await reveal(page,'browser');assert.match(await page.$eval('[data-change-key="browser.notify_tasks"]',n=>n.textContent),/shadowed/);assert.equal(await page.$eval('#settingsChanges',n=>n.textContent.includes('not-a-secret')||n.textContent.includes('/tmp/example-repository')),false);assert.equal(requests.some(r=>r.method!=='GET'),false);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:path.join(scratch,'review-'+width+'.png'),fullPage:true});for(const scope of ['machine','queue']){await page.$eval('[data-change-scope="'+scope+'"]',n=>n.scrollIntoView({block:'start'}));await page.screenshot({path:path.join(scratch,scope+'-'+width+'.png'),fullPage:true});}if(width===390){await page.$eval('[data-change-key="MANUAL.headroom_dispatch"] button',n=>n.scrollIntoView({block:'center'}));assert.equal(await page.$eval('[data-change-key="MANUAL.headroom_dispatch"] button',n=>{const r=n.getBoundingClientRect();return r.height>=44&&n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}),true);}
});
test('explicit same-as-baseline is distinguished from effective change',async t=>{
 const{page}=await open(t,{seed:{'ccc-theme':'dark'}});await reveal(page,'browser');assert.match(await page.$eval('[data-change-key="browser.theme"]',n=>n.textContent),/Explicitly saved at the baseline/);assert.match(await page.$eval('[data-change-key="browser.theme"]',n=>n.textContent),/dark/);
});
test('legacy browser density uses actual native fallback without seeding',async t=>{
 const{page}=await open(t,{seed:{'ccc-compact-rows':'0'}});await reveal(page,'browser');assert.match(await page.$eval('[data-change-key="browser.coding_density"]',n=>n.textContent),/inherited.*detailed/s);assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-coding-density')),null);assert.match(await page.$eval('[data-change-key="browser.compact_rows"]',n=>n.textContent),/shadowed/);
});
test('canonical reader return preserves original review focus on phone',async t=>{
 const{page}=await open(t,{width:390,seed:{'ccc-conv-font-scale':'1.2'}});const selector='[data-change-key="browser.reader_scale"] button';await page.focus(selector);await page.keyboard.press('Enter');await page.waitForSelector('#settingsModalFontSlot .settings-changes-return');await page.focus('#settingsModalFontSlot .settings-changes-return');await page.keyboard.press('Enter');assert.equal(await page.$eval('#settingsModal',n=>n.hidden),false);assert.equal(await page.evaluate(()=>document.activeElement.closest('[data-change-key]')?.dataset.changeKey),'browser.reader_scale');
});
test('native staged policy opens exact profile context without writes and returns',async t=>{
 const{page,requests}=await open(t);await reveal(page,'machine');await page.click('[data-change-key="model_profiles.fast.models"] button');await page.waitForFunction(()=>document.getElementById('routingPolicyContext').value==='fast');await page.waitForFunction(()=>document.activeElement.id==='routingPolicyContext').catch(async()=>{throw Error(await page.$eval('#routingPolicyEditor',n=>JSON.stringify({error:n.querySelector('#routingPolicyError').textContent,status:n.querySelector('#routingPolicyStatus').textContent,disabled:n.querySelector('#routingPolicyContext').disabled})));});await page.click('#routingPolicyEditor .settings-changes-return');assert.equal(await page.evaluate(()=>document.activeElement.closest('[data-change-key]')?.dataset.changeKey),'model_profiles.fast.models');assert.equal(requests.some(r=>r.method!=='GET'),false);
});
test('manual queue edit uses native manager, exact queue, Escape return without mutation',async t=>{
 const{page,requests}=await open(t,{width:390});await reveal(page,'queue');const trigger='[data-change-key="MANUAL.engine"] button';await page.focus(trigger);await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('.queue-policy-status')?.textContent.includes('Saved queue policy loaded'));assert.equal(await page.$eval('#fqConfigQueue',n=>n.value),'MANUAL');await page.keyboard.press('Escape');assert.equal(await page.$('.fq-config-composer'),null);assert.equal(await page.evaluate(()=>document.activeElement.closest('[data-change-key]')?.dataset.changeKey),'MANUAL.engine');assert(requests.filter(r=>r.method==='POST').every(r=>r.path==='/api/queue/config-options'||JSON.parse(r.body).policy_action==='read'));assert.deepEqual(JSON.parse(fs.readFileSync(queues,'utf8')),config);
});
test('malformed queue store is separately unavailable while host changes stay visible',async t=>{
 const{page}=await open(t,{malformed:true});assert.match(await page.$eval('[data-change-scope="queue"]',n=>n.textContent),/Unavailable/);assert.match(await page.$eval('[data-change-scope="machine"]',n=>n.textContent),/310/);
});
test('empty covered state never claims every setting is default',async t=>{
 const{page}=await open(t,{empty:true});assert.match(await page.$eval('#settingsChangesGroups',n=>n.textContent),/No saved overrides in this covered scope.*does not establish/s);
});
test('failed read and retry are read-only and retain keyboard focus',async t=>{
 const{page,requests}=await open(t,{width:390,fail:true});assert.equal(await page.$$eval('[data-change-scope]',ns=>ns.length),0);fs.rmSync(path.join(scratch,'fail'));await page.focus('#settingsChangesRefresh');await page.keyboard.press('Enter');await page.waitForSelector('[data-change-scope="queue"]');assert.equal(await page.evaluate(()=>document.activeElement.id),'settingsChangesRefresh');assert.equal(requests.some(r=>r.method!=='GET'),false);
});
test('storage-event changes refresh only supported browser rows with focus intact',async t=>{
 const{page}=await open(t,{seed:{'ccc-theme':'light'}});await page.focus('[data-change-key="browser.theme"] button');await page.evaluate(()=>{localStorage.setItem('ccc-theme','dark');window.dispatchEvent(new StorageEvent('storage',{key:'ccc-theme'}));});assert.equal(await page.evaluate(()=>document.activeElement.closest('[data-change-key]')?.dataset.changeKey),'browser.theme');assert.match(await page.$eval('[data-change-key="browser.theme"]',n=>n.textContent),/Explicitly saved at the baseline/);
});
test('existing browser profiles still refuse newly reviewed contextual keys',async t=>{
 const{page}=await open(t);await page.waitForFunction(()=>!document.querySelector('[data-pref-action="import"]').disabled);await page.$eval('#settingsPreferencesProfile',n=>{n.value=JSON.stringify({format:'ccc-preferences',version:1,values:{reader_scale:1.2}});});await page.click('[data-pref-action="import"]');assert.match(await page.$eval('[data-pref-status]',n=>n.textContent),/supported browser preferences/);assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-conv-font-scale')),null);
});

test('stale snapshot is labelled without an automatic reread or mutation',async t=>{
 const{page,requests}=await open(t);const count=requests.length;await page.evaluate(()=>{Date.now=()=>Number.MAX_SAFE_INTEGER;document.querySelector('#settingsChangesRefresh').focus();});assert.match(await page.$eval('#settingsChangesStatus',n=>n.textContent),/Snapshot is stale/);assert.equal(requests.length,count);
});
test('browser storage failure retains independent host and queue evidence',async t=>{
 const{page}=await open(t);await page.evaluate(()=>{Storage.prototype.getItem=()=>{throw Error('Fixture unavailable');};});await page.click('#settingsChangesRefresh');await page.waitForFunction(()=>document.querySelector('[data-change-scope="browser"]')?.textContent.includes('Unavailable'));assert.match(await page.$eval('[data-change-scope="machine"]',n=>n.textContent),/310/);
});

for(const width of [1440,390])test('appearance uses its canonical host and returns '+width,async t=>{
 const {page}=await open(t,{width,seed:{'ccc-font':'mono'}});
 await page.click('[data-change-key="browser.font"] button');
 await page.waitForSelector('#appearancePopover .settings-changes-return',{visible:true});
 await page.click('#appearancePopover .settings-changes-return');
 assert.equal(await page.evaluate(()=>document.activeElement.closest('[data-change-key]')?.dataset.changeKey),'browser.font');
});
test('handled Edit click does not reach an outside-click closer',async t=>{
 const {page}=await open(t,{width:390,seed:{'ccc-queue-rhs-list':'on'}});
 await page.evaluate(()=>document.addEventListener('click',()=>{const node=document.getElementById('contextQueuePreferences');if(node)node.hidden=true;}));
 await page.click('[data-change-key="browser.queue_rhs_list"] button');
 await page.waitForSelector('#contextQueuePreferences .settings-changes-return',{visible:true});
});
test('unavailable reader keeps review and visible explanation with source focus',async t=>{
 const {page}=await open(t,{width:390,seed:{'ccc-conv-font-scale':'1.2'}});
 await page.evaluate(()=>{window.cccSettingsContext.redirect=()=>{document.getElementById('settingsModal').hidden=true;document.getElementById('settingsModalFontSlot').hidden=true;return true;};});
 await page.click('[data-change-key="browser.reader_scale"] button');
 assert.equal(await page.$eval('#settingsModal',n=>n.hidden),false);
 assert.match(await page.$eval('#settingsChangesStatus',n=>n.textContent),/Select a conversation/);
 assert.equal(await page.evaluate(()=>document.activeElement.closest('[data-change-key]')?.dataset.changeKey),'browser.reader_scale');
});

test('empty or new reader is rejected before contextual navigation on phone',async t=>{
 const {page}=await open(t,{width:390,seed:{'ccc-conv-font-scale':'1.2'}});
 for(const value of ['', '__new__']){
  await page.evaluate(value=>{window.currentConversation=value;window.__redirected=false;window.cccSettingsContext.redirect=()=>{window.__redirected=true;};},value);
  await page.click('[data-change-key="browser.reader_scale"] button');
  assert.equal(await page.evaluate(()=>window.__redirected),false);
  assert.equal(await page.$eval('#settingsModal',n=>n.hidden),false);
  assert.match(await page.$eval('#settingsChangesStatus',n=>n.textContent),/Select a conversation/);
 }
});
test('contextual Back overrides later native compact button sizing',async t=>{
 const {page}=await open(t,{width:390,seed:{'ccc-queue-rhs-list':'on'}});
 await page.addStyleTag({content:'@media(max-width:1200px){[data-context-preferences] button{min-height:36px}}'});
 await page.click('[data-change-key="browser.queue_rhs_list"] button');
 await page.$eval('#contextQueuePreferences',n=>n.setAttribute('data-context-preferences',''));
 assert.equal(await page.$eval('#contextQueuePreferences .settings-changes-return',n=>n.getBoundingClientRect().height>=44),true);
});

test('rejected reader restores onscreen source after review scroll reset',async t=>{
 const {page}=await open(t,{width:390,seed:{'ccc-conv-font-scale':'1.2'}});
 await page.evaluate(()=>{window.currentConversation='';const row=document.querySelector('[data-change-key="browser.reader_scale"]');row.style.marginTop='1500px';const original=window.setActiveSettingsRailSection;window.setActiveSettingsRailSection=(...args)=>{original(...args);document.getElementById('settingsPane').scrollTop=0;};});
 await page.click('[data-change-key="browser.reader_scale"] button');
 await page.waitForFunction(()=>{const el=document.activeElement,r=el.getBoundingClientRect();return el.closest('[data-change-key]')?.dataset.changeKey==='browser.reader_scale'&&r.top>=0&&r.bottom<=innerHeight&&el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));});
});
