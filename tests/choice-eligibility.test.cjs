'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),http=require('node:http'),os=require('node:os');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),read=name=>fs.readFileSync(path.join(root,name),'utf8'),app=read('static/app.js');
const between=(a,b)=>{const start=app.indexOf(a),end=app.indexOf(b,start+a.length);assert(start>=0&&end>start,a);return app.slice(start,end);};
const sandbox={window:{}};vm.runInNewContext(read('static/choice-eligibility.js'),sandbox);const projection=sandbox.window.cccChoiceEligibility;
const now=Date.now(),catalog={claude:[{id:'sonnet',label:'Sonnet',supports_reasoning:true,supports_vision:true},{id:'retired',available:false,availability_reason:'Removed from catalog'},{id:'blocked',policy_blocked:true}],codex:[{id:'codex-model',label:'Codex model'}]},snapshot={generated_at:new Date(now).toISOString(),engines:{claude:{cli_present:true,auth_present:true},codex:{cli_present:true,auth_present:null}}};
const facts={catalog,snapshot,now};
for(const [name,view,pattern,rank] of [
 ['fresh local evidence',snapshot,/local sign-in evidence/,0],
 ['null auth', {...snapshot,engines:{claude:{cli_present:true,auth_present:null}}},/sign-in unchecked/,1],
 ['missing auth', {...snapshot,engines:{claude:{cli_present:true}}},/sign-in unchecked/,1],
 ['negative auth', {...snapshot,engines:{claude:{cli_present:true,auth_present:false}}},/sign-in not detected/,2],
 ['no installation', {...snapshot,engines:{claude:{cli_present:false,auth_present:true}}},/Installation not detected/,2],
 ['stale evidence',{...snapshot,generated_at:new Date(now-300001).toISOString()},/stale/,3],
 ['undated evidence',{...snapshot,generated_at:null},/undated/,3],
 ['future-dated evidence',{...snapshot,generated_at:new Date(now+60001).toISOString()},/stale/,3],
 ['failed snapshot',{...snapshot,unavailable:true},/unavailable/,3],
 ['missing snapshot',{},/unchecked/,3],
])test('projection distinguishes '+name,()=>{const result=projection.evidence('claude',view,now);assert.match(result.text,pattern);assert.equal(result.rank,rank);});
test('policy/catalog/context restrictions explain hard disabled choices while current custom stays intact',()=>{
 assert.match(projection.project('claude',{id:'blocked'},{},facts).reason,/Blocked/);
 assert.match(projection.project('claude',{id:'retired'},{},facts).reason,/Removed/);
 assert.match(projection.project('claude',{id:'codex-model'},{},facts).reason,/another engine/);
 assert.match(projection.project('codex',{id:'codex-model'},{engines:['claude']},facts).reason,/context/);
 const custom=projection.project('claude',{id:'custom-stored'},{},facts);assert.equal(custom.id,'custom-stored');assert.equal(custom.disabled,false);assert.equal(custom.unknown,true);
 assert.equal(projection.project('claude',{id:'blocked'},{policyConfirmation:true},facts).disabled,false);
 assert.equal(projection.project('claude',{id:'sonnet'},{},{...facts,disabled:['claude']}).disabled,false);
 assert.match(projection.project('claude',{id:'sonnet'},{},{...facts,disabled:['claude']}).observation,/switched off/);
});

const encoded=JSON.stringify(read('static/index.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,''));
const engineHub=between('  const ENGINE_HUB_META =','  // ── Engines section: BYOK');
const fixture=`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/queue-policy.css"><link rel="stylesheet" href="/static/engine-connection.css"><link rel="stylesheet" href="/static/choice-eligibility.css"><style>body{display:block;overflow:auto}#fixture{max-width:1000px;margin:auto;padding:0}#settingsModal .settings-modal{width:min(1000px,96vw);max-width:96vw}.settings-section{display:block!important}.settings-section[hidden]{display:none!important}#settingsModal{position:relative;display:block;inset:auto}#settingsModal[hidden]{display:none!important}</style></head><body><button id="queue">Queue policy</button><main id="fixture"></main><script src="/static/choice-eligibility.js"></script><script src="/static/engine-connection.js"></script><script src="/static/settings-routing-policy.js"></script><script src="/static/settings-launch-preview.js"></script><script src="/static/queue-policy.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');const shell=parsed.getElementById('settingsModal');document.getElementById('fixture').append(shell);shell.hidden=false;
const $settingsModal=shell,$settingsModalInner=shell.querySelector('.settings-modal'),$settingsPane=shell.querySelector('#settingsPane'),$settingsRail=shell.querySelector('#settingsRail');
let _settingsCurrentSection='routing',choiceEligibilityReturn,settingsSearchFocusTimer;
function setActiveSettingsRailSection(section){_settingsCurrentSection=section;document.querySelectorAll('.settings-section').forEach(n=>n.hidden=!(section==='routing'?['defaults','routing'].includes(n.dataset.sectionId):n.dataset.sectionId===section));}
function openSettingsModal(){shell.hidden=false;}function closeSettingsModal(){if(choiceEligibilityReturn){choiceEligibilityReturn();return;}shell.hidden=true;}function clearSettingsSearch(){}
const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),escapeAttr=escapeHtml,sessionCostTier=()=>'',_normalizeModelId=v=>v,spawnEngineLabel=v=>v;
const MODEL_OPTIONS_BY_ENGINE=${JSON.stringify(catalog)},SPAWN_DEFAULT_ENGINES=['claude','codex'],WORKER_DEFAULT_ENGINES=['claude','codex','kimi','grok'],SPAWN_DEFAULT_OTHER='__other__',ENGINE_SUPPORTS_CUSTOM_MODEL={codex:false};
const normalizeSpawnDefaultEngine=v=>v,latestModelsOnly=rows=>rows,_modelAllowedForEngine=()=>false,_engineSupportsCustomModel=()=>true;
const spawnDefaultsState={blockedModels:new Set(),disabled_engines:[]},_uxqHealthCache={},_uxqItemsCache={};
let _emnInstalled=null,_emnObservedAt=null;let _engHubData={doctor:${JSON.stringify(snapshot)}};let _engHubUnavailable=false;
const showOpToast=()=>{},_renderQueuePanel=()=>{},shouldUseInlineOnboardingLogin=()=>false,onbLoginSessions={};
${between('  const CODEX_REASONING_LEVELS =','  function _normalizeModelId(')}
${between('  function modelOptionsForSpawnEngine(', '  function mergeSpawnDefaults(')}
${between('  async function openQueueManager(', '  // Routes a ticket')}
// Native cached-only schedule guards; a doctor request is forbidden by the test host.
let _engHubTimer=null;const ENGINE_HUB_POLL_MS=30;function _engHubVisible(){return _settingsCurrentSection==='engines'&&!shell.hidden;}function applyDisabledEnginesToPickers(){}function refreshEnginesHub(){return fetch('/api/engines/doctor');}
${between('  function _engHubSchedule()', '  function _engHubNote(')}
${between('  window.cccChoiceEligibility?.configure({','  window.cccLaunchPreview?.mount();')}
window.cccEngineConnection.mount({navigate:options=>{openSettingsModal();setActiveSettingsRailSection('engines');if(!options.cachedOnly)startEnginesHub();},refresh:()=>fetch('/api/engines/doctor'),sessionEngines:SPAWN_DEFAULT_ENGINES,workerEngines:WORKER_DEFAULT_ENGINES,terminalAvailable:()=>false,checking:()=>false});
window.cccEngineConnection.render([{engine:'claude',meta:{label:'Claude',cli:'claude',url:''},installed:true,auth:null,disabled:false}],{});
setActiveSettingsRailSection('routing');window.__policy=window.cccRoutingPolicy;window.__snapshot=(next)=>{_engHubData={doctor:next};window.cccChoiceEligibility.refresh();};window.__unavailable=()=>{_engHubUnavailable=true;window.cccChoiceEligibility.refresh();};
document.getElementById('queue').onclick=()=>openQueueManager('DEMO');
</script></body></html>`;
const base={ok:true,engine:'claude',models:{claude:'custom-stored',codex:'codex-model'},reasoning_effort:'high',auto_compact_k:300,worker_engine:'codex',worker_model:'codex-model',worker_reasoning_effort:'high',worker_auto_compact_k:300,worker_inherit_session:false,supported_engines:['claude','codex'],worker_fallback_engines:['claude','codex','kimi','grok'],model_profile_engines:['claude'],efforts_by_engine:{claude:['high'],codex:['high']},policy_revision:'test-revision',policy_documents:{worker_fallback:{enabled:true,models:[{engine:'codex',model:'codex-model'}]},model_profiles:{standard:{models:[{engine:'claude',model:'sonnet'}]}}}};
let browser,server,url;const scratch=fs.mkdtempSync(path.join(os.tmpdir(),'choice-eligibility-'));
test.before(async()=>{
 server=http.createServer((req,res)=>{
  const send=(value,type='application/json; charset=utf-8')=>{res.writeHead(200,{'Content-Type':type.includes('charset')?type:type+'; charset=utf-8'});res.end(type.startsWith('application/json')?JSON.stringify(value):value);};
  if(req.url==='/')return send(fixture,'text/html');
  if(req.url.startsWith('/static/'))return send(read(req.url.slice(1)),req.url.endsWith('.css')?'text/css':'application/javascript');
  if(req.url==='/api/spawn-defaults?policy=1')return send(base);
  if(req.url==='/api/queue/config-options')return send({ok:true,defaults:{},queues:[{queue:'DEMO',config:{engine:'codex',model:'custom-queue',future_metadata:true}}],models_by_engine:{claude:['sonnet'],codex:['codex-model']}});
  if(req.url.startsWith('/api/queue/roles'))return send({ok:true,available:false});
  if(req.url==='/api/queue/config')return send({ok:true,queue:'DEMO',config:{engine:'codex',model:'custom-queue'},overrides:{engine:'codex',model:'custom-queue'},revision:'queue-revision',sources:{},effective:{}});
  if(req.url==='/api/settings/queue-engines')return send({ok:true,queues:[]});
  res.writeHead(405);res.end('Forbidden fixture action');
 });await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));url='http://127.0.0.1:'+server.address().port;
 browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();await new Promise(resolve=>server.close(resolve));console.log('Choice screenshots:',scratch);});
async function open(t,width=1440){const page=await browser.newPage(),errors=[],requests=[];await page.setViewport({width,height:1000});page.on('pageerror',error=>errors.push(error.message));page.on('request',r=>requests.push({url:r.url(),method:r.method(),body:r.postData()}));t.after(async()=>{await page.close();assert.deepEqual(errors,[]);assert(!requests.some(r=>/doctor|auth|install|login|usage/.test(new URL(r.url).pathname)));assert(!requests.some(r=>r.method==='POST'&&!['/api/queue/config-options','/api/queue/config'].includes(new URL(r.url).pathname)));assert(requests.filter(r=>r.url.endsWith('/api/queue/config')).every(r=>JSON.parse(r.body).policy_action==='read'));});await page.goto(url);await page.waitForFunction(()=>document.getElementById('routingPolicyStatus').textContent.includes('Saved policy loaded'));return page;}
test('native staged model choices retain unknown selection, disclose restrictions and reorder cached engine choices',async t=>{
 const page=await open(t);assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.value),'custom-stored');
 const rows=await page.$eval('#spawnDefaultsModel',n=>[...n.options].map(o=>({value:o.value,disabled:o.disabled,text:o.textContent})));
 assert(rows.find(o=>o.value==='blocked').disabled);assert.match(rows.find(o=>o.value==='blocked').text,/Blocked by model policy/);assert(rows.find(o=>o.value==='retired').disabled);assert.match(rows.find(o=>o.value==='sonnet').text,/Reasoning, Images/);
 await page.$eval('#spawnDefaultsModel + details',n=>n.open=true);assert.match(await page.$eval('#spawnDefaultsModel + details',n=>n.innerText),/retained; native validation/);
 await page.evaluate(()=>window.__snapshot({generated_at:new Date().toISOString(),engines:{claude:{cli_present:false,auth_present:false},codex:{cli_present:true,auth_present:true}}}));
 assert.equal(await page.$eval('#spawnDefaultsEngine',n=>n.options[0].value),'codex');assert.equal(await page.$eval('#spawnDefaultsEngine',n=>n.value),'claude');assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.value),'custom-stored');
 await page.screenshot({path:path.join(scratch,'policy-desktop.png'),fullPage:true});
});
test('stale, unavailable and null cache never turns into confirmed sign-in or drops the draft',async t=>{
 const page=await open(t,390);await page.$eval('#spawnDefaultsWorkerModel + details',n=>n.open=true);assert.match(await page.$eval('#spawnDefaultsWorkerModel + details',n=>n.innerText),/sign-in unchecked/);
 await page.evaluate(()=>window.__snapshot({generated_at:'2001-01-01T00:00:00Z',engines:{claude:{cli_present:true,auth_present:true}}}));
 assert.match(await page.$eval('#spawnDefaultsModel + details p',n=>n.textContent),/stale/);
 await page.evaluate(()=>window.__unavailable());assert.match(await page.$eval('#spawnDefaultsModel + details p',n=>n.textContent),/unavailable/);
 assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.value),'custom-stored');
});
test('managed profiles expose Claude-only context and keep current incompatible selections visible',async t=>{
 const page=await open(t);await page.evaluate(()=>window.__policy.open('standard'));
 const engines=await page.$$eval('#routingPolicyChain fieldset select:first-of-type',nodes=>[...nodes[0].options].map(o=>o.value));assert.deepEqual(engines,['claude']);
 await page.evaluate(()=>window.__policy.open('worker_fallback'));assert.equal(await page.$eval('#routingPolicyChain fieldset select',n=>n.value),'codex');
 await page.screenshot({path:path.join(scratch,'fallback-desktop.png'),fullPage:true});
});
test('phone keyboard cached setup handoff returns to staged policy without probes or writes',async t=>{
 const page=await open(t,390);await page.focus('#spawnDefaultsModel + details summary');await page.keyboard.press('Enter');await page.focus('#spawnDefaultsModel + details button');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.getElementById('choiceEligibilityReturn'));
 await page.evaluate(()=>startEnginesHub());await new Promise(resolve=>setTimeout(resolve,100));assert.equal(await page.evaluate(()=>window.cccEngineConnection.cachedOnly()),true);
 const backHit=await page.$eval('#choiceEligibilityReturn',n=>{const r=n.getBoundingClientRect();return r.height>=44&&n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));});assert(backHit);await page.screenshot({path:path.join(scratch,'setup-phone.png'),fullPage:true});await page.keyboard.press('Escape');assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.value),'custom-stored');assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Engine setup · keep draft');
 await page.screenshot({path:path.join(scratch,'policy-phone.png'),fullPage:true});
});
test('queue canonical controls preserve unsupported custom identifier and staged overrides across cached setup',async t=>{
 const page=await open(t,390);await page.click('#queue');await page.waitForSelector('.queue-policy-composer');await page.waitForFunction(()=>document.querySelector('.queue-policy-status')?.textContent.includes('Saved queue policy loaded'));
 assert.equal(await page.$eval('#fqConfigCustomModel',n=>n.value),'custom-queue');assert.equal(await page.$eval('#fqConfigCustomModel',n=>n.hidden),false);
 await page.click('[aria-label="Override Model"]');await page.$eval('#fqConfigModel + details',n=>n.open=true);await page.focus('#fqConfigModel + details button');await page.keyboard.press('Enter');await page.waitForSelector('#choiceEligibilityReturn');await page.keyboard.press('Escape');
 assert.equal(await page.$eval('#fqConfigCustomModel',n=>n.value),'custom-queue');assert.equal(await page.$eval('#fqConfigModel',n=>n.disabled),false);
 assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Engine setup · keep draft');
 await page.screenshot({path:path.join(scratch,'queue-phone.png'),fullPage:true});await page.keyboard.press('Escape');assert.equal(await page.$('.queue-policy-composer'),null);
});
test('current incompatible catalog value stays selected and disabled; no automatic substitution',async t=>{
 const original=base.models.claude;try{base.models.claude='retired';const page=await open(t);assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.value),'retired');assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.selectedOptions[0].disabled),true);await page.$eval('#spawnDefaultsModel + details',n=>n.open=true);assert.match(await page.$eval('#spawnDefaultsModel + details p',n=>n.textContent),/Removed from catalog/);}finally{base.models.claude=original;}
});
test('stored foreign engine in managed profile is retained with a context-specific restriction',async t=>{
 const original=base.policy_documents.model_profiles.standard.models;try{base.policy_documents.model_profiles.standard.models=[{engine:'codex',model:'codex-model'}];const page=await open(t);await page.evaluate(()=>window.__policy.open('standard'));assert.equal(await page.$eval('#routingPolicyChain fieldset select',n=>n.value),'codex');assert.equal(await page.$eval('#routingPolicyChain fieldset select',n=>n.selectedOptions[0].disabled),true);assert.match(await page.$eval('#routingPolicyChain fieldset select',n=>n.selectedOptions[0].textContent),/Unsupported engine for this policy context/);assert.equal(await page.$eval('#routingPolicyChain fieldset label:nth-of-type(2) select',n=>n.value),'codex-model');}finally{base.policy_documents.model_profiles.standard.models=original;}
});
test('stored unknown engine/model is not normalized to Claude and cached installation never confirms auth',async t=>{
 const original=base.engine;try{base.engine='custom-engine';base.models['custom-engine']='custom-engine-model';const page=await open(t);assert.equal(await page.$eval('#spawnDefaultsEngine',n=>n.value),'custom-engine');assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.value),'custom-engine-model');await page.evaluate(()=>{_engHubData=null;_emnInstalled={claude:true};_emnObservedAt=new Date().toISOString();window.cccChoiceEligibility.refresh();});assert.match(await page.$eval('#spawnDefaultsEngine option[value="claude"]',n=>n.textContent),/Installation detected; sign-in unchecked/);}finally{base.engine=original;delete base.models['custom-engine'];}
});
test('pending polling timer is inert during cached handoff and native Settings close returns to draft',async t=>{
 const page=await open(t);await page.evaluate(()=>{_settingsCurrentSection='engines';_engHubSchedule();_settingsCurrentSection='routing';});await page.$eval('#spawnDefaultsModel + details',n=>n.open=true);await page.click('#spawnDefaultsModel + details button');await new Promise(resolve=>setTimeout(resolve,100));assert.match(await page.$eval('#engineConnectionStatus',n=>n.textContent),/Cached snapshot only/);await page.evaluate(()=>closeSettingsModal());assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.value),'custom-stored');assert.equal(await page.$('#choiceEligibilityReturn'),null);
});
test('native phone keyboard selects a compatible catalog model while disabled choices remain unavailable',async t=>{
 const page=await open(t,390);await page.focus('#spawnDefaultsModel');await page.keyboard.press('Home');await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');assert.equal(await page.$eval('#spawnDefaultsModel',n=>n.value),'sonnet');assert.match(await page.$eval('#routingPolicyStatus',n=>n.textContent),/Draft only/);
 await page.focus('#spawnDefaultsModel + details summary');await page.keyboard.press('Enter');const size=await page.$eval('#spawnDefaultsModel + details button',n=>{const r=n.getBoundingClientRect();return {height:r.height,width:r.width,hit:n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};});assert(size.height>=44);assert(size.width>=44);assert(size.hit);
 await page.addScriptTag({url:url+'/static/settings-scope.js'});assert.equal(await page.$eval('#spawnDefaultsModel + details button',n=>window.cccSettingsScope.describe(n).id),'choice-inspection');
});
test('native installation cache retains booleans and excludes null instead of inventing missing installation',async()=>{
 const cache={window:{},currentConversation:'__new__',Date,fetch:async()=>({json:async()=>({engines:[{kind:'spawn',engine:'claude',installed:null},{kind:'spawn',engine:'codex',installed:true},{kind:'spawn',engine:'kimi',installed:false}]})})};
 vm.runInNewContext('let _emnInstalled=null,_emnObservedAt=null;function renderEngineMissingNotice(){}function renderNsAdvisor(){}'+between('  async function refreshEngineMissingNotice(', '  function renderEngineMissingNotice()')+';window.run=async()=>{await refreshEngineMissingNotice();return _emnInstalled;};',cache);
 assert.deepEqual(JSON.parse(JSON.stringify(await cache.window.run())),{codex:true,kimi:false});
});
test('queue-local Override shares catalog and policy exclusions without saving or replacing the effective route',async t=>{
 const page=await open(t);await page.click('#queue');await page.waitForFunction(()=>document.querySelector('.queue-policy-status')?.textContent.includes('Saved queue policy loaded'));await page.click('[aria-label="Override Engine"]');await page.click('#fqEngineSeg [data-engine="claude"]');await page.click('[aria-label="Override Model"]');
 const rows=await page.$eval('#fqConfigModel',n=>Object.fromEntries([...n.options].map(o=>[o.value,{disabled:o.disabled,text:o.textContent}])));assert.equal(rows.blocked.disabled,true);assert.match(rows.blocked.text,/Blocked by model policy/);assert.equal(rows.retired.disabled,true);assert.match(rows.retired.text,/Removed from catalog/);assert.equal(rows.sonnet.disabled,false);assert.equal(await page.$eval('#fqConfigModel',n=>n.value),'');assert.match(await page.$eval('.queue-policy-status',n=>n.textContent),/nothing is saved|Unsaved queue exceptions/);await page.screenshot({path:path.join(scratch,'queue-desktop.png'),fullPage:true});
});
