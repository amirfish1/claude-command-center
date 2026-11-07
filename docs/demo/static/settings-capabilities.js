/* Capability setup uses native runtime controls; no synthetic enable switches. */
(() => {
  'use strict';
  const specs = {
    voice: {title:'Voice & speech', scope:'Live call', summary:'Dictation and reading run on demand; there is no global voice switch.', section:'voice', detail:'On/off reports live calls in this CCC only. Dictation and reading run on demand; there is no global voice switch. Call shortcut controls change browser visibility only. Open call controls to end an active call; opening the panel starts nothing.'},
    remote: {title:'Remote access', scope:'Listener', summary:'Manage phone sharing and trusted access. Saved setup is not a reachability test.', section:'network', detail:'On means a non-loopback listener is known. Phone sharing, Cloud relay, paired nodes, external tunnels and reachability are untested. Existing managers retain their own enable/disable controls. Use Phone access to turn off CCC-managed sharing; Network access changes trust/binding and may restart the dashboard.'},
    free: {title:'Free-model routing', scope:'Router', summary:'Start/Stop changes routing. Stopping can break routes pinned to the router.', section:'free', detail:'On/off uses a recent cached router probe only; otherwise runtime is unknown. Start/Stop and Pause provider in the native setup change actual routing. Stopping the router may break routes pinned to it; launch policy stays unchanged.'},
    operator: {title:'Hands-free operator', scope:'Operator', summary:'Separate local process. Stop ends the operator and retains saved keys.', section:'voice', detail:'A separate local process. Runtime is checked by its native manager when opened. Unavailable means its launcher is absent. Stop ends the operator; saved keys are retained. This is separate from dictation and live browser calls.'},
  };
  const get=id=>document.getElementById(id);
  let hooks, host, data={}, pending, gateState='loading';
  const opened=new Set(), bodies=new Map();
  function browserVoice(){try{return ['ccc-voice-call-button','ccc-voice-mobile-btn','ccc-tts-voice-default'].some(key=>localStorage.getItem(key)!==null);}catch(_){return false;}}
  function configured(id){return data[id]?.configured===true || (id==='voice'&&browserVoice()) || (id==='voice'&&data.operator?.configured===true);}
  function visible(id){return opened.has(id)||configured(id)||data[id]?.runtime==='on'||data[id]?.configured===null||(id==='voice'&&data.operator?.configured===null)||gateState==='failed';}
  function reveal(id){
    if(id==='operator')id='voice';
    opened.add(id);sync();hooks?.invalidate();
    window.dispatchEvent(new CustomEvent('ccc:capability-setup',{detail:{id}}));
    if(id==='voice')window.cccVoiceSetup?.enter();
  }
  function label(id){
    const entry=data[id], config=configured(id);
    const runtime=entry?.runtime||'unknown';
    return `${specs[id].scope}: ${runtime[0].toUpperCase()+runtime.slice(1)} · ${config?'Saved setup':entry?.configured===false?'No saved setup':'Setup unknown'}`;
  }
  function sync(){
    for(const [id,body]of bodies){body.hidden=!visible(id);const gate=body.previousElementSibling;if(gate?.classList.contains('capability-gate')){gate.hidden=visible(id);gate.querySelector('[role="status"]').textContent=label(id);}}
    for(const [id,spec]of Object.entries(specs)){
      const row=get('capability-'+id);if(!row)continue;
      row.querySelector('[role="status"]').textContent=label(id);
      row.querySelector('button').textContent=configured(id)||data[id]?.runtime==='on'?'Manage / turn off…':'Set up…';
    }
    const preview=get('capability-previews');if(preview){const meta=window.__CCC_FLAGS_META__||[];preview.querySelector('[role="status"]').textContent=meta.length?((Object.values(window.__CCC_FLAGS__||{}).some(Boolean)?'On':'Off')+' · Preview feature gates; not service status'):'Unavailable · No preview controls supplied by this build';preview.querySelector('button').disabled=!meta.length;}
  }
  function attach(id){
    if(bodies.has(id))return;
    const section=get('settingsSection-'+specs[id].section);if(!section)return;
    const destination=section.querySelector(':scope > details')||section;
    const body=document.createElement('div');body.className='capability-configuration';body.dataset.capabilityBody=id;
    const children=[...destination.childNodes].filter(node=>!(node.nodeType===1&&node.tagName==='SUMMARY'));
    body.append(...children);
    const gate=document.createElement('div');gate.className='settings-row capability-gate';gate.dataset.keywords=specs[id].title+' optional setup enable disable '+body.textContent;gate.setAttribute('data-settings-search-group','');
    gate.innerHTML='<div class="settings-row-main"><div class="settings-row-label"></div><p role="status"></p><p>Setup stays hidden until selected or already configured. Opening setup changes no runtime setting.</p></div><button type="button" class="settings-action-btn">Open setup…</button>';
    gate.querySelector('.settings-row-label').textContent=specs[id].title;
    gate.querySelector('button').onclick=()=>{reveal(id);hooks.navigate(specs[id].section);const focus=body.querySelector('input:not([disabled]),select:not([disabled]),button:not([disabled]),a[href]');focus?.focus();};
    destination.append(gate,body);bodies.set(id,body);sync();hooks.invalidate();
  }
  async function refresh(){
    if(pending)return pending;
    pending=(async()=>{
      get('capabilityInventoryStatus').textContent='Reading saved setup metadata…';
      try{
        const response=await fetch('/api/settings/capabilities',{cache:'no-store'});
        const result=await response.json();
        if(!response.ok||!result.capabilities)throw Error('Inventory unavailable');
        for(const id of Object.keys(specs))if(!result.capabilities[id]||!['on','off','unknown','unavailable'].includes(result.capabilities[id].runtime)||![true,false,null].includes(result.capabilities[id].configured))throw Error('Incomplete inventory');
        data=result.capabilities;gateState='ready';
        get('capabilityInventoryStatus').textContent='This machine · Metadata snapshot. No credentials read or services tested. Refresh after changes; runtime unknown means untested.';
      }catch(_){gateState='failed';data={};get('capabilityInventoryStatus').textContent='Capability inventory unavailable. Existing setup stays accessible; refresh to retry. No runtime state changed.';}
      finally{pending=null;sync();hooks.invalidate();window.dispatchEvent(new CustomEvent('ccc:capability-setup'));}
    })();return pending;
  }
  function open(id){
    if(id==='previews'){hooks.navigate('experimental');return;}
    reveal(id);const spec=specs[id];hooks.navigate(spec.section);
    const section=get('settingsSection-'+spec.section);const disclosure=section?.querySelector(':scope > details');if(disclosure)disclosure.open=true;
    if(id==='operator')window.cccVoiceSetup?.open('operator');
    else if(id==='voice')window.cccVoiceSetup?.open('listening');
    else {const body=bodies.get(id);body?.scrollIntoView({block:'nearest'});(body?.querySelector('button,summary,input,select')||get('capability-'+id)?.querySelector('button'))?.focus();}
  }
  function mount(integration){
    if(host)return;hooks=integration;
    const section=document.createElement('section');section.id='settingsSection-capabilities';section.className='settings-section';section.dataset.sectionId='capabilities';section.setAttribute('role','tabpanel');
    const tab=document.createElement('button');tab.id='settingsRailTab-capabilities';tab.dataset.sectionTarget='capabilities';tab.className='settings-rail-item';tab.type='button';tab.setAttribute('role','tab');tab.setAttribute('aria-selected','false');tab.setAttribute('aria-controls',section.id);tab.textContent='Optional capabilities';get('settingsRail').append(tab);
    section.innerHTML='<div class="settings-section-eyebrow">Optional capabilities</div><div id="settingsCapabilities"><p>Choose a capability to configure. Its own controls enable or stop it; opening setup does neither. Saved settings and credentials are preserved.</p><p id="capabilityInventoryStatus" role="status"></p><button type="button" class="settings-action-btn" id="capabilityRefresh">Refresh inventory</button><div id="capabilityRows"></div></div>';
    get('settingsPane').append(section);host=get('settingsCapabilities');
    new MutationObserver(()=>{if(section.classList.contains('is-active-section')&&!get('settingsModal').hidden)refresh();}).observe(section,{attributes:true,attributeFilter:['class']});
    section.addEventListener('toggle',event=>{if(event.target.parentElement===section&&event.target.open)refresh();},true);
    for(const [id,spec]of Object.entries({...specs,previews:{title:'Preview features',summary:'Native feature gates; turning one off does not stop a running service.',detail:'These native flags control their documented feature gates. Turning off a preview does not stop a service or remove configuration.'}})){
      const row=document.createElement('div');row.id='capability-'+id;row.className='settings-row';row.setAttribute('data-settings-search-group','');row.dataset.keywords='optional capability '+spec.title+' configure enable disable';
      row.innerHTML='<div class="settings-row-main"><div class="settings-row-label"></div><p role="status"></p><p class="capability-summary"></p><details><summary>Scope & effects</summary><p class="capability-effects"></p></details></div><div class="capability-actions"><button type="button" class="settings-action-btn">Set up…</button></div>';
      row.querySelector('.settings-row-label').textContent=spec.title;row.querySelector('.capability-summary').textContent=spec.summary;row.querySelector('.capability-effects').textContent=spec.detail;row.querySelector('button').onclick=()=>open(id);
      if(id==='voice'){const controls=document.createElement('button');controls.type='button';controls.className='settings-action-btn';controls.textContent='Open call controls';controls.onclick=()=>hooks.voiceControls();row.querySelector('.capability-actions').append(controls);}
      get('capabilityRows').append(row);
    }
    get('capabilityRefresh').onclick=refresh;
    for(const id of ['voice','remote','free'])attach(id);
    new MutationObserver(()=>{for(const id of ['voice','remote','free'])attach(id);}).observe(get('settingsPane'),{childList:true,subtree:true});
    window.addEventListener('ccc:flags',sync);
    window.addEventListener('ccc:preferences-applied',()=>{sync();});
    sync();refresh();
  }
  window.cccSettingsCapabilities={mount,refresh,reveal,open,configurationVisible:visible,route(id){
    if(id==='capabilities'&&host){refresh();return;}
    const key=Object.keys(specs).find(key=>specs[key].section===id);if(key)reveal(key);
  }};
})();
