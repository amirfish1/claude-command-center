'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'voice-setup-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
function between(a,b){const start=app.indexOf(a),end=app.indexOf(b,start+a.length);assert(start>=0&&end>start,a);return app.slice(start,end);}
const organize=between('  function organizeSettings() {','  function isSettingsFormField(');
// The grouping helper includes intervening state definitions; keep only its call.
const grouping=organize.slice(0,organize.indexOf('  organizeSettings();')+'  organizeSettings();'.length);
const config=between('  function voiceModalError(', '  // -- wiring');
const vars=between("  const $voiceModeBtn = document.getElementById('voiceModeBtn');",'  const VOICE_HEARTBEAT_MS');
const car=between('  // ── Car Mode (hands-free voice operator)', '  // ── Voice mode (Codex realtime)');
const prefs=between("  const VOICE_MOBILE_BTN_STORAGE_KEY =",'  function applyTheme(');
const access=between("  const $voiceCallBtnToggle =",'  if ($voicePanelHideBtn)');
const tts=between("  const TTS_VOICE_DEFAULT_KEY =",'  renderAllConversationBackgroundPalettes();');
const wiring=between("  if ($voiceSettingsBtn) $voiceSettingsBtn.addEventListener",'  // Tab close/navigation:');
const search=between('  function ensureSettingsRowCrumb(', '  function clearSettingsSearch(');
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/voice-setup.css"><style>body{display:block;overflow:auto}#settingsModal{position:static;display:block!important;background:var(--bg)}#settingsModal .settings-modal-inner{display:block;max-height:none;width:100%;max-width:1100px;margin:auto}#settingsModal .settings-modal-body{display:block}#settingsRail{display:none}#settingsPane{overflow:visible;max-height:none;width:100%;box-sizing:border-box}#settingsPane>.settings-section{display:none!important}#settingsPane>#settingsSection-voice{display:block!important}#settingsPane>[data-section-id=api-keys].fixture-keys{display:block!important}#settingsActionsTools{padding:12px}.voice-setup{overflow-wrap:anywhere}#voiceMobileBtn{display:none!important}</style></head><body><div id="settingsActionsTools"></div><script src="/static/voice-setup.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');for(const id of ['settingsModal','voiceModal','carModeModal','voiceMobileBtn'])document.body.append(parsed.getElementById(id));
const simple=document.createElement('div');simple.id='simpleHolder';simple.append(parsed.getElementById('simpleVoiceMobileRow'));document.body.append(simple);
const $settingsPane=document.getElementById('settingsPane'),$settingsRail=document.getElementById('settingsRail');
let _settingsSearchIndex=null;function refreshSettingsQueues(){}function startEnginesHub(){}function closeSettingsModal(){window.__closed=true;window.cccVoiceSetup?.leave();}
window.__toasts=[];function showOpToast(...v){window.__toasts.push(v);}let currentConversation=null;function _normalizeModelId(v){return v;}function paneConversationId(){return null;}
window.__forbidden=[];Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia(){window.__forbidden.push('microphone');throw new Error('Microphone forbidden');}},configurable:true});
window.SpeechRecognition=class{constructor(){window.__forbidden.push('recognition');throw new Error('Recognition forbidden');}};
if(window.speechSynthesis)window.speechSynthesis.speak=()=>window.__forbidden.push('playback');
const $settingsModalInner=document.querySelector('#settingsModal .settings-modal'),$settingsEmptyState=document.getElementById('settingsEmptyState'),$settingsEmptyQuery=document.getElementById('settingsEmptyQuery');
${grouping}
${search}
window.__search=applySettingsSearch;
${vars}
${prefs}
${config}
${access}
${car}
${tts}
${wiring}
window.__open=part=>window.cccVoiceSetup.open(part);
window.cccVoiceSetup.mount({navigate:()=>{window.__closed=false;document.querySelector('#settingsSection-voice>details').open=true;},load:loadVoiceConfig,loadOperator:fetchCarModeStatus,refreshVoices:refreshTtsVoiceOptions});
window.__refreshTts=refreshTtsVoiceOptions;
</script><script src="/static/local-speech.js"></script></body></html>`;
let child,browser,url,original;const file=path.join(scratch,'voice','voice.json');
test.before(async()=>{fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);child=spawn('python3',['-u','tests/fixtures/voice_setup_server.py',scratch],{cwd:root,env:{...process.env,PYTHONPATH:root,CCC_EPHEMERAL:'1'}});let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});url=(await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw new Error(stderr);})])).url;original=fs.readFileSync(file,'utf8');browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Voice setup screenshots:',scratch);});
async function open(t,width=1440,fragment=''){
 fs.writeFileSync(file,original);for(const flag of ['fail-write','fail-read','fail-catalog'])fs.rmSync(path.join(scratch,flag),{force:true});
 const context=await browser.createBrowserContext(),page=await context.newPage(),errors=[],posts=[];page.setDefaultTimeout(7000);await page.setViewport({width,height:1000});page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);page.on('request',r=>{if(r.method()==='POST')posts.push({url:r.url(),body:r.postData()?JSON.parse(r.postData()):null});return r.url().startsWith(url)?r.continue():r.abort();});
 t.after(async()=>{assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);await context.close();assert.deepEqual(errors,[]);assert(posts.every(p=>p.url===url+'/api/voice/config'));});
 await page.goto(url+'/'+fragment,{waitUntil:'load'});await page.evaluate(()=>window.__open('listening'));await page.waitForFunction(()=>document.querySelector('#voiceSetupCallStatus')?.textContent.includes('Saved call defaults loaded'));return{page,posts};
}
const read=()=>JSON.parse(fs.readFileSync(file,'utf8'));
for(const width of [1440,390])test('one native workflow with truthful input/output scopes and no writes at '+width,async t=>{
 const{page,posts}=await open(t,width);assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(posts,[]);
 for(const id of ['voiceSelect','settingsTtsVoiceDefault','settingsVoiceCallBtnToggle','localSpeechInstall','carModeAnthropicKey'])assert.equal(await page.$$eval('#'+id,n=>n.length),1);
 assert.equal(await page.$eval('#voiceModal',n=>n.hidden),true);assert.equal(await page.$eval('#carModeModal',n=>n.hidden),true);assert.equal(await page.$eval('#settingsSoundsToggle',n=>n.closest('.settings-section').id),'settingsSection-notifications');assert.equal(await page.$eval('#voiceSetupLegacy #settingsVoiceMobileToggle',n=>!!n),true);
 assert.match(await page.$eval('#voiceSetupListeningStatus',n=>n.textContent),/permission.*not been checked/);assert.match(await page.$eval('#voiceSetup-reading',n=>n.textContent),/does not open the microphone/);assert.match(await page.$eval('#voiceSetup-offline',n=>n.textContent),/does not establish offline dictation/);
 await page.$eval('#voiceSetup',n=>n.scrollIntoView({block:'start'}));await page.screenshot({path:path.join(scratch,'setup-'+width+'.png'),fullPage:true});const bounds=await page.$eval('#voiceSetup',n=>({scroll:n.scrollWidth,width:n.clientWidth}));assert(bounds.scroll<=bounds.width+1);
});
test('saved unavailable voice and profile remain selected; no-op Save never writes or changes visibility',async t=>{
 const{page,posts}=await open(t);assert.equal(await page.$eval('#voiceSelect',n=>n.value),'coral');assert.equal(await page.$eval('#voiceProfileSelect',n=>n.value),'Missing profile');assert.match(await page.$eval('#voiceProfileSelect',n=>n.selectedOptions[0].textContent),/key unavailable/);const before=await page.evaluate(()=>({...localStorage}));await page.click('#voiceModalSaveBtn');await page.waitForFunction(()=>document.querySelector('#voiceSetupCallStatus').textContent.includes('No call settings changed'));assert.deepEqual(posts,[]);assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(await page.evaluate(()=>({...localStorage})),before);
});
test('one edited call field saves through actual API without overwriting missing catalog selections',async t=>{
 const{page,posts}=await open(t);await page.$eval('#voiceMaxMinutes',n=>{n.value='30';n.dispatchEvent(new Event('input',{bubbles:true}));});await page.focus('#voiceModalSaveBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#voiceSetupCallStatus').textContent.includes('defaults saved'));assert.deepEqual(posts.map(p=>p.body),[{max_minutes:30}]);assert.equal(read().max_minutes,30);assert.equal(read().voice,'coral');assert.equal(read().profile,'Missing profile');assert.equal(read().show_mobile_btn,false);
});
test('failed save leaves browser preferences and server config unchanged; invalid duration does not post',async t=>{
 const{page,posts}=await open(t,390);await page.$eval('#voiceIdleSeconds',n=>n.value='1');await page.click('#voiceModalSaveBtn');assert.deepEqual(posts,[]);assert.match(await page.$eval('#voiceModalError',n=>n.textContent),/Check/);await page.$eval('#voiceIdleSeconds',n=>n.value='60');fs.writeFileSync(path.join(scratch,'fail-write'),'1');await page.click('#voiceModalSaveBtn');await page.waitForFunction(()=>document.querySelector('#voiceModalError').textContent.includes('Save could not be confirmed'));assert.equal(fs.readFileSync(file,'utf8'),original);assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-voice-mobile-btn')),null);await page.screenshot({path:path.join(scratch,'phone-save-error.png'),fullPage:true});
});
test('call access is one explicit browser action updating both saved gates, independent of server call settings',async t=>{
 const{page,posts}=await open(t,390);await page.evaluate(()=>{localStorage.setItem('ccc-voice-call-button','1');localStorage.setItem('ccc-voice-mobile-btn','0');window.cccVoiceSetup.refreshAccess();});assert.equal(await page.$eval('#settingsVoiceCallBtnToggle',n=>n.getAttribute('aria-checked')),'false');await page.focus('#settingsVoiceCallBtnToggle');await page.keyboard.press('Enter');assert.deepEqual(await page.evaluate(()=>[localStorage.getItem('ccc-voice-call-button'),localStorage.getItem('ccc-voice-mobile-btn')]),['1','1']);assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(posts,[]);await page.click('#settingsVoiceCallBtnToggle');assert.deepEqual(await page.evaluate(()=>[localStorage.getItem('ccc-voice-call-button'),localStorage.getItem('ccc-voice-mobile-btn')]),['0','0']);
});
test('read-aloud selection stays separate from call voice and retains a saved unavailable output voice',async t=>{
 const{page,posts}=await open(t);await page.evaluate(()=>localStorage.setItem('ccc-tts-voice-default','Retained output'));await page.click('#voiceSetupReadingRefresh');await page.waitForFunction(()=>document.querySelector('#settingsTtsVoiceDefault').value==='Retained output');assert.match(await page.$eval('#settingsTtsVoiceDefault',n=>n.selectedOptions[0].textContent),/unavailable/);await page.select('#settingsTtsVoiceDefault','Kokoro: af_heart');assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-tts-voice-default')),'Kokoro: af_heart');assert.equal(await page.$eval('#voiceSelect',n=>n.value),'coral');assert.equal(fs.readFileSync(file,'utf8'),original);assert.deepEqual(posts,[]);
});
test('legacy setup buttons and hash links redirect into actual sections with keyboard focus',async t=>{
 const{page}=await open(t,390);for(const [hash,target] of [['settingsTtsVoiceDefault','reading'],['voiceModal','conversation'],['settings/voice-offline','offline'],['settingsVoiceMobileToggle','access']]){await page.evaluate(h=>{location.hash=h;},hash);await page.waitForFunction(id=>document.activeElement.id===id, {}, 'voiceSetup-'+target);}
 await page.focus('#carModeMenuBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#voiceSetup-operator').open);assert.equal(await page.$eval('#carModeModal',n=>n.classList.contains('open')),false);assert.equal(await page.$eval('#carModeAnthropicKey',n=>n.type),'password');assert.equal(await page.$eval('#carModeAnthropicKey',n=>n.value),'');assert.equal(await page.$eval('#carModeStartBtn',n=>n.disabled),true);assert.match(await page.$eval('#carModeStatusBanner',n=>n.textContent),/launcher is unavailable/);await page.click('#carModeCancelBtn');assert.equal(await page.$eval('#voiceSetup-operator',n=>n.open),false);
 await page.focus('#voiceSettingsBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.activeElement.id==='voiceSetup-conversation');
});
test('offline refresh is read-only and never claims local output makes calls offline',async t=>{
 const{page,posts}=await open(t,390);fs.writeFileSync(path.join(scratch,'offline.json'),JSON.stringify({state:'ready',reason:'',progress:''}));await page.click('#voiceSetupOfflineRefresh');await page.waitForFunction(()=>document.querySelector('#localSpeechStatus').textContent.includes('Ready'));assert.equal(await page.$eval('#localSpeechInstall',n=>n.disabled),true);assert.match(await page.$eval('#voiceSetup-offline',n=>n.textContent),/does not establish offline dictation/);assert.deepEqual(posts,[]);fs.writeFileSync(path.join(scratch,'offline.json'),JSON.stringify({state:'not_installed',reason:'',progress:''}));
});
test('load failure disables Save and retry restores actual saved values',async t=>{
 const{page}=await open(t);fs.writeFileSync(path.join(scratch,'fail-read'),'1');await page.click('#voiceSetupReload');await page.waitForFunction(()=>document.querySelector('#voiceSetupCallStatus').textContent.includes('unavailable'));assert.equal(await page.$eval('#voiceModalSaveBtn',n=>n.disabled),true);assert.equal(fs.readFileSync(file,'utf8'),original);fs.rmSync(path.join(scratch,'fail-read'));await page.click('#voiceSetupReload');await page.waitForFunction(()=>document.querySelector('#voiceSetupCallStatus').textContent.includes('Saved call defaults loaded'));assert.equal(await page.$eval('#voiceSelect',n=>n.value),'coral');
});

test('catalog failure preserves saved output selection and exposes a working retry', async t => {
 const {page,posts}=await open(t);
 await page.evaluate(()=>localStorage.setItem('ccc-tts-voice-default','Retained output'));
 fs.writeFileSync(path.join(scratch,'fail-catalog'),'1');
 await page.click('#voiceSetupReadingRefresh');
 await page.waitForFunction(()=>document.querySelector('#voiceSetupReadingStatus').textContent.includes('catalog unavailable'));
 assert.equal(await page.$eval('#settingsTtsVoiceDefault',n=>n.value),'Retained output');
 fs.rmSync(path.join(scratch,'fail-catalog'));
 await page.click('#voiceSetupReadingRefresh');
 await page.waitForFunction(()=>document.querySelector('#voiceSetupReadingStatus').textContent.includes('advertised'));
 assert.equal(await page.$eval('#settingsTtsVoiceDefault',n=>n.value),'Retained output');
 assert.deepEqual(posts,[]);
});

test('passive listening availability distinguishes absent API, insecure connection and native bridge', async t => {
 const {page,posts}=await open(t,390);
 await page.evaluate(()=>{delete window.SpeechRecognition;delete window.webkitSpeechRecognition;window.cccVoiceSetup.enter();});
 assert.match(await page.$eval('#voiceSetupListeningStatus',n=>n.textContent),/API unavailable/);
 await page.evaluate(()=>{Object.defineProperty(window,'isSecureContext',{value:false,configurable:true});window.cccVoiceSetup.enter();});
 assert.match(await page.$eval('#voiceSetupListeningStatus',n=>n.textContent),/unavailable on this connection/);
 await page.evaluate(()=>{window.Capacitor={Plugins:{SpeechRecognition:{}}};window.cccVoiceSetup.enter();});
 assert.match(await page.$eval('#voiceSetupListeningStatus',n=>n.textContent),/bridge present.*not been checked/);
 assert.deepEqual(posts,[]);
});

test('initial legacy links and Simple setup route to the same native controls', async t => {
 const {page,posts}=await open(t,390);
 await page.goto(url+'/#voiceModal',{waitUntil:'load'});
 await page.waitForFunction(()=>document.activeElement.id==='voiceSetup-conversation');
 await page.focus('#simpleHolder button');await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.activeElement.id==='voiceSetup-access');
 await page.focus('#carModeMenuBtn');await page.keyboard.press('Enter');
 await page.waitForFunction(()=>document.querySelector('#carModeStatusBanner').textContent.includes('launcher is unavailable'));
 await page.screenshot({path:path.join(scratch,'phone-operator-unavailable.png'),fullPage:true});
 assert.equal(await page.$eval('#carModeAnthropicKey',n=>n.value),'');
 assert.deepEqual(posts,[]);
});

test('native Settings search reveals each task with its controls, without reviving duplicate editors', async t => {
 const {page}=await open(t,390);
 for(const [query,id] of [['spoken replies','settingsTtsVoiceDefault'],['fallback profile','voiceProfileSelect'],['offline output','localSpeechInstall'],['floating shortcut','settingsVoiceCallBtnToggle'],['anthropic','carModeAnthropicKey']]){
  await page.evaluate(q=>window.__search(q),query);
  assert.equal(await page.$eval('#'+id,n=>!!n.getClientRects().length),true,query);
  assert.equal(await page.$eval('#settingsVoiceMobileToggle',n=>!!n.getClientRects().length),false);
 }
 await page.evaluate(()=>window.__search(''));
 assert.equal(await page.$eval('#voiceModal',n=>!!n.getClientRects().length),false);
});

test('closing operator or Settings clears unsaved credential fields without sending them', async t => {
 const {page,posts}=await open(t,390);
 await page.evaluate(()=>window.__open('operator'));
 await page.waitForFunction(()=>document.querySelector('#voiceSetup-operator').open);
 await page.$eval('#carModeAnthropicKey',n=>n.value='obvious-test-only');
 await page.click('#carModeCancelBtn');
 await page.waitForFunction(()=>document.querySelector('#carModeAnthropicKey').value==='');
 await page.evaluate(()=>window.__open('operator'));
 await page.waitForFunction(()=>document.querySelector('#voiceSetup-operator').open);
 await page.$eval('#carModeDeepgramKey',n=>n.value='obvious-test-only');
 await page.evaluate(()=>closeSettingsModal());
 assert.equal(await page.$eval('#carModeDeepgramKey',n=>n.value),'');
 assert.deepEqual(posts,[]);
});

test('native Settings shell leaves task focus intact on entry and repeated navigation',async t=>{
 const{page}=await open(t);
 const shell=between('  let settingsSearchFocusTimer;', '  // Deep link:');
 const navigation=between('    navigate: () => {\n      clearTimeout(settingsSearchFocusTimer);','    load: loadVoiceConfig').trim().replace(/,$/,'');
 await page.evaluate(({shell,navigation})=>{
  const setup=new Function('shell','navigation',`
   const $settingsModal=document.getElementById('settingsModal'),$settingsSearchInput=document.getElementById('settingsSearchInput'),$settingsBtn=null;
   let _settingsModalPrevFocus,_settingsCurrentSection='voice';
   const clearSettingsSearch=()=>{},renderExperimentalFlags=()=>{},refreshAppearanceChecks=()=>{},refreshMonthlyClaudePlanInput=()=>{},refreshMonthlyCodexPlanInput=()=>{},refreshSpawnEngineValue=()=>{},refreshEngineUpdateStatus=()=>{},refreshByokSettings=()=>{},refreshVaultSettings=()=>{},buildSettingsSearchIndex=()=>{},setActiveSettingsRailSection=()=>{};
   return eval(shell+';({open:openSettingsModal,'+navigation+'})');`);
  window.__shellFocus=setup(shell,navigation);
 },{shell,navigation});
 for(const state of ['closed','open','pending-search']) {
  await page.evaluate(state=>{
   const modal=document.getElementById('settingsModal');modal.hidden=state==='closed';
   if(state==='pending-search')window.__shellFocus.open();
   window.__shellFocus.navigate();
   requestAnimationFrame(()=>document.getElementById('voiceSetup-conversation').focus());
  },state);
  await page.waitForFunction(()=>document.activeElement.id==='voiceSetup-conversation');
  await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,50)));
  assert.equal(await page.evaluate(()=>document.activeElement.id),'voiceSetup-conversation',state);
 }
});

test('closing Settings skips a hidden desktop trigger and restores the visible phone opener',async t=>{
 const{page}=await open(t,390);
 const close=between('  function closeSettingsModal() {','  // Exposed for call sites');
 const result=await page.evaluate(close=>{
  const settings=document.getElementById('settingsModal');settings.hidden=false;
  const desktop=document.createElement('button');desktop.style.display='none';document.body.append(desktop);
  const opener=document.createElement('button');opener.textContent='Voice setup';document.body.append(opener);
  const run=new Function('$settingsModal','$settingsBtn','_settingsModalPrevFocus','finishEnginesFirstRun',close+';closeSettingsModal();');
  run(settings,desktop,opener,()=>{});
  return {hidden:settings.hidden,restored:document.activeElement===opener};
 },close);
 assert.deepEqual(result,{hidden:true,restored:true});
});
