'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'service-connections-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
function between(a,b){const i=app.indexOf(a),j=app.indexOf(b,i+a.length);assert(i>=0&&j>i,a);return app.slice(i,j);}
const grouping=between('  function organizeSettings() {','  function isSettingsFormField(').split('  organizeSettings();')[0]+'  organizeSettings();';
const native=between('  let _byokProviders = {};','  // ✓ Saved pulse');
const active=between('  function setActiveSettingsRailSection(','  function ensureSettingsRowCrumb(');
const search=between('  function ensureSettingsRowCrumb(','  function settingsResetAppearance(');
const shell=between('  let settingsSearchFocusTimer;','  // Deep link:');
const close=between('  function closeSettingsModal() {','  // Exposed for call sites');
const deepLink=between('  // Deep link:','  // ── First-run engines screen');
const mount=between('  window.cccServiceConnections?.mount({','  window.cccSettingsContext?.mount({');
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/service-connections.css"><style>body{display:block}#settingsModal .settings-modal{width:min(1100px,96vw)}#settingsPane>.settings-section:not(.is-active-section){display:none!important}#settingsPane>.settings-section.is-active-section{display:block}#settingsBtn{display:none}</style><button id="opener" data-mobile-nav-menu>Connections</button><button id="settingsBtn" hidden>Settings</button><script src="/static/service-connections.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');document.body.append(parsed.getElementById('settingsModal'));
const $settingsModal=document.getElementById('settingsModal'),$settingsModalInner=$settingsModal.querySelector('.settings-modal'),$settingsPane=document.getElementById('settingsPane'),$settingsRail=document.getElementById('settingsRail'),$settingsSearchInput=document.getElementById('settingsSearchInput'),$settingsBtn=document.getElementById('settingsBtn'),$settingsEmptyState=document.getElementById('settingsEmptyState'),$settingsEmptyQuery=document.getElementById('settingsEmptyQuery');
let _settingsSearchIndex=null,_settingsModalPrevFocus=null,_settingsCurrentSection='vault';
const _bootUrlParams=new URLSearchParams(location.search);const APP_CONFIG={};const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function refreshJevRepoPromo(){}function showSettingsSavedPulse(){}function refreshSettingsQueues(){}function startEnginesHub(){}function renderExperimentalFlags(){}function refreshAppearanceChecks(){}function refreshMonthlyClaudePlanInput(){}function refreshMonthlyCodexPlanInput(){}function refreshSpawnEngineValue(){}function refreshEngineUpdateStatus(){}function endTailscaleStep(){}function finishEnginesFirstRun(){}
${native}
${grouping}
${active}
${search}
${shell}
${close}
${mount}
${deepLink}
$settingsRail.addEventListener('click',event=>{const tab=event.target.closest('[data-section-target]');if(tab)setActiveSettingsRailSection(tab.dataset.sectionTarget);});
document.getElementById('opener').onclick=()=>{openSettingsModal({focusSearch:false});setActiveSettingsRailSection(new URLSearchParams(location.search).get('legacy')||'vault');};
document.getElementById('settingsModalClose').onclick=closeSettingsModal;
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$settingsModal.hidden)closeSettingsModal();});
window.__search=applySettingsSearch;window.__navigate=setActiveSettingsRailSection;
</script>`;
let child,browser,url,initial;
const state=()=>Object.fromEntries(['provider-metadata/index.json','credential-metadata/index.json'].map(p=>[p,fs.readFileSync(path.join(scratch,p),'utf8')]));
test.before(async()=>{
 fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);
 child=spawn('python3',['-u','tests/fixtures/service_connections_server.py',scratch],{cwd:root,env:{...process.env,PYTHONPATH:root,CCC_EPHEMERAL:'1'}});
 let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});
 url=(await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw Error(stderr);})])).url;
 initial=state();browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Service connection screenshots:',scratch);});
async function open(t,width=1440,query='',before){
 for(const [p,s]of Object.entries(initial))fs.writeFileSync(path.join(scratch,p),s);
 for(const p of ['fail-list','fail-save'])fs.rmSync(path.join(scratch,p),{force:true});before?.();
 const context=await browser.createBrowserContext(),page=await context.newPage(),errors=[],requests=[];page.setDefaultTimeout(6000);await page.setViewport({width,height:1000});
 page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);
 page.on('request',r=>{requests.push({url:r.url(),method:r.method(),body:r.postData()});return r.url().startsWith(url)?r.continue():r.abort();});
 t.after(async()=>{await context.close();assert.deepEqual(errors,[]);assert(requests.every(r=>!r.url.includes('/secret/')&&!r.url.includes('/reveal')));});
 await page.goto(url+'/'+query,{waitUntil:'load'});if(!query.includes('ccc_settings=')){await page.focus('#opener');await page.keyboard.press('Enter');}await page.waitForFunction(()=>!document.querySelector('#byokProfilesList').textContent.includes('No profiles yet.'));
 await page.waitForFunction(()=>!document.querySelector('#vaultEntriesList').textContent.includes('Nothing in the vault yet.'));
 return {page,requests};
}
const posts=requests=>requests.filter(r=>r.method==='POST');
for(const width of [1440,390])test('single directory uses native controls without changing metadata at '+width,async t=>{
 const {page,requests}=await open(t,width);assert.deepEqual(state(),initial);assert.deepEqual(posts(requests),[]);
 assert.equal(await page.$eval('#serviceConnectionAdd',n=>n.textContent),'+ Add');assert.equal(await page.$('#serviceConnectionPurpose'),null);assert.equal(await page.$eval('#settingsSection-vault .settings-section-eyebrow',n=>n.textContent),'Vault');
 assert.match(await page.$eval('#byokProfilesList',n=>n.textContent),/example-profile.*OpenAI/s);
 assert.match(await page.$eval('#vaultEntriesList',n=>n.textContent),/example-login/);
 assert.equal(await page.$$eval('#byokProfileInput',n=>n.length),1);assert.equal(await page.$$eval('#vaultValueInput',n=>n.length),1);
 assert.equal(await page.$eval('#settingsSection-api-keys',n=>!!n.getClientRects().length),false);
 assert.equal(await page.$eval('#serviceCredentialEditor',n=>n.open),false);
 assert.equal(await page.$eval('#byokKeyInput',n=>n.value),'');
 assert.equal(await page.$eval('#serviceConnections',n=>n.scrollWidth<=n.clientWidth+1),true);
 await page.screenshot({path:path.join(scratch,'directory-'+width+'.png'),fullPage:true});
});
test('native provider replacement preserves profile/provider, clears values and never changes defaults',async t=>{
 const {page,requests}=await open(t,390);await page.focus('[data-service-provider="openai"]');await page.keyboard.press('Enter');
 assert.equal(await page.$eval('#byokProfileInput',n=>n.value),'example-profile');assert.equal(await page.$eval('#byokProfileInput',n=>n.readOnly),true);
 assert.equal(await page.$eval('#byokProviderSelect',n=>n.disabled),true);assert.equal(await page.evaluate(()=>document.activeElement.id),'byokKeyInput');
 await page.type('#byokKeyInput','not-a-secret-replacement');await page.focus('#byokSaveKeyBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#byokSaveStatus').textContent.startsWith('Saved'));
 assert.equal(await page.$eval('#byokKeyInput',n=>n.value),'');assert.equal(await page.evaluate(()=>document.activeElement.id),'byokSaveKeyBtn');assert.deepEqual(state(),initial);
 assert.deepEqual(posts(requests).map(r=>[new URL(r.url).pathname,JSON.parse(r.body)]),[['/api/byok/keys',{profile:'example-profile',provider:'openai',key:'not-a-secret-replacement'}]]);
 await page.click('#serviceProviderCancel');assert.equal(await page.evaluate(()=>document.activeElement.id),'serviceConnectionAdd');
});
test('failed provider save stays visible, clears credential and preserves metadata on phone',async t=>{
 const {page,requests}=await open(t,390);fs.writeFileSync(path.join(scratch,'fail-save'),'1');
 await page.click('[data-service-provider="openai"]');await page.type('#byokKeyInput','not-a-secret');await page.click('#byokSaveKeyBtn');
 await page.waitForFunction(()=>document.querySelector('#byokSaveStatus').textContent.includes('Could not save'));
 assert.match(await page.$eval('#byokSaveStatus',n=>n.textContent),/Fixture storage unavailable/);assert.equal(await page.$eval('#byokKeyInput',n=>n.value),'');assert.equal(await page.evaluate(()=>document.activeElement.id),'byokSaveKeyBtn');assert.deepEqual(state(),initial);assert.equal(posts(requests).length,1);
 await page.screenshot({path:path.join(scratch,'phone-failed-provider-save.png'),fullPage:true});
});
test('other service add/edit uses native metadata editor, no value round trip and keeps identity',async t=>{
 const {page,requests}=await open(t,390);await page.click('[data-vault-edit="example-login"]');assert.equal(await page.$eval('#serviceCredentialEditor',n=>n.open),true);
 assert.equal(await page.$eval('#vaultNameInput',n=>n.readOnly),true);assert.equal(await page.$eval('#vaultValueInput',n=>n.value),'');
 await page.$eval('#vaultServiceInput',n=>n.value='Example service updated');await page.click('#vaultSaveBtn');await page.waitForFunction(()=>document.querySelector('#vaultFormStatus').textContent==='Details updated.');
 const request=posts(requests)[0];assert.equal(new URL(request.url).pathname,'/api/vault/entries/update');const data=JSON.parse(request.body);assert.equal(data.name,'example-login');assert.equal('value'in data,false);
 assert.equal(data.service,'Example service updated');assert.equal(await page.$eval('#serviceCredentialEditor',n=>n.open),false);
 await page.click('#serviceConnectionAdd');assert.equal(await page.evaluate(()=>document.activeElement.id),'vaultNameInput');assert.equal(await page.$eval('#vaultNameInput',n=>n.readOnly),false);
});
test('failed named credential save retains metadata and clears value',async t=>{
 const {page}=await open(t,390);fs.writeFileSync(path.join(scratch,'fail-save'),'1');await page.click('#serviceConnectionAdd');
 await page.type('#vaultNameInput','example-new');await page.type('#vaultValueInput','not-a-secret');await page.click('#vaultSaveBtn');await page.waitForFunction(()=>document.querySelector('#vaultFormStatus').textContent.includes('Could not save'));
 assert.equal(await page.$eval('#vaultValueInput',n=>n.value),'');assert.equal(await page.evaluate(()=>document.activeElement.id),'vaultSaveBtn');assert.deepEqual(state(),initial);assert.equal(await page.$eval('#vaultNameInput',n=>n.value),'example-new');
});
for(const legacy of ['vault','api-keys'])test('legacy '+legacy+' links use one directory and keyboard return',async t=>{
 const {page,requests}=await open(t,390,'?ccc_settings='+legacy);
 assert.equal(await page.$eval('#serviceConnections',n=>!!n.getClientRects().length),true);
 if(legacy==='api-keys')assert.equal(await page.evaluate(()=>document.activeElement.id),'byokProfileInput');
 await page.keyboard.press('Escape');await page.waitForFunction(()=>document.querySelector('#settingsModal').hidden);assert.equal(await page.evaluate(()=>document.activeElement.id),'opener');assert.deepEqual(posts(requests),[]);
});
test('empty vault adds with only a name and a secret value and independent provider/general state',async t=>{
 const {page,requests}=await open(t,390,'',()=>{fs.writeFileSync(path.join(scratch,'provider-metadata/index.json'),'{}');fs.writeFileSync(path.join(scratch,'credential-metadata/index.json'),'{"entries":{}}');});
 assert.equal(await page.$eval('#byokProfilesList',n=>n.textContent),'');assert.match(await page.$eval('#vaultEntriesList',n=>n.textContent),/Nothing saved yet/);assert.deepEqual(posts(requests),[]);
 await page.click('#serviceConnectionAdd');assert.equal(await page.evaluate(()=>document.activeElement.id),'vaultNameInput');assert.equal(await page.$$eval('#serviceCredentialEditor input,#serviceCredentialEditor select',ns=>ns.filter(n=>n.getClientRects().length).map(n=>n.id).join()),'vaultNameInput,vaultValueInput');await page.click('#serviceProviderEditor>summary');assert.equal(await page.$eval('#byokProviderSelect',n=>n.disabled),false);
 await page.screenshot({path:path.join(scratch,'phone-empty.png'),fullPage:true});
});
test('unavailable directory distinguishes failure from empty and offers retry',async t=>{
 const {page,requests}=await open(t,390,'',()=>fs.writeFileSync(path.join(scratch,'fail-list'),'1'));
 assert.match(await page.$eval('#byokProfilesList',n=>n.textContent),/unavailable/);assert.match(await page.$eval('#vaultEntriesList',n=>n.textContent),/unavailable/);
 await page.screenshot({path:path.join(scratch,'phone-unavailable.png'),fullPage:true});fs.rmSync(path.join(scratch,'fail-list'));await page.click('#serviceConnectionRefresh');await page.waitForSelector('[data-service-provider="openai"]');assert.deepEqual(posts(requests),[]);
});
test('search finds canonical provider editor without reviving retired duplicate section',async t=>{
 const {page}=await open(t);await page.evaluate(()=>window.__search('provider account'));assert.equal(await page.$eval('#settingsSection-api-keys',n=>!!n.getClientRects().length),false);
 await page.evaluate(()=>window.__search(''));await page.click('#serviceProviderEditor>summary');await page.type('#byokKeyInput','not-a-secret');await page.click('#serviceProviderEditor>summary');await page.waitForFunction(()=>document.querySelector('#byokKeyInput').value==='');
});

test('native removal requires confirmation and preserves other providers on the same profile',async t=>{
 const {page,requests}=await open(t);let dialogs=0;page.on('dialog',async d=>{dialogs++;await d.accept();});
 await page.click('[data-byok-del-provider="openai"]');await page.waitForFunction(()=>!document.querySelector('[data-byok-del-provider="openai"]'));
 assert.equal(dialogs,1);assert.match(await page.$eval('#byokProfilesList',n=>n.textContent),/example-profile.*OpenRouter/s);
 assert.equal(posts(requests).length,1);assert.equal(new URL(posts(requests)[0].url).pathname,'/api/byok/keys/delete');assert.equal(state()['credential-metadata/index.json'],initial['credential-metadata/index.json']);
});
test('failed native removal reports visible error and preserves both providers',async t=>{
 const {page}=await open(t,390);fs.writeFileSync(path.join(scratch,'fail-save'),'1');page.on('dialog',d=>d.accept());
 await page.click('[data-byok-del-provider="openai"]');await page.waitForFunction(()=>document.querySelector('#byokSaveStatus').textContent.includes('Could not remove'));
 assert.equal(await page.$eval('#serviceProviderEditor',n=>n.open),true);assert.deepEqual(state(),initial);
});
test('new provider and named credential saves use their existing stores and no import copies',async t=>{
 const {page,requests}=await open(t,390);await page.click('#serviceProviderEditor>summary');await page.type('#byokProfileInput','example-other');await page.type('#byokKeyInput','not-a-secret');
 await page.click('#byokSaveKeyBtn');await page.waitForFunction(()=>document.querySelector('#byokProfilesList').textContent.includes('example-other'));
 await page.click('#serviceConnectionAdd');await page.type('#vaultNameInput','example-token');await page.type('#vaultValueInput','not-a-secret');
 await page.click('#vaultSaveBtn');await page.waitForFunction(()=>document.querySelector('#vaultEntriesList').textContent.includes('example-token'));
 assert.deepEqual(posts(requests).map(r=>new URL(r.url).pathname),['/api/byok/keys','/api/vault/entries']);
 assert.deepEqual(await page.evaluate(()=>Object.keys(localStorage)),[]);
});
test('closing Settings clears unfinished credentials without a request and returns phone keyboard focus',async t=>{
 const {page,requests}=await open(t,390);await page.click('#serviceProviderEditor>summary');await page.type('#byokKeyInput','not-a-secret');await page.keyboard.press('Escape');
 await page.waitForFunction(()=>document.querySelector('#settingsModal').hidden);assert.equal(await page.$eval('#byokKeyInput',n=>n.value),'');assert.equal(await page.evaluate(()=>document.activeElement.id),'opener');assert.deepEqual(posts(requests),[]);
});
test('existing explicit copy capability only opens the canonical editor and explains independent scope',async t=>{
 const {page,requests}=await open(t,390);await page.click('#serviceConnectionCompatibility>summary');await page.click('[data-vault-import="0"]');
 assert.equal(await page.$eval('#serviceCredentialEditor',n=>n.open),true);assert.equal(await page.$eval('#vaultValueInput',n=>n.hidden),true);
 assert.match(await page.$eval('#vaultFormStatus',n=>n.textContent),/explicitly creates an independent named copy/);assert.deepEqual(posts(requests),[]);assert.deepEqual(state(),initial);
});
test('unrecognized providers and credential provenance stay intact without exposing stored hints',async t=>{
 const {page,requests}=await open(t,390,'',()=>{
  const providers=JSON.parse(initial['provider-metadata/index.json']);providers['example-profile'].push('future-example-provider');fs.writeFileSync(path.join(scratch,'provider-metadata/index.json'),JSON.stringify(providers));
  const credentials=JSON.parse(initial['credential-metadata/index.json']);Object.assign(credentials.entries['example-login'],{hint:'example-hidden-hint',source:{type:'byok',profile:'example-profile',provider:'openai'}});fs.writeFileSync(path.join(scratch,'credential-metadata/index.json'),JSON.stringify(credentials));
 });
 assert.equal(await page.$eval('[data-service-provider="future-example-provider"]',n=>n.disabled),true);
 assert.doesNotMatch(await page.$eval('#vaultEntriesList',n=>n.textContent),/example-hidden-hint/);
 assert.deepEqual(posts(requests),[]);
});

test('pending provider save does not reclaim focus after a deliberate move',async t=>{
 const {page}=await open(t);await page.click('[data-service-provider="openai"]');await page.type('#byokKeyInput','not-a-secret');
 await page.evaluate(()=>{const native=window.fetch;window.fetch=async(...args)=>{if(args[0]==='/api/byok/keys')await new Promise(resolve=>window.__releaseSave=resolve);return native(...args);};});
 await page.focus('#byokSaveKeyBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>window.__releaseSave);
 await page.focus('#serviceProviderCancel');await page.evaluate(()=>window.__releaseSave());await page.waitForFunction(()=>!document.querySelector('#byokSaveKeyBtn').disabled);
 assert.equal(await page.evaluate(()=>document.activeElement.id),'serviceProviderCancel');
});

test('phone directory and both editors provide44px controls',async t=>{
 const {page}=await open(t,390);
 for(const selector of ['[data-service-provider="openai"]','[data-vault-edit="example-login"]']) {
  await page.click(selector);
  const small=await page.$$eval('#settingsSection-vault button,#settingsSection-vault input:not([type=checkbox]):not([type=radio]),#settingsSection-vault select',nodes=>nodes.filter(n=>n.getClientRects().length&&n.getBoundingClientRect().height<44).map(n=>({id:n.id,height:n.getBoundingClientRect().height})));
  assert.deepEqual(small,[]);
 }
});
