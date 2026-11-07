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
    get('voiceSetupAccessStatus').textContent='This browser · Floating shortcut '+(floating?'on':'off')+'; mobile visibility '+(mobile?'on':'off')+'. '+(show?'The shortcut can appear on phone layouts.':'The idle shortcut is hidden.')+' Changing this switch updates both saved visibility flags. Header Voice access is separate; no call is started.';
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
    if(!ready)return;get('voiceSetup-operator').open=false;
    get('carModeAnthropicKey').value='';get('carModeDeepgramKey').value='';
  }
  function enter() {
    if(window.cccSettingsCapabilities&&!window.cccSettingsCapabilities.configurationVisible('voice'))return;
    if(!ready)return;listeningStatus();refreshAccess();
    if(!entered&&!loading){entered=true;reload();hooks.refreshVoices();}
  }
  async function reload(){if(loading)return;loading=true;get('voiceSetupCallStatus').textContent='Reading saved call settings…';try{await hooks.load();}finally{loading=false;}}
  function loaded(data){if(!ready)return;get('voiceSetupCallStatus').textContent='This machine · Saved call defaults loaded. Subscription entitlement, microphone and connection are untested. '+(data.openai_profiles?.length?'Paid fallback profiles are listed by name only.':'No paid fallback profile is available.');}
  function loadFailed(){if(ready)get('voiceSetupCallStatus').textContent='Call settings unavailable. Reload to retry; saving is disabled. Dictation and read-aloud are separate.';}
  function saved(message){if(ready)get('voiceSetupCallStatus').textContent=message;}
  function ttsLoaded(options){if(ready)get('voiceSetupReadingStatus').textContent=options.length?'This browser · '+options.length+' output voices advertised by this machine. $ marks paid output. Playback has not been tested.':'No optional output voices advertised. Random voice uses the existing speech fallback; its availability has not been tested.';}
  function ttsFailed(){if(ready)get('voiceSetupReadingStatus').textContent='Output voice catalog unavailable. The saved selection is retained; refresh to retry. Playback has not been tested.';}
  function open(part='listening'){
    if(!ready)return false;window.cccSettingsCapabilities?.reveal('voice');hooks.navigate();enter();
    const target=get('voiceSetup-'+part)||get('voiceSetup-listening');
    const disclosure=get('settingsSection-voice').querySelector(':scope > details');if(disclosure)disclosure.open=true;
    if(target.tagName==='DETAILS')target.open=true;
    requestAnimationFrame(()=>{target.scrollIntoView({block:'start'});target.focus({preventScroll:true});});return true;
  }
  function hashRoute(){
    let hash;try{hash=decodeURIComponent(location.hash.slice(1));}catch(_){return;}
    const aliases={voice:'listening',voiceModal:'conversation',voiceSettingsBtn:'conversation',carModeModal:'operator',carModeMenuBtn:'operator',settingsTtsVoiceDefault:'reading',localSpeechStatus:'offline',localSpeechInstall:'offline',simpleVoiceMobileRow:'access',settingsVoiceMobileToggle:'access',settingsVoiceCallBtnToggle:'access',voiceShowMobileBtn:'access'};
    const id=hash.replace(/^settings[\/-]/,'');const task=id.match(/^voice-(listening|reading|conversation|offline|access|operator)$/);if(task)open(task[1]);else if(aliases[id])open(aliases[id]);
  }
  function mount(integration){
    if(ready)return;
    const root=get('settingsSection-voice');if(!root||!get('voiceModal')||!get('carModeModal'))return;
    hooks=integration;
    const host=create('div',null,'voice-setup');host.id='voiceSetup';
    host.innerHTML=`<h2>Voice setup</h2><p>Choose how you use voice. Dictation writes text, Speak reads replies, and live conversation listens and answers. Each has its own settings and requirements.</p>
      <nav aria-label="Voice setup tasks" class="voice-setup-nav"><a href="#settings/voice-listening" data-voice-part="listening">Listening</a><a href="#settings/voice-reading" data-voice-part="reading">Read replies</a><a href="#settings/voice-conversation" data-voice-part="conversation">Live conversation</a><a href="#settings/voice-offline" data-voice-part="offline">Offline output</a><a href="#settings/voice-access" data-voice-part="access">Call access</a></nav>
      <section id="voiceSetup-listening" class="voice-setup-section settings-row" data-keywords="voice listening dictation microphone speech input" tabindex="-1"><h3>Listening · dictate into a composer</h3><p>Use the composer’s microphone button to insert English text (en-US), then review and send it. Nothing listens while you are setting up. Recognition may depend on your device or a network service; offline output does not make dictation offline.</p><p id="voiceSetupListeningStatus" role="status"></p></section>
      <section id="voiceSetup-reading" class="voice-setup-section settings-row" data-keywords="voice speak spoken replies read aloud output voice" tabindex="-1"><h3>Read replies aloud</h3><p>Speak reads an existing reply when you press its speaker button. It does not open the microphone or start a live conversation. The default below is saved immediately in this browser; a session can keep its own voice from the reader’s voice stepper.</p><p id="voiceSetupReadingStatus" role="status">Reading output voice catalog…</p><button type="button" id="voiceSetupReadingRefresh" class="settings-action-btn">Refresh output voices</button></section>
      <section id="voiceSetup-conversation" class="voice-setup-section settings-row" data-keywords="voice realtime live conversation codex call limits fallback profile" tabindex="-1"><h3>Live conversation · listen and answer</h3><p>This machine · Experimental Codex realtime calls use the ChatGPT subscription lane. Microphone audio goes to OpenAI; starting a call may request microphone permission. An optional OpenAI BYOK profile permits paid fallback. The call can propose actions for confirmation, not execute them automatically.</p><p id="voiceSetupCallStatus" role="status" aria-live="polite">Open this section to read call settings.</p><button type="button" id="voiceSetupReload" class="settings-action-btn">Reload call settings</button><button type="button" data-settings-go="api-keys" class="settings-action-btn">Manage provider keys</button></section>
      <section id="voiceSetup-offline" class="voice-setup-section settings-row" data-keywords="voice offline output kokoro install repair availability" tabindex="-1"><h3>Offline output · for reading replies</h3><p>This machine · Optional local speech output. Its status does not establish offline dictation, an available microphone or offline live conversation. Installation is a separate explicit download.</p><button type="button" id="voiceSetupOfflineRefresh" class="settings-action-btn">Refresh offline status</button></section>
      <section id="voiceSetup-access" class="voice-setup-section settings-row" data-keywords="voice call access shortcut floating mobile" tabindex="-1"><h3>Call access</h3><p>Show or hide the floating CALL shortcut on phone layouts. The header Voice control can still open the live conversation panel.</p><p id="voiceSetupAccessStatus" role="status" aria-live="polite"></p></section>
      <details id="voiceSetup-operator" class="voice-setup-section settings-row" data-keywords="voice hands free car mode operator credentials local" tabindex="-1"><summary>Hands-free operator · separate local process</summary><p>This machine · Car Mode is separate from browser dictation, Speak and Codex realtime calls. Its dispatcher requires an Anthropic key; its speech engine is selected by machine configuration. Status below reports configuration requirements, not a microphone or call test. Blank key fields retain stored credentials.</p></details>`;
    const disclosure=root.querySelector(':scope > details'), destination=disclosure||root;
    destination.append(host);
    const legacy=create('div');legacy.hidden=true;legacy.id='voiceSetupLegacy';host.append(legacy);
    function moveRow(id,target){const element=get(id);if(!element)return;const row=element.closest('.settings-row');if(row)target.append(row);}
    moveRow('settingsTtsVoiceDefault',get('voiceSetup-reading'));moveRow('localSpeechInstall',get('voiceSetup-offline'));moveRow('settingsVoiceCallBtnToggle',get('voiceSetup-access'));
    const callToggle=get('settingsVoiceCallBtnToggle');callToggle.setAttribute('aria-label','Show floating call shortcut');
    callToggle.closest('.settings-row').querySelector('.settings-row-label').textContent='Show floating call shortcut';
    callToggle.closest('.settings-row').querySelector('.settings-row-desc').textContent='This browser · Saved immediately. Does not start a call.';
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
    get('carModeCancelBtn').textContent='Close operator section';
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
    get('voiceSetupReadingRefresh').addEventListener('click',()=>hooks.refreshVoices());
    get('voiceSetupOfflineRefresh').addEventListener('click',()=>window.cccLocalSpeech?.check?.());
    host.addEventListener('click',event=>{const target=event.target.closest('[data-voice-part]');if(target){event.preventDefault();open(target.dataset.voicePart);}});
    operator.addEventListener('toggle',()=>{get('carModeAnthropicKey').value='';get('carModeDeepgramKey').value='';if(operator.open)hooks.loadOperator();});
    // Search each task as a unit, keeping its native controls visible together.
    host.querySelectorAll('.voice-setup-section').forEach(section=>{section.setAttribute('data-settings-search-group','');section.dataset.keywords+=' '+section.textContent;});
    legacy.querySelectorAll('.settings-row').forEach(row=>{row.hidden=true;});
    // Older hash links target real controls now placed in this one section.
    window.addEventListener('hashchange',hashRoute);
    ready=true;refreshAccess();listeningStatus();hashRoute();
    if(disclosure)disclosure.addEventListener('toggle',()=>{if(disclosure.open)enter();});
    if('IntersectionObserver'in window)new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting))enter();}).observe(host);
  }
  window.cccVoiceSetup=Object.freeze({mount,mounted,open,enter,leave,refreshAccess,loaded,loadFailed,saved,ttsLoaded,ttsFailed});
})();
