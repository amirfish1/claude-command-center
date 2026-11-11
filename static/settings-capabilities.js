/* Capability setup uses native runtime controls; no synthetic enable switches. */
(() => {
  'use strict';
  const specs = {
    voice: {title:'Voice & speech', scope:'Live call', summary:'Dictation and reading run on demand; there is no global voice switch.', section:'voice', detail:'On/off reports live calls in this CCC only. Dictation and reading run on demand; there is no global voice switch. Call shortcut controls change browser visibility only. Use End active call below to stop a browser call; viewing settings starts nothing.'},
    remote: {title:'Remote access', scope:'Listener', summary:'Manage phone sharing and trusted access. Saved setup is not a reachability test.', section:'network', detail:'On means a non-loopback listener is known. Phone sharing, Cloud relay, paired nodes, external tunnels and reachability are untested. Existing managers retain their own enable/disable controls. Use Phone access to turn off CCC-managed sharing; Network access changes trust/binding and may restart the dashboard.'},
    free: {title:'Free-model routing', scope:'Router', summary:'Start/Stop changes routing. Stopping can break routes pinned to the router.', section:'free', detail:'On/off uses a recent cached router probe only; otherwise runtime is unknown. Start/Stop and Pause provider in the native setup change actual routing. Stopping the router may break routes pinned to it; launch policy stays unchanged.'},
    operator: {title:'Hands-free operator', scope:'Operator', summary:'Separate local process. Stop ends the operator and retains saved keys.', section:'voice', detail:'A separate local process. Runtime is checked by its native manager when opened. Unavailable means its launcher is absent. Stop ends the operator; saved keys are retained. This is separate from dictation and live browser calls.'},
  };
  const get=id=>document.getElementById(id);
  let hooks, host, data={}, pending;
  const bodies=new Map(), destinations=new Map();
  function browserVoice(){try{return ['ccc-voice-call-button','ccc-voice-mobile-btn','ccc-tts-voice-default'].some(key=>localStorage.getItem(key)!==null);}catch(_){return false;}}
  function configured(id){return data[id]?.configured===true || (id==='voice'&&browserVoice()) || (id==='voice'&&data.operator?.configured===true);}
  function visible(){return true;}
  function reveal(id){
    if(id==='operator')id='voice';
    sync();hooks?.invalidate();
    window.dispatchEvent(new CustomEvent('ccc:capability-setup',{detail:{id}}));
    if(id==='voice')window.cccVoiceSetup?.enter();
  }
  function label(id){
    const entry=data[id], config=configured(id);
    const runtime=entry?.runtime||'unknown';
    return `${specs[id].scope}: ${runtime[0].toUpperCase()+runtime.slice(1)} · ${config?'Saved setup':entry?.configured===false?'No saved setup':'Setup unknown'}`;
  }
  function sync(){
    for(const [id]of Object.entries(specs)){
      const row=get('capability-'+id);if(row)row.querySelector('[role="status"]').textContent=label(id);
    }
    const preview=get('capability-previews');if(preview){const meta=window.__CCC_FLAGS_META__||[];preview.querySelector('[role="status"]').textContent=meta.length?((Object.values(window.__CCC_FLAGS__||{}).some(Boolean)?'On':'Off')+' · Preview feature gates; not service status'):'Unavailable · No preview controls supplied by this build';}
  }
  function attach(id){
    if(bodies.has(id))return;
    const section=get('settingsSection-'+specs[id].section);if(!section)return;
    const destination=section.querySelector(':scope > details')||section;
    const body=document.createElement('div');body.className='capability-configuration';body.dataset.capabilityBody=id;
    const children=[...destination.childNodes].filter(node=>!(node.nodeType===1&&node.tagName==='SUMMARY'));
    body.append(...children);
    destination.append(body);bodies.set(id,body);destinations.set(id,destination);
    if(id==='remote')window.cccPhoneAccess?.mount(get('phoneAccessSetup'));
    sync();hooks.invalidate();
  }
  function placeConfigurations(catalog){
    for(const [id,body]of bodies){
      const target=catalog?get('capability-'+id).querySelector('.capability-inline'):destinations.get(id);
      if(body.parentElement!==target)target.append(body);
    }
    hooks.invalidate();
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
        data=result.capabilities;
        get('capabilityInventoryStatus').textContent='This machine · Metadata snapshot. No credentials read or services tested. Refresh after changes; runtime unknown means untested.';
      }catch(_){data={};get('capabilityInventoryStatus').textContent='Capability inventory unavailable. Existing setup stays accessible; refresh to retry. No runtime state changed.';}
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
    section.innerHTML='<div class="settings-section-eyebrow">Optional capabilities</div><div id="settingsCapabilities"><p>Configure optional capabilities below. Their own controls enable or stop them; viewing settings does neither. Saved settings and credentials are preserved.</p><p id="capabilityInventoryStatus" role="status"></p><button type="button" class="settings-action-btn" id="capabilityRefresh">Refresh inventory</button><div id="capabilityRows"></div></div>';
    get('settingsPane').append(section);host=get('settingsCapabilities');
    new MutationObserver(()=>{if(section.classList.contains('is-active-section')&&!get('settingsModal').hidden)refresh();}).observe(section,{attributes:true,attributeFilter:['class']});
    section.addEventListener('toggle',event=>{if(event.target.parentElement===section&&event.target.open)refresh();},true);
    for(const [id,spec]of Object.entries({...specs,previews:{title:'Preview features',summary:'Native feature gates; turning one off does not stop a running service.',detail:'These native flags control their documented feature gates. Turning off a preview does not stop a service or remove configuration.'}})){
      const row=document.createElement('div');row.id='capability-'+id;row.className='settings-row';row.dataset.keywords='optional capability '+spec.title+' configure enable disable';
      row.innerHTML='<div class="settings-row-main"><h3 class="settings-row-label"></h3><p role="status"></p><p class="capability-summary"></p><p class="capability-effects"></p><div class="capability-inline"></div></div>';
      row.querySelector('.settings-row-label').textContent=spec.title;row.querySelector('.capability-summary').textContent=spec.summary;row.querySelector('.capability-effects').textContent=spec.detail;
      get('capabilityRows').append(row);
    }
    get('capabilityRefresh').onclick=refresh;
    for(const id of ['voice','remote'])attach(id);
    new MutationObserver(()=>{for(const id of ['voice','remote'])attach(id);}).observe(get('settingsPane'),{childList:true,subtree:true});
    window.addEventListener('ccc:flags',sync);
    window.addEventListener('ccc:preferences-applied',()=>{sync();});
    sync();refresh();
  }
  window.cccSettingsCapabilities={mount,refresh,reveal,open,configurationVisible:visible,route(id){
    const catalog=id==='capabilities'||id==='leftover'||get('settingsSection-'+id)?.dataset.destination==='leftover';
    if(host)placeConfigurations(catalog);
    if(catalog&&host){refresh();window.cccVoiceSetup?.enter();return;}
    const key=Object.keys(specs).find(key=>specs[key].section===id);if(key)reveal(key);
  }};
})();
