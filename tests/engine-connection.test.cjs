'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'engine-connection-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
function between(a,b){const start=app.indexOf(a),end=app.indexOf(b,start+a.length);assert(start>=0&&end>start,a);return app.slice(start,end);}
const organize=between('  function organizeSettings() {','  function isSettingsFormField(');
const grouping=organize.slice(0,organize.indexOf('  organizeSettings();')+'  organizeSettings();'.length);
const hub=between('  const ENGINE_HUB_META =','  // ── Engines section: BYOK');
const login=between('  let currentOnbStep =','  async function checkOnboarding(');
const shell=between('  let settingsSearchFocusTimer;','  // Deep link:');
const active=between('  function setActiveSettingsRailSection(','  function ensureSettingsRowCrumb(');
const search=between('  function ensureSettingsRowCrumb(','  function settingsResetAppearance(');
const firstRun=between("  const ENGINES_FIRST_RUN_KEY =",'  // ── First-run step 2:');
const close=between('  function closeSettingsModal() {','  // Exposed for call sites');
const mount=between('  window.cccEngineConnection?.mount({','  if (window.cccEngineConnection?.mounted())');
const legacy=between('  function showOnboarding(statusData) {','  function showOnbStep(')+between('  async function installMissingEngine(engine) {',"  window.addEventListener('focus',");
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/engine-connection.css"><style>body{display:block}#settingsModal .settings-modal{width:min(1100px,96vw)}#settingsPane>.settings-section:not(.is-active-section){display:none!important}#settingsPane>.settings-section.is-active-section{display:block}#composer{padding:20px}#settingsBtn{display:none}</style></head><body><button id="opener" data-mobile-nav-menu>Connect engine</button><button id="settingsBtn" hidden>Settings</button><div id="composer" hidden><textarea id="convInput" aria-label="Session prompt"></textarea></div><script src="/static/engine-connection.js"></script><script src="/static/settings-routing-policy.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');document.body.append(parsed.getElementById('settingsModal'),parsed.getElementById('onboardingModal'));
const $settingsModal=document.getElementById('settingsModal'),$settingsModalInner=$settingsModal.querySelector('.settings-modal'),$settingsPane=document.getElementById('settingsPane'),$settingsRail=document.getElementById('settingsRail'),$settingsSearchInput=document.getElementById('settingsSearchInput'),$settingsBtn=document.getElementById('settingsBtn'),$settingsEmptyState=document.getElementById('settingsEmptyState'),$settingsEmptyQuery=document.getElementById('settingsEmptyQuery');
let _settingsSearchIndex=null,_settingsModalPrevFocus=null,_settingsCurrentSection='engines',_spawnEngineChosenByUser=false;
const APP_CONFIG={capabilities:{launchTerminal:new URLSearchParams(location.search).has('terminal'),terminalJump:new URLSearchParams(location.search).has('terminal')}};
const SPAWN_DEFAULT_ENGINES=['claude','codex','cursor','antigravity','kilo','hermes','kimi','opencode','devin','grok'],WORKER_DEFAULT_ENGINES=SPAWN_DEFAULT_ENGINES.concat(['aider','droid','pi']);
let spawnDefaultsState={engine:'claude',models:{claude:'claude-sonnet-4-6',codex:'gpt-5-codex'},disabled_engines:[]};
const $convInputEngineSelect=null,$kptToolbarEngineSelect=null;
const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const spawnEngineLabel=e=>e,normalizeSpawnDefaultEngine=e=>e;
window.__toasts=[];function showOpToast(...v){window.__toasts.push(v);}window.__sessions=[];
let currentConversation=null;const nsAdvisor={manual:false,applying:false};
${between('  function markNsAdvisorManual() {','  async function loadNsAdvisor(')}
function setSpawnEngine(engine,opts){if(!opts||opts.record!==false)markNsAdvisorManual();window.__selectedEngine=engine;window.__sessions.push({action:'select',engine});}
function enterNewSessionMode(){if(currentConversation!=='__new__')nsAdvisor.manual=false;currentConversation='__new__';window.__sessions.push({action:'composer'});document.getElementById('composer').hidden=false;}
window.__lateAdvisor=()=>{if(!nsAdvisor.manual)window.__selectedEngine='devin';};
function mergeSpawnDefaults(d){Object.assign(spawnDefaultsState,d);}function syncSpawnEngineDependentUi(){}
function refreshSettingsQueues(){}function renderExperimentalFlags(){}function refreshAppearanceChecks(){}function refreshMonthlyClaudePlanInput(){}function refreshMonthlyCodexPlanInput(){}function refreshSpawnEngineValue(){}function refreshEngineUpdateStatus(){}function refreshByokSettings(){}function refreshVaultSettings(){}function endTailscaleStep(){}
${grouping}
${active}
${search}
${firstRun}
${shell}
${close}
${hub}
${login}
${legacy}
window.cccRoutingPolicy.mount({models:engine=>(engine==='codex'?['gpt-5-codex']:['claude-sonnet-4-6']).map(id=>({id,label:id})),label:e=>e,navigate:()=>setActiveSettingsRailSection('routing'),saved:d=>{mergeSpawnDefaults(d);renderEnginesHub();}});
${mount}
document.getElementById('opener').onclick=()=>window.cccEngineConnection.open();
document.getElementById('settingsModalClose').onclick=closeSettingsModal;
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$settingsModal.hidden)closeSettingsModal();});
window.__refresh=()=>refreshEnginesHub({fresh:true});window.__firstRun=openEnginesFirstRun;window.__legacy=()=>showOnboarding({clis:{}});window.__missing=installMissingEngine;
window.__search=applySettingsSearch;
</script></body></html>`;
let child,browser,url,initialConfig,initialEngines;
const configFile=path.join(scratch,'spawn-defaults.json'),enginesFile=path.join(scratch,'engines.json');
test.before(async()=>{
 fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);
 child=spawn('python3',['-u','tests/fixtures/engine_connection_server.py',scratch],{cwd:root,env:{...process.env,PYTHONPATH:root,CCC_EPHEMERAL:'1'}});
 let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});
 url=(await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw Error(stderr);})])).url;
 initialConfig=fs.readFileSync(configFile,'utf8');initialEngines=fs.readFileSync(enginesFile,'utf8');
 browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Engine connection screenshots:',scratch);});
async function open(t,width=1440,query=''){
 fs.writeFileSync(configFile,initialConfig);fs.writeFileSync(enginesFile,initialEngines);for(const flag of ['fail-status','fail-test','login-done'])fs.rmSync(path.join(scratch,flag),{force:true});
 const context=await browser.createBrowserContext(),page=await context.newPage(),errors=[],posts=[],gets=[];page.setDefaultTimeout(7000);await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));
 await page.setRequestInterception(true);page.on('request',r=>{if(r.method()==='POST')posts.push({url:r.url(),body:r.postData()?JSON.parse(r.postData()):null});else gets.push(r.url());return r.url().startsWith(url)?r.continue():r.abort();});
 t.after(async()=>{await context.close();assert.deepEqual(errors,[]);assert(posts.every(row=>!row.url.includes('/sessions/spawn')));});
 await page.goto(url+'/'+query,{waitUntil:'load'});await page.focus('#opener');await page.keyboard.press('Enter');await page.waitForSelector('#engineConnectionPanel');
 return {page,posts,gets};
}
const choose=(page,engine)=>page.select('#engineConnectionSelect',engine);
const saved=()=>fs.readFileSync(configFile,'utf8');

for(const width of [1440,390])test('one connection flow preserves configuration and distinguishes setup evidence at '+width,async t=>{
 const {page,posts,gets}=await open(t,width);
 assert.deepEqual(posts,[]);assert.equal(saved(),initialConfig);assert.deepEqual(await page.evaluate(()=>window.__sessions),[]);
 assert.equal(await page.$$eval('#engineConnectionSelect',n=>n.length),1);
 assert.equal(await page.$eval('#onboardingModal',n=>n.hidden),true);
 assert.equal(await page.$eval('.eng-hero',n=>!!n.getClientRects().length),false);
 assert.match(await page.$eval('#engineConnectionStatus',n=>n.textContent),/not been validated/);
 assert(!gets.some(u=>u.includes('setup-status')||u.includes('/kimi/verify')));
 const bounds=await page.$eval('#engineConnectionPanel',n=>({scroll:n.scrollWidth,width:n.clientWidth}));assert(bounds.scroll<=bounds.width+1);
 await page.$eval('#engineConnection',n=>n.scrollIntoView({block:'start'}));await page.screenshot({path:path.join(scratch,'setup-'+width+'.png'),fullPage:true});
 await choose(page,'kilo');assert.match(await page.$eval('#engineConnectionPanel',n=>n.textContent),/Sign-in unchecked/);assert.match(await page.$eval('#engineConnectionPanel',n=>n.textContent),/no sign-in probe/);assert.equal(await page.$eval('#engineConnection-session',n=>n.disabled),false);
 await choose(page,'aider');assert.match(await page.$eval('#engineConnectionPanel',n=>n.textContent),/workers only/);assert.equal(await page.$('#engineConnection-session'),null);
});

test('native install action is explicit, keeps the same engine selected and reports host-terminal scope',async t=>{
 const {page,posts}=await open(t,390,'?terminal=1');await choose(page,'codex');assert.equal(await page.$eval('#engineConnection-session',n=>n.disabled),true);
 await page.focus('#engineConnection-install');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#engineConnectionOutcome')?.textContent.includes('Finish there'));
 assert.deepEqual(posts.map(p=>p.body),[{engine:'codex'}]);assert.equal(saved(),initialConfig);
 const state=JSON.parse(initialEngines);state.codex.installed=true;fs.writeFileSync(enginesFile,JSON.stringify(state));
 await page.click('#engineConnectionRefresh');await page.waitForSelector('#engineConnection-login');
 assert.equal(await page.$eval('#engineConnectionSelect',n=>n.value),'codex');assert.match(await page.$eval('#engineConnectionPanel',n=>n.textContent),/Sign-in not detected/);
});

test('host without visible terminal copies installation command, never attempts install or changes defaults',async t=>{
 const {page,posts}=await open(t,390);await page.evaluate(()=>{window.__copied=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.__copied.push(text)},configurable:true});});
 await choose(page,'codex');await page.click('#engineConnection-install');await page.waitForFunction(()=>window.__copied.length===1);
 assert.deepEqual(await page.evaluate(()=>window.__copied),['example-install codex']);assert.deepEqual(posts,[]);assert.equal(saved(),initialConfig);
});

test('native inline authentication stays inside the selected flow and survives status refresh with focus',async t=>{
 const {page,posts}=await open(t,390);const state=JSON.parse(initialEngines);state.codex={installed:true,auth:false};fs.writeFileSync(enginesFile,JSON.stringify(state));
 await page.evaluate(()=>window.__refresh());await choose(page,'codex');await page.click('#engineConnection-login');
 await page.waitForSelector('#engineConnectionLoginHost .onb-login-panel');await page.waitForFunction(()=>document.querySelector('.onb-login-output').textContent.includes('Complete sign-in'));
 await page.focus('[data-role=onb-login-input]');await page.type('[data-role=onb-login-input]','not-a-secret');await page.evaluate(()=>window.__refresh());
 assert.equal(await page.evaluate(()=>document.activeElement?.dataset.role),'onb-login-input');assert.equal(await page.$eval('[data-role=onb-login-input]',n=>n.value),'not-a-secret');
 await page.$eval('[data-role=onb-login-input]',n=>n.value='');
 assert.equal(await page.$eval('.onb-login-hints a',n=>n.href),'https://example.test/auth');
 await page.click('[data-role=onb-login-cancel]');await page.waitForFunction(()=>document.querySelector('[data-role=onb-login-cancel]').disabled);
 assert(posts.some(p=>p.url.endsWith('/login/start')));assert(posts.some(p=>p.url.endsWith('/login/cancel')));assert.equal(saved(),initialConfig);
 await page.screenshot({path:path.join(scratch,'phone-inline-auth.png'),fullPage:true});
});

test('ordinary recheck never starts a live test; explicit Kimi ACP test reports only its actual result',async t=>{
 const {page,posts}=await open(t);await choose(page,'kimi');await page.click('#engineConnection-verify');await page.waitForFunction(()=>document.querySelector('#engineConnectionOutcome')?.textContent.includes('No live connection'));
 assert.deepEqual(posts,[]);fs.writeFileSync(path.join(scratch,'fail-test'),'1');await page.click('#engineConnection-test');await page.waitForFunction(()=>document.querySelector('#engineConnectionOutcome')?.textContent.includes('test failed'));
 assert.match(await page.$eval('#engineConnectionOutcome',n=>n.textContent),/Simulated ACP failure/);fs.rmSync(path.join(scratch,'fail-test'));
 await page.click('#engineConnection-test');await page.waitForFunction(()=>document.querySelector('#engineConnectionOutcome')?.textContent.includes('test succeeded'));
 assert.match(await page.$eval('#engineConnectionOutcome',n=>n.textContent),/remain untested/);assert.equal(posts.length,2);assert(posts.every(p=>p.url.endsWith('/kimi/verify')));assert.equal(saved(),initialConfig);
});

test('defaults use actual staged Launch policy with a keyboard return, never an implicit save',async t=>{
 const {page,posts}=await open(t,390);await choose(page,'codex');await page.focus('#engineConnection-default');await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.includes('Draft only'));
 assert.equal(saved(),initialConfig);assert.deepEqual(posts,[]);
 await page.click('#routingPolicyPreview');await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.startsWith('Validated'));
 assert.equal(saved(),initialConfig);assert.equal(posts.length,1);assert.equal(posts[0].body.policy_action,'preview');
 await page.focus('#engineConnectionPolicyReturn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.activeElement.id==='engineConnectionSelect');
 assert.equal(await page.$eval('#engineConnectionSelect',n=>n.value),'codex');assert.equal(saved(),initialConfig);
});

test('session entry uses native selection and composer only after explicit action, without saving defaults',async t=>{
 const {page,posts}=await open(t,390);await choose(page,'kilo');assert.deepEqual(await page.evaluate(()=>window.__sessions),[]);
 await page.focus('#engineConnection-session');await page.screenshot({path:path.join(scratch,'phone-session-entry.png'),fullPage:true});await page.keyboard.press('Enter');await page.waitForFunction(()=>document.activeElement.id==='convInput');
 assert.deepEqual(await page.evaluate(()=>window.__sessions),[{action:'composer'},{action:'select',engine:'kilo'}]);
 await page.evaluate(()=>window.__lateAdvisor());assert.equal(await page.evaluate(()=>window.__selectedEngine),'kilo');
 assert.equal(await page.$eval('#settingsModal',n=>n.hidden),true);assert.deepEqual(posts,[]);assert.equal(saved(),initialConfig);
});

test('failed doctor response exposes stale snapshot, disables session action and supports retry',async t=>{
 const {page,posts}=await open(t,390);fs.writeFileSync(path.join(scratch,'fail-status'),'1');await page.click('#engineConnectionRefresh');await page.waitForFunction(()=>document.querySelector('#engineConnectionStatus').textContent.includes('not current'));
 assert.equal(await page.$eval('#engineConnection-session',n=>n.disabled),true);assert.deepEqual(posts,[]);await page.screenshot({path:path.join(scratch,'phone-status-failure.png'),fullPage:true});
 fs.rmSync(path.join(scratch,'fail-status'));await page.click('#engineConnectionRefresh');await page.waitForFunction(()=>!document.querySelector('#engineConnection-session').disabled);assert.equal(saved(),initialConfig);
});

test('legacy wizard and missing-engine entry redirect, initial hash preserves return focus on phone',async t=>{
 const {page,posts}=await open(t,390);await page.evaluate(()=>window.__legacy());await page.waitForFunction(()=>document.activeElement.id==='engineConnectionSelect');assert.equal(await page.$eval('#onboardingModal',n=>n.hidden),true);
 await page.evaluate(()=>window.__missing('codex'));await page.waitForFunction(()=>document.querySelector('#engineConnectionSelect').value==='codex');assert.deepEqual(posts,[]);
 await page.goto(url+'/#settings/engines:kilo',{waitUntil:'load'});await page.waitForFunction(()=>document.querySelector('#engineConnectionPanel')?.dataset.engine==='kilo');await page.waitForFunction(()=>document.activeElement.id==='engineConnectionSelect');
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'opener');
 assert.equal(saved(),initialConfig);
});

test('pure assessment never turns unknown sign-in or a missing engine into verified readiness',()=>{
 const vm=require('node:vm'),context={window:{},document:{}};vm.runInNewContext(fs.readFileSync(path.join(root,'static/engine-connection.js'),'utf8'),context);
 const assess=context.window.cccEngineConnection.assessment;
 assert.equal(assess(null).label,'Status unavailable');
 assert.equal(assess({installed:false,auth:true}).session,false);
 assert.equal(assess({installed:true,auth:false}).session,false);
 assert.equal(assess({installed:true,auth:null}).kind,'unchecked');
 assert.equal(assess({installed:true,auth:undefined}).kind,'unchecked');
 assert.equal(assess({installed:true,auth:true,disabled:true}).session,false);
 assert.equal(assess({installed:true,auth:true}).label,'Local setup detected');
});

test('failed native recheck keeps stale state explicit through final rendering',async t=>{
 const {page}=await open(t,390);fs.writeFileSync(path.join(scratch,'fail-status'),'1');
 await page.click('#engineConnection-verify');await page.waitForFunction(()=>document.querySelector('#engineConnectionOutcome')?.textContent.includes('Status check unavailable'));
 assert.match(await page.$eval('#engineConnectionStatus',n=>n.textContent),/not current/);
 assert.equal(await page.$eval('#engineConnection-session',n=>n.disabled),true);
});

test('saved setup details remain open across native status refresh',async t=>{
 const {page}=await open(t);await page.$eval('[data-connection-step=Authenticate]',n=>n.open=true);
 await page.evaluate(()=>window.__refresh());assert.equal(await page.$eval('[data-connection-step=Authenticate]',n=>n.open),true);
});

test('first-run uses the canonical defaults action and returns to setup without losing its policy draft',async t=>{
 const {page,posts}=await open(t,390);await page.evaluate(()=>window.__firstRun());await page.waitForFunction(()=>document.activeElement.id==='engineConnectionSelect');
 assert.equal(await page.$eval('#engFirstRunDefaults',n=>!!n.getClientRects().length),false);
 await choose(page,'codex');await page.click('#engineConnection-default');await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.includes('Draft only'));
 assert.equal(await page.$eval('#spawnDefaultsEngine',n=>n.value),'codex');
 await page.focus('#engineConnectionPolicyReturn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.activeElement.id==='engineConnectionSelect');
 assert.equal(await page.$eval('#engineConnectionSelect',n=>n.value),'codex');assert.equal(saved(),initialConfig);assert.deepEqual(posts,[]);
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'opener');
});

test('the actual welcome shell links to canonical engine setup without running its installer',async t=>{
 const {page,posts}=await open(t,390);
 await page.addStyleTag({url:url+'/static/onboarding/onboarding.css'});
 await page.addScriptTag({url:url+'/static/onboarding/onboarding.js'});
 await page.evaluate(()=>window.cccOnboarding.open());
 await page.waitForSelector('#cccMomentZero [data-engine-connection=open]');
 await page.focus('#cccMomentZero [data-engine-connection=open]');await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.activeElement.id==='engineConnectionSelect');
 assert.equal(await page.evaluate(()=>window.cccOnboarding.isOpen()),false);
 assert.deepEqual(posts,[]);assert.deepEqual(await page.evaluate(()=>window.__sessions),[]);assert.equal(saved(),initialConfig);
});

test('keyboard recheck returns focus to its live replacement after the asynchronous check',async t=>{
 const {page}=await open(t,390);await page.focus('#engineConnection-verify');await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelector('#engineConnectionOutcome')?.textContent.includes('No live connection'));
 await page.waitForFunction(()=>document.activeElement.id==='engineConnection-verify'&&!document.activeElement.disabled);
});

test('native availability action preserves unknown metadata and default handoff is staged with a return path',async t=>{
 const {page,posts}=await open(t,390);await choose(page,'kilo');await page.$eval('.engine-connection-details',n=>n.open=true);
 const savedResponse=page.waitForResponse(r=>r.url()===url+'/api/spawn-defaults'&&r.request().method()==='POST');
 await page.focus('#engineConnectionAvailability');await page.keyboard.press('Enter');await (await savedResponse).json();await page.waitForFunction(()=>document.querySelector('#engineConnectionAvailability').getAttribute('aria-checked')==='false');
 await page.waitForFunction(()=>document.querySelector('#engineConnectionPanel').textContent.includes('Disabled in CCC'));
 const raw=JSON.parse(saved());assert.deepEqual(raw.future_policy,{keep:true});assert.equal(raw.engine,'claude');assert.deepEqual(raw.disabled_engines,['kilo']);
 assert.equal(posts.length,1);assert.deepEqual(posts[0].body,{disabled_engines:['kilo']});
 await choose(page,'claude');await page.$eval('.engine-connection-details',n=>n.open=true);await page.click('#engineConnectionAvailability');
 await page.waitForFunction(()=>document.querySelector('#routingPolicyStatus').textContent.includes('Draft only'));
 assert.equal(posts.length,1);assert.equal(JSON.parse(saved()).engine,'claude');
 await page.click('#engineConnectionPolicyReturn');await page.waitForFunction(()=>document.activeElement.id==='engineConnectionSelect');
 assert.equal(await page.$eval('#engineConnectionSelect',n=>n.value),'claude');
});
