'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'settings-capabilities-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
function between(a,b){const i=app.indexOf(a),j=app.indexOf(b,i+a.length);assert(i>=0&&j>i,a);return app.slice(i,j);}
const grouping=between('  function organizeSettings() {','  function isSettingsFormField(').split('  organizeSettings();')[0]+'  organizeSettings();';
const active=between('  function setActiveSettingsRailSection(','  function ensureSettingsRowCrumb(');
const search=between('  function ensureSettingsRowCrumb(','  function settingsResetAppearance(');
const shell=between('  let settingsSearchFocusTimer;','  // Deep link:');
const close=between('  function closeSettingsModal() {','  // Exposed for call sites');
const deepLink=between('  // Deep link:','  // ── First-run engines screen');
const flags=between('  function renderExperimentalFlags() {','  let repoAvatarRefreshSeq');
const ffClick=between("      const ffToggle = e.target.closest('[data-ff-toggle]');",'      const heroOpenBtn');
const vars=between("  const $voiceModeBtn = document.getElementById('voiceModeBtn');",'  const VOICE_HEARTBEAT_MS');
const prefs=between('  const VOICE_MOBILE_BTN_STORAGE_KEY =','  function applyTheme(');
const config=between('  function voiceModalError(', '  // -- wiring');
const access=between("  const $voiceCallBtnToggle =",'  if ($voicePanelHideBtn)');
const car=between('  // ── Car Mode (hands-free voice operator)', '  // ── Voice mode (Codex realtime)');
const tts=between('  const TTS_VOICE_DEFAULT_KEY =','  renderAllConversationBackgroundPalettes();');
const wiring=between('  if ($voiceSettingsBtn) $voiceSettingsBtn.addEventListener','  // Tab close/navigation:');
const voiceMount=between('  window.cccVoiceSetup?.mount({','  window.cccEngineConnection?.mount({');
const capMount=between('  window.cccSettingsCapabilities?.mount({','  window.cccSettingsContext?.mount({');
const voicePanel=between('  function voiceOpenPanel() {','  function voiceHidePanel() {');
const phoneButton=between("  const $phoneAccessBtn = document.getElementById('phoneAccessBtn');",'  if ($networkBackdrop)');
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/voice-setup.css"><link rel="stylesheet" href="/static/settings-capabilities.css"><style>body{display:block}#settingsModal .settings-modal{width:min(1100px,96vw)}#settingsPane>.settings-section:not(.is-active-section){display:none!important}#settingsPane>.settings-section.is-active-section{display:block}#settingsBtn{display:none}.is-searching #settingsPane>.settings-section:not(.settings-search-miss){display:block!important}</style><button id="opener" data-mobile-nav-menu>Capabilities</button><button id="settingsBtn" hidden>Settings</button><div id="settingsActionsTools"></div><script src="/static/settings-capabilities.js"></script><script src="/static/voice-setup.js"></script><script src="/static/phone-access.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');for(const id of ['settingsModal','voiceModal','carModeModal','voiceMobileBtn','voicePanel'])document.body.append(parsed.getElementById(id));
const $settingsModal=document.getElementById('settingsModal'),$settingsModalInner=$settingsModal.querySelector('.settings-modal'),$settingsPane=document.getElementById('settingsPane'),$settingsRail=document.getElementById('settingsRail'),$settingsSearchInput=document.getElementById('settingsSearchInput'),$settingsBtn=document.getElementById('settingsBtn'),$settingsEmptyState=document.getElementById('settingsEmptyState'),$settingsEmptyQuery=document.getElementById('settingsEmptyQuery');
let _settingsSearchIndex=null,_settingsModalPrevFocus=null,_settingsCurrentSection='capabilities';
const _bootUrlParams=new URLSearchParams(location.search),APP_CONFIG={};const escapeHtml=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function refreshSettingsQueues(){}function startEnginesHub(){}function refreshAppearanceChecks(){}function refreshMonthlyClaudePlanInput(){}function refreshMonthlyCodexPlanInput(){}function refreshSpawnEngineValue(){}function refreshEngineUpdateStatus(){}function refreshByokSettings(){}function refreshVaultSettings(){}function endTailscaleStep(){}function finishEnginesFirstRun(){}function showSettingsSavedPulse(){}
let currentConversation=null;function _normalizeModelId(v){return v;}function paneConversationId(){return null;}function showOpToast(){}
window.__forbidden=[];Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia(){window.__forbidden.push('mic');throw Error('No media');}},configurable:true});window.SpeechRecognition=class{constructor(){window.__forbidden.push('recognition');throw Error('No speech');}};if(window.speechSynthesis)window.speechSynthesis.speak=()=>window.__forbidden.push('playback');
${vars}
${prefs}
${config}
${access}
${car}
${tts}
${wiring}
${voicePanel}
${grouping}
${active}
${search}
${flags}
${shell}
${close}
${phoneButton}
${voiceMount}
${capMount}
${deepLink}
$settingsRail.addEventListener('click',event=>{const tab=event.target.closest('[data-section-target]');if(tab)setActiveSettingsRailSection(tab.dataset.sectionTarget);});
$settingsModal.addEventListener('click',e=>{${ffClick}});
document.getElementById('opener').onclick=()=>{openSettingsModal({focusSearch:false});setActiveSettingsRailSection('capabilities');const fold=document.querySelector('#settingsSection-capabilities>details');if(fold)fold.open=true;};
document.getElementById('settingsModalClose').onclick=closeSettingsModal;document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$settingsModal.hidden)closeSettingsModal();});
window.__navigate=setActiveSettingsRailSection;window.__search=applySettingsSearch;
fetch('/api/features').then(r=>r.json()).then(d=>{window.__CCC_FLAGS__=d.preview;window.__CCC_FLAGS_META__=d.preview_meta;window.dispatchEvent(new CustomEvent('ccc:flags'));});
</script><script src="/static/free-settings.js"></script>`;
let child,browser,url;
const markers=['voice/voice.json','network.json','operator-config.json','phone.json','router.json','flag-on','router-on','operator-on','fail-inventory','fail-save','fail-flag'];
const saved=()=>Object.fromEntries(markers.filter(p=>fs.existsSync(path.join(scratch,p))).map(p=>[p,fs.readFileSync(path.join(scratch,p),'utf8')]));
test.before(async()=>{fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);child=spawn('python3',['-u','tests/fixtures/settings_capabilities_server.py',scratch],{cwd:root,env:{...process.env,PYTHONPATH:root,CCC_EPHEMERAL:'1'}});let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});url=(await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw Error(stderr);})])).url;browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Capability screenshots:',scratch);});
async function open(t,width=1440,{query='',configured=false,fail=false,browserPref=false}={}){
 for(const p of markers)fs.rmSync(path.join(scratch,p),{force:true});fs.mkdirSync(path.join(scratch,'voice'),{recursive:true});
 if(configured)for(const p of ['voice/voice.json','network.json','operator-config.json','phone.json','router.json'])fs.writeFileSync(path.join(scratch,p),'{}');if(fail)fs.writeFileSync(path.join(scratch,'fail-inventory'),'1');
 const before=saved(),context=await browser.createBrowserContext(),page=await context.newPage(),errors=[],requests=[];page.setDefaultTimeout(7000);await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));
 await page.setRequestInterception(true);page.on('request',r=>{requests.push({url:r.url(),method:r.method(),body:r.postData()});return r.url().startsWith(url)?r.continue():r.abort();});if(browserPref)await page.evaluateOnNewDocument(()=>localStorage.setItem('ccc-voice-call-button','0'));
 t.after(async()=>{await context.close();assert.deepEqual(errors,[]);});
 await page.goto(url+'/'+query,{waitUntil:'load'});if(!query){await page.focus('#opener');await page.keyboard.press('Enter');}
 await page.waitForFunction(()=>!document.querySelector('#capabilityInventoryStatus').textContent.includes('Reading'));await page.waitForSelector('#settingsSection-free [data-capability-body="free"]');
 return {page,requests,before};
}
const posts=rs=>rs.filter(r=>r.method==='POST');
for(const width of [1440,390])test('fresh optional setup is gated, runtime unknown is honest and inventory has no side effects '+width,async t=>{
 const {page,requests,before}=await open(t,width);for(const id of ['voice','remote','free'])assert.equal(await page.$eval('[data-capability-body="'+id+'"]',n=>n.hidden),true);
 assert.match(await page.$eval('#capability-voice',n=>n.textContent),/Off.*No saved setup/);assert.match(await page.$eval('#capability-remote',n=>n.textContent),/Unknown.*No saved setup/);assert.match(await page.$eval('#capability-operator',n=>n.textContent),/Unavailable/);
 assert.deepEqual(posts(requests),[]);assert.deepEqual(saved(),before);assert(!requests.some(r=>r.url.includes('/api/free-settings/')||r.url.includes('/api/free-router/status')||r.url.includes('/api/phone-access/status')||r.url.includes('/api/voice/config')));
 assert.equal(await page.$eval('#settingsCapabilities',n=>n.scrollWidth<=n.clientWidth+1),true);await page.screenshot({path:path.join(scratch,'catalog-'+width+'.png'),fullPage:true});
});
test('voice opt-in opens canonical setup, labels shortcut visibility and does not start media or change runtime',async t=>{
 const {page,requests,before}=await open(t,390);await page.focus('#capability-voice button');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.activeElement.id==='voiceSetup-listening');
 assert.equal(await page.$eval('[data-capability-body="voice"]',n=>n.hidden),false);assert.equal(await page.$$eval('#voiceSelect',n=>n.length),1);assert.match(await page.$eval('#voiceSetup-access',n=>n.textContent),/shortcut/);assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);assert.deepEqual(posts(requests),[]);assert.deepEqual(saved(),before);
 await page.screenshot({path:path.join(scratch,'phone-voice-setup.png'),fullPage:true});await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'opener');
});
test('configured setups and explicit saved off browser preferences remain reachable',async t=>{
 const {page,requests,before}=await open(t,390,{configured:true});for(const id of ['voice','remote','free'])assert.equal(await page.$eval('[data-capability-body="'+id+'"]',n=>n.hidden),false);
 assert.match(await page.$eval('#capability-free',n=>n.textContent),/Unknown.*Saved setup/);assert.match(await page.$eval('#capability-remote button',n=>n.textContent),/Manage/);assert.deepEqual(posts(requests),[]);assert.deepEqual(saved(),before);
});
test('saved off shortcut is configured, without claiming voice is disabled',async t=>{
 const {page}=await open(t,390,{browserPref:true});assert.equal(await page.$eval('[data-capability-body="voice"]',n=>n.hidden),false);assert.match(await page.$eval('#capability-voice',n=>n.textContent),/no global voice switch/);
});
test('failed inventory preserves access and retry restores truthful metadata',async t=>{
 const {page,requests}=await open(t,390,{configured:true,fail:true});for(const id of ['voice','remote','free'])assert.equal(await page.$eval('[data-capability-body="'+id+'"]',n=>n.hidden),false);assert.match(await page.$eval('#capabilityInventoryStatus',n=>n.textContent),/unavailable.*accessible/);
 await page.screenshot({path:path.join(scratch,'phone-inventory-unavailable.png'),fullPage:true});fs.rmSync(path.join(scratch,'fail-inventory'));await page.click('#capabilityRefresh');await page.waitForFunction(()=>document.querySelector('#capabilityInventoryStatus').textContent.includes('Metadata snapshot'));assert.deepEqual(posts(requests),[]);
});
for(const section of ['voice','network','free'])test('legacy Settings URL '+section+' deliberately opens its canonical configuration',async t=>{
 const {page,requests}=await open(t,390,{query:'?ccc_settings='+section});const id={voice:'voice',network:'remote',free:'free'}[section];assert.equal(await page.$eval('[data-capability-body="'+id+'"]',n=>n.hidden),false);assert.deepEqual(posts(requests),[]);
});
test('free routing native Stop remains reachable and leaves saved configuration intact',async t=>{
 const {page,requests}=await open(t,390,{configured:true});fs.writeFileSync(path.join(scratch,'router-on'),'1');await page.click('#capability-free button');await page.waitForSelector('#fsStopBtn');await page.focus('#fsStopBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#fsStartBtn'));
 assert.equal(await page.evaluate(()=>document.activeElement.id),'fsStartBtn');assert.equal(fs.existsSync(path.join(scratch,'router-on')),false);assert.equal(fs.readFileSync(path.join(scratch,'router.json'),'utf8'),'{}');assert.deepEqual(posts(requests).map(r=>new URL(r.url).pathname),['/api/free-router/stop']);
});
test('failed free-router stop stays visible and never claims it stopped',async t=>{
 const {page}=await open(t,390,{configured:true});fs.writeFileSync(path.join(scratch,'router-on'),'1');fs.writeFileSync(path.join(scratch,'fail-save'),'1');await page.click('#capability-free button');await page.waitForSelector('#fsStopBtn');await page.click('#fsStopBtn');await page.waitForFunction(()=>document.querySelector('#fsRoot').textContent.includes('Fixture router action failed'));assert.equal(await page.evaluate(()=>document.activeElement.id),'fsStopBtn');assert.equal(fs.existsSync(path.join(scratch,'router-on')),true);
});
test('opening phone management does not enable a preview flag; configured sharing can be disabled with flag off',async t=>{
 const {page,requests}=await open(t,390,{configured:true});await page.click('#capability-remote button');await page.focus('#phoneAccessBtn');await page.keyboard.press('Enter');await page.waitForSelector('.ccc-phone-backdrop [data-act="disable"]');assert.deepEqual(posts(requests),[]);assert.equal(fs.existsSync(path.join(scratch,'flag-on')),false);
 await page.click('.ccc-phone-backdrop [data-act="disable"]');await page.waitForSelector('.ccc-phone-backdrop [data-act="enable"]');assert.equal(fs.existsSync(path.join(scratch,'phone.json')),false);assert.equal(fs.existsSync(path.join(scratch,'flag-on')),false);assert.deepEqual(posts(requests).map(r=>new URL(r.url).pathname),['/api/phone-access/disable']);
});
test('explicit phone enable enables its gate only at the actual action, failures keep sharing off',async t=>{
 const {page,requests}=await open(t,390);await page.click('#capability-remote button');await page.click('#phoneAccessBtn');await page.waitForSelector('.ccc-phone-backdrop [data-act="enable"]');assert.deepEqual(posts(requests),[]);
 fs.writeFileSync(path.join(scratch,'fail-flag'),'1');await page.click('.ccc-phone-backdrop [data-act="enable"]');await page.waitForFunction(()=>document.querySelector('[data-role="serve-msg"]').textContent.includes('Could not enable'));
 assert.equal(fs.existsSync(path.join(scratch,'phone.json')),false);assert.deepEqual(posts(requests).map(r=>new URL(r.url).pathname),['/api/features/flag']);
});
test('preview flag rejected save keeps original gate and reports error without pretending runtime stopped',async t=>{
 const {page,requests}=await open(t,390);fs.writeFileSync(path.join(scratch,'fail-flag'),'1');await page.click('#capability-previews button');await page.focus('[data-ff-toggle="phone_access"]');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('[data-ff-status="phone_access"]').textContent.includes('Not saved'));
 assert.equal(await page.$eval('[data-ff-toggle="phone_access"]',n=>n.getAttribute('aria-checked')),'false');assert.equal(fs.existsSync(path.join(scratch,'flag-on')),false);assert.deepEqual(posts(requests).map(r=>new URL(r.url).pathname),['/api/features/flag']);assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('data-ff-toggle')),'phone_access');
});
test('actual Turn on sequences gate then sharing with only inert fixture effects',async t=>{
 const {page,requests}=await open(t,390);await page.click('#capability-remote button');await page.click('#phoneAccessBtn');await page.waitForSelector('.ccc-phone-backdrop [data-act="enable"]');assert.deepEqual(posts(requests),[]);
 await page.click('.ccc-phone-backdrop [data-act="enable"]');await page.waitForSelector('.ccc-phone-backdrop [data-act="disable"]');await page.waitForFunction(()=>document.querySelector('[data-role="test-msg"]').textContent.includes('Inert fixture'));
 assert.deepEqual(posts(requests).map(r=>new URL(r.url).pathname),['/api/features/flag','/api/phone-access/enable','/api/phone-access/test']);assert.equal(fs.existsSync(path.join(scratch,'flag-on')),true);assert.equal(fs.existsSync(path.join(scratch,'phone.json')),true);
});
test('call controls open without starting a call or microphone and put focus on native Stop',async t=>{
 const {page,requests}=await open(t,390);await page.focus('#capability-voice .capability-actions button:last-child');await page.keyboard.press('Enter');
 assert.equal(await page.$eval('#settingsModal',n=>n.hidden),true);assert.equal(await page.$eval('#voicePanel',n=>n.hidden),false);assert.equal(await page.evaluate(()=>document.activeElement.id),'voicePanelStopBtn');assert.deepEqual(posts(requests),[]);assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);
});
test('voice legacy task hash reveals setup without activation or changes',async t=>{
 const {page,requests,before}=await open(t,390,{query:'#settings/voice-offline'});await page.waitForFunction(()=>document.activeElement.id==='voiceSetup-offline');assert.equal(await page.$eval('[data-capability-body="voice"]',n=>n.hidden),false);assert.deepEqual(posts(requests),[]);assert.deepEqual(saved(),before);
});
test('native browser shortcut remains a visibility control and writes only its existing preference keys',async t=>{
 const {page,requests}=await open(t,390);await page.click('#capability-voice button');await page.waitForFunction(()=>document.activeElement.id==='voiceSetup-listening');
 await page.click('#settingsVoiceCallBtnToggle');assert.deepEqual(await page.evaluate(()=>Object.fromEntries(Object.keys(localStorage).map(k=>[k,localStorage.getItem(k)]))),{'ccc-voice-call-button':'1','ccc-voice-mobile-btn':'1'});assert.match(await page.$eval('#voiceSetupAccessStatus',n=>n.textContent),/visibility flags.*no call is started/is);assert.deepEqual(posts(requests),[]);assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);
});
for(const failure of [false,true])test('hands-free native Stop preserves keys and reports '+(failure?'failed':'successful')+' requests',async t=>{
 const {page,requests}=await open(t,390,{configured:true});fs.writeFileSync(path.join(scratch,'operator-on'),'1');if(failure)fs.writeFileSync(path.join(scratch,'fail-save'),'1');
 await page.focus('#capability-operator button');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#voiceSetup-operator').open&&!document.querySelector('#carModeStopBtn').disabled);await page.focus('#carModeStopBtn');await page.keyboard.press('Enter');
 if(failure){await page.waitForFunction(()=>document.querySelector('#carModeError').textContent.includes('Fixture operator stop failed'));assert.equal(fs.existsSync(path.join(scratch,'operator-on')),true);}else{await page.waitForFunction(()=>document.querySelector('#carModeStopBtn').style.display==='none');assert.equal(fs.existsSync(path.join(scratch,'operator-on')),false);}
 assert.equal(fs.readFileSync(path.join(scratch,'operator-config.json'),'utf8'),'{}');assert.deepEqual(posts(requests).map(r=>new URL(r.url).pathname),['/api/car-mode/stop']);assert.equal(await page.$eval('#carModeAnthropicKey',n=>n.value),'');
});
test('failed phone disable retains saved sharing and the native error is visible',async t=>{
 const {page}=await open(t,390,{configured:true});fs.writeFileSync(path.join(scratch,'fail-save'),'1');await page.click('#capability-remote button');await page.click('#phoneAccessBtn');await page.waitForSelector('.ccc-phone-backdrop [data-act="disable"]');await page.click('.ccc-phone-backdrop [data-act="disable"]');await page.waitForFunction(()=>document.querySelector('[data-role="serve-msg"]').textContent.includes('Fixture sharing change failed'));assert.equal(fs.existsSync(path.join(scratch,'phone.json')),true);
});
test('search for hidden voice configuration offers setup, clears filtering and opens canonical controls',async t=>{
 const {page,requests}=await open(t,390);await page.evaluate(()=>window.__search('realtime'));await page.waitForSelector('#settingsSection-voice .capability-gate button',{visible:true});await page.click('#settingsSection-voice .capability-gate button');
 assert.equal(await page.$eval('[data-capability-body="voice"]',n=>n.hidden),false);assert.equal(await page.$eval('#settingsModal .settings-modal',n=>n.classList.contains('is-searching')),false);assert.deepEqual(posts(requests),[]);
});
test('unknown operator configuration cannot hide the existing voice manager',async t=>{
 const {page,requests}=await open(t,390);await page.evaluate(async()=>{
  const native=window.fetch;window.fetch=async(...args)=>{const result=await native(...args);if(args[0]!=='/api/settings/capabilities')return result;const data=await result.json();data.capabilities.operator.configured=null;return new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});};await window.cccSettingsCapabilities.refresh();
 });assert.equal(await page.$eval('[data-capability-body="voice"]',n=>n.hidden),false);assert.deepEqual(posts(requests),[]);
});
test('confirmed preview save updates its actual gate and preserves configured remote setup',async t=>{
 const {page,requests}=await open(t,390,{configured:true});await page.click('#capability-previews button');await page.focus('[data-ff-toggle="phone_access"]');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('[data-ff-toggle="phone_access"]').getAttribute('aria-checked')==='true');
 assert.equal(fs.existsSync(path.join(scratch,'flag-on')),true);assert.equal(fs.readFileSync(path.join(scratch,'phone.json'),'utf8'),'{}');assert.match(await page.$eval('#capability-previews [role="status"]',n=>n.textContent),/^On.*not service status/);assert.deepEqual(posts(requests).map(r=>new URL(r.url).pathname),['/api/features/flag']);
});

test('phone dialog owns focus, traps Tab, consumes Escape and restores visible opener',async t=>{
 const{page}=await open(t,390);
 await page.focus('#capability-remote button');await page.evaluate(()=>window.cccPhoneAccess.open());await page.waitForSelector('.ccc-phone-backdrop [data-act="enable"]');
 assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('data-act')),'close');
 await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>!!document.activeElement.closest('.ccc-phone-backdrop')),true);
 await page.keyboard.down('Shift');await page.keyboard.press('Tab');await page.keyboard.up('Shift');assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('data-act')),'close');
 const small=await page.$$eval('#settingsCapabilities summary,.ccc-phone-backdrop button,.ccc-phone-backdrop input',nodes=>nodes.filter(n=>n.getClientRects().length&&n.getBoundingClientRect().height<44).map(n=>n.outerHTML));assert.deepEqual(small,[]);
 await page.keyboard.press('Escape');assert.equal(await page.$eval('#settingsModal',n=>n.hidden),false);assert.equal(await page.evaluate(()=>document.activeElement.matches('#capability-remote button')),true);
});

test('phone explicit enable preserves action focus across body replacement',async t=>{
 const{page}=await open(t,390);await page.focus('#capability-remote button');await page.evaluate(()=>window.cccPhoneAccess.open());await page.waitForSelector('.ccc-phone-backdrop [data-act="enable"]');
 await page.focus('.ccc-phone-backdrop [data-act="enable"]');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('[data-role="test-msg"]')?.textContent.includes('Inert fixture'));
 assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('data-act')),'disable');
 await page.focus('.ccc-phone-backdrop [data-act="close"]');await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>document.activeElement.matches('#capability-remote button')),true);
});

test('free Stop settlement preserves a deliberate focus move outside its renderer',async t=>{
 const{page}=await open(t,390,{configured:true});fs.writeFileSync(path.join(scratch,'router-on'),'1');await page.click('#capability-free button');await page.waitForSelector('#fsStopBtn');
 await page.evaluate(()=>{const native=window.fetch;window.fetch=async(...args)=>{if(args[0]==='/api/free-router/stop')await new Promise(resolve=>window.__releaseStop=resolve);return native(...args);};});
 await page.focus('#fsStopBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>window.__releaseStop);await page.focus('#settingsSearchInput');await page.evaluate(()=>window.__releaseStop());await page.waitForSelector('#fsStartBtn');
 assert.equal(await page.evaluate(()=>document.activeElement.id),'settingsSearchInput');
});

test('phone close skips an occluded desktop fallback and focuses native More',async t=>{
 const{page}=await open(t,390);
 await page.evaluate(()=>{
  document.getElementById('settingsModal').hidden=true;
  const desktop=document.getElementById('settingsBtn');desktop.hidden=false;desktop.style.cssText='display:block!important;position:fixed;left:20px;top:20px;width:44px;height:44px;z-index:1';
  const more=document.createElement('button');more.dataset.mobileNavMenu='';more.textContent='More';more.style.cssText='position:fixed;left:20px;top:20px;width:44px;height:44px;z-index:2';document.body.append(more);
  desktop.focus();window.cccPhoneAccess.open();
 });
 await page.waitForSelector('.ccc-phone-backdrop [data-act="enable"]');await page.keyboard.press('Escape');
 assert.equal(await page.evaluate(()=>document.activeElement.matches('[data-mobile-nav-menu]')),true);
 assert.equal(await page.evaluate(()=>{const n=document.activeElement,r=n.getBoundingClientRect(),top=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return top===n||n.contains(top);}),true);
});
