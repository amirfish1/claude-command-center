/* One setup workflow; native controls, listeners and credential APIs stay owned
 * by app.js. Opening setup never listens, plays audio or starts a call. */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  let hooks, ready = false, entered = false, loading = false;
  const create = (tag, text, cls) => { const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n; };
  function refreshAccess() {
    if(!ready)return;
    let floating=false,mobile=true;
    try{floating=localStorage.getItem('ccc-voice-call-button')==='1';mobile=localStorage.getItem('ccc-voice-mobile-btn')!=='0';}catch(_){}
    const show=floating&&mobile, control=get('settingsVoiceCallBtnToggle');
    control.classList.toggle('is-on',show);control.setAttribute('aria-checked',String(show));
    get('voiceSetupAccessStatus').textContent='';
  }
  function listeningStatus() {
    const native=window.Capacitor?.Plugins?.SpeechRecognition;
    get('voiceSetupListeningStatus').textContent=native
      ? 'Native dictation bridge present. Device support and microphone/speech permissions have not been checked.'
      : window.isSecureContext===false
        ? 'Dictation unavailable on this connection. Use HTTPS or localhost. Microphone permission has not been requested.'
        : window.SpeechRecognition||window.webkitSpeechRecognition
          ? 'Browser dictation API present. Microphone permission and recognition service availability have not been checked.'
          : 'Dictation API unavailable in this browser. Use a browser with speech recognition support. No microphone permission was requested.';
  }
  function mounted(){return ready;}
  function leave(){
    if(!ready)return;
    get('carModeAnthropicKey').value='';get('carModeDeepgramKey').value='';
  }
  function enter() {
    if(window.cccSettingsCapabilities&&!window.cccSettingsCapabilities.configurationVisible('voice'))return;
    if(!ready)return;listeningStatus();refreshAccess();
    if(!entered&&!loading){entered=true;reload();hooks.loadOperator();}
  }
  async function reload(){if(loading)return;loading=true;get('voiceSetupCallStatus').textContent='Reading saved call settings…';try{await hooks.load();}finally{loading=false;}}
  function loaded(data){if(!ready)return;get('voiceSetupCallStatus').textContent='Saved call defaults loaded. Connection and mic checked when you call.';}
  function loadFailed(){if(ready)get('voiceSetupCallStatus').textContent='Call settings unavailable. Reload to retry; saving is disabled. Dictation and read-aloud are separate.';}
  function saved(message){if(ready)get('voiceSetupCallStatus').textContent=message;}
  function open(part='listening'){
    if(!ready)return false;window.cccSettingsCapabilities?.reveal('voice');hooks.navigate();enter();if(part==='operator')hooks.loadOperator();
    const target=get('voiceSetup-'+part)||get('voiceSetup-listening');
    const disclosure=get('settingsSection-voice').querySelector(':scope > details');if(disclosure)disclosure.open=true;
    if(target.tagName==='DETAILS')target.open=true;
    requestAnimationFrame(()=>{target.scrollIntoView({block:'start'});target.focus({preventScroll:true});});return true;
  }
  function hashRoute(){
    let hash;try{hash=decodeURIComponent(location.hash.slice(1));}catch(_){return;}
    const aliases={voice:'listening',voiceModal:'conversation',voiceSettingsBtn:'conversation',carModeModal:'operator',carModeMenuBtn:'operator',settingsBrowserVoiceToggle:'reading',simpleVoiceMobileRow:'access',settingsVoiceMobileToggle:'access',settingsVoiceCallBtnToggle:'access',voiceShowMobileBtn:'access'};
    const id=hash.replace(/^settings[\/-]/,'');const task=id.match(/^voice-(listening|reading|conversation|access|operator)$/);if(task)open(task[1]);else if(aliases[id])open(aliases[id]);
  }
  function mount(integration){
    if(ready)return;
    const root=get('settingsSection-voice');if(!root||!get('voiceModal')||!get('carModeModal'))return;
    hooks=integration;
    const host=create('div',null,'voice-setup');host.id='voiceSetup';
    host.innerHTML=`<section id="voiceSetup-reading" class="voice-setup-section settings-row" data-keywords="voice speak spoken replies read aloud output voice" tabindex="-1"><h3>Read replies aloud</h3><p>Use a reply’s speaker button. Browser voice reads English; the device voice handles other languages and fallback. Choose a speaker beside the conversation.</p></section>
      <section id="voiceSetup-access" class="voice-setup-section settings-row" data-keywords="voice call access shortcut floating mobile" tabindex="-1"><p id="voiceSetupAccessStatus" role="status" aria-live="polite"></p></section>
      <details id="voiceSetup-conversation" class="voice-setup-section settings-row" data-keywords="voice realtime live conversation codex call limits fallback profile" tabindex="-1"><summary>Live conversation</summary><p>Experimental · Uses your ChatGPT subscription. Mic audio goes to OpenAI. Actions require confirmation.</p><p id="voiceSetupCallStatus" role="status" aria-live="polite">Open to load settings.</p><button type="button" id="voiceSetupReload" class="settings-action-btn">Reload settings</button><button type="button" data-settings-go="api-keys" class="settings-action-btn">Provider keys</button></details>
      <details id="voiceSetup-listening" class="voice-setup-section settings-row" data-keywords="voice listening dictation microphone speech input" tabindex="-1"><summary>Dictation</summary><p>Use the composer’s mic to dictate English text, then review and send. May require a network connection.</p><p id="voiceSetupListeningStatus" role="status"></p></details>
      <details id="voiceSetup-operator" class="voice-setup-section settings-row" data-keywords="voice hands free car mode operator credentials local" tabindex="-1"><summary>Car Mode</summary><p>Separate hands-free operator. Requires a paid Anthropic API key. Blank fields keep saved keys.</p></details>`;
    const disclosure=root.querySelector(':scope > details'), destination=disclosure||root;
    destination.append(host);
    const legacy=create('div');legacy.hidden=true;legacy.id='voiceSetupLegacy';host.append(legacy);
    function moveRow(id,target){const element=get(id);if(!element)return;const row=element.closest('.settings-row');if(row)target.append(row);}
    moveRow('settingsTtsEnabledToggle',get('voiceSetup-reading'));moveRow('settingsVoiceCallBtnToggle',get('voiceSetup-access'));
    moveRow('settingsBrowserVoiceToggle',get('voiceSetup-reading'));
    const callToggle=get('settingsVoiceCallBtnToggle');callToggle.setAttribute('aria-label','Show floating call shortcut');
    callToggle.closest('.settings-row').querySelector('.settings-row-label').textContent='Show floating call shortcut';
    callToggle.closest('.settings-row').querySelector('.settings-row-desc').textContent='Show CALL on phone layouts. Saves automatically.';
    moveRow('settingsVoiceMobileToggle',legacy);
    moveRow('settingsSoundsToggle',get('settingsSection-notifications')||host);
    const call=get('voiceSetup-conversation'), configForm=get('voiceSelect').parentElement.parentElement;
    get('voiceSelect').previousElementSibling.textContent='Live conversation voice';
    get('voiceShowMobileBtn').closest('label').hidden=true;
    call.append(get('voiceSetupBanner'),configForm,get('voiceModalError'),get('voiceModalSaveBtn'));
    get('voiceModalError').setAttribute('role','status');get('voiceModalError').setAttribute('aria-live','polite');
    get('voiceModalSaveBtn').textContent='Save call settings';
    legacy.append(get('voiceModalCancelBtn'));
    const operator=get('voiceSetup-operator'), car=get('carModeModal'), carDialog=car.querySelector('.upd-dialog');
    for(const child of [...carDialog.children])if(child.id!=='carModeTitle'&&!child.classList.contains('upd-versions'))operator.append(child);
    get('carModeCancelBtn').hidden=false;
    get('carModeCancelBtn').onclick=()=>{operator.open=false;leave();};
    // Existing openers become setup shortcuts; no second set of editors.
    for(const id of ['voiceSettingsBtn','carModeMenuBtn']){
      const button=get(id);if(!button)continue;const row=button.closest('.settings-row');
      button.textContent=id==='voiceSettingsBtn'?'Voice setup…':'Hands-free operator setup…';button.className='sh-more-item';button.setAttribute('role','menuitem');
      (get('settingsActionsTools')||legacy).append(button);row?.remove();
    }
    for(const id of ['voiceModal','carModeModal']){const old=get(id);old.hidden=true;old.classList.remove('open');old.removeAttribute('role');old.removeAttribute('aria-modal');}
    // Simple settings now links to this setup instead of exposing another gate.
    const simple=get('simpleVoiceMobileRow');if(simple){const go=create('button','Voice setup…','simple-chip');go.type='button';go.addEventListener('click',()=>open('access'));simple.parentElement.append(go);legacy.append(simple);}
    for(const child of [...destination.children])if(child.matches('.settings-section-eyebrow,.settings-section-note'))child.hidden=true;
    get('voiceSetupReload').addEventListener('click',reload);
    host.addEventListener('click',event=>{const target=event.target.closest('[data-voice-part]');if(target){event.preventDefault();open(target.dataset.voicePart);}});
    // Search each task as a unit, keeping its native controls visible together.
    host.querySelectorAll('.voice-setup-section').forEach(section=>{section.setAttribute('data-settings-search-group','');section.dataset.keywords+=' '+section.textContent;});
    legacy.querySelectorAll('.settings-row').forEach(row=>{row.hidden=true;});
    // Older hash links target real controls now placed in this one section.
    window.addEventListener('hashchange',hashRoute);
    ready=true;refreshAccess();listeningStatus();hashRoute();
    operator.addEventListener('toggle',()=>{if(!operator.open)leave();});
    if(disclosure)disclosure.addEventListener('toggle',()=>{if(disclosure.open)enter();});
    if('IntersectionObserver'in window)new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting))enter();}).observe(host);
  }
  window.cccVoiceSetup=Object.freeze({mount,mounted,open,enter,leave,refreshAccess,loaded,loadFailed,saved});
})();
