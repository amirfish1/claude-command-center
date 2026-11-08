/* Evidence-first health checks in the existing System dialog. No probes or
 * mutations: repair actions navigate to canonical editors and diagnostics. */
(() => {
  'use strict';
  let api, root, busy=false, snapshot=null, expires=0, returnId='', expiryTimer;
  const get=id=>document.getElementById(id);
  const make=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
  const states={issue:'Needs attention',unchecked:'Unchecked',stale:'Stale',unavailable:'Unavailable',observed:'Observed'};
  function render() {
    const list=get('settingsHealthChecks'), focus=list.contains(document.activeElement)?document.activeElement.id:'', detailsOpen=list.querySelector('details')?.open;list.replaceChildren();
    if(!snapshot)return;
    const stale=Date.now()>expires;
    const ordered=[...snapshot.checks].sort((a,b)=>(a.state==='issue'?0:1)-(b.state==='issue'?0:1));
    const other=make('details'), summary=make('summary','Other evidence · unchecked, stale or observed');other.open=!!detailsOpen;other.append(summary);const otherList=make('ul');other.append(otherList);
    if(stale||!ordered.some(row=>row.state==='issue'))list.append(make('li',stale?'The displayed evidence expired. Refresh to read available observations.':'No confirmed issues in the available observations. Unchecked evidence below does not establish readiness.'));
    for(const row of ordered){
      const item=make('li');item.dataset.healthCheck=row.id;
      const content=make('div'),title=make('strong',row.label),state=stale?'stale':row.state;
      content.append(title,make('span',states[state]),make('p',stale?'This displayed snapshot expired. Refresh evidence; no new probes run.':row.message));
      if(row.age_seconds!==null)content.append(make('small','Observation age at refresh: '+row.age_seconds+' seconds.'));
      const label=row.repair.startsWith('engine')?'Open engine setup':row.repair==='policy'?'Review launch policy':row.repair==='connections'?'Manage connections':'Open service diagnostics';
      const button=make('button',label);button.type='button';button.className='settings-action-btn';button.dataset.healthRepair=row.repair;button.id='health-repair-'+row.id;
      item.append(content,button);(state==='issue'?list:otherList).append(item);
    }
    if(otherList.children.length){const wrapper=make('li');wrapper.className='settings-health-other';wrapper.append(other);list.append(wrapper);}
    if(focus&&document.activeElement===document.body){const target=get(focus);target?.closest('details')?.setAttribute('open','');target?.focus({preventScroll:true});}
  }
  async function refresh(){
    if(busy)return;const initiated=document.activeElement===get('settingsHealthRefresh');busy=true;get('settingsHealthRefresh').disabled=true;
    get('settingsHealthStatus').textContent='Reading saved policy and existing observations…';
    try{
      const r=await fetch('/api/settings/health',{cache:'no-store'});if(!r.ok)throw Error();
      const data=await r.json();
      if(!Array.isArray(data.checks)||!data.checks.length||data.checks.some(row=>!row||typeof row.id!=='string'||typeof row.label!=='string'||typeof row.message!=='string'||!states[row.state]||typeof row.repair!=='string'||!(/^(engines|engine:[a-z]+|services|policy|connections|capabilities)$/).test(row.repair)))throw Error();
      snapshot=data;expires=Date.now()+30000;clearTimeout(expiryTimer);expiryTimer=setTimeout(render,30001);render();
      get('settingsHealthStatus').textContent='This CCC host · Saved policy and cached observations only. Refresh does not test providers, start processes or change configuration.';
    }catch(_){
      if(snapshot){expires=0;render();}else get('settingsHealthChecks').replaceChildren();
      get('settingsHealthStatus').textContent='Health evidence unavailable. Retry refresh; no healthy result can be inferred. Existing diagnostics and setup remain available.';
    }finally{busy=false;get('settingsHealthRefresh').disabled=false;if(initiated&&document.activeElement===document.body)get('settingsHealthRefresh').focus();}
  }
  function back(){
    get('settingsHealthReturn').hidden=true;api.closeSettings();api.openHealth();
    (get(returnId)||get('settingsHealthRefresh'))?.focus();returnId='';
  }
  function repair(button){
    const target=button.dataset.healthRepair;
    if(target==='services'){api.services(button.closest('[data-health-check]')?.dataset.healthCheck);return;}
    returnId=button.id;api.closeSystem();
    api.navigate(target.startsWith('engine')?'engines':target==='policy'?'defaults':target==='capabilities'?'capabilities':'vault');
    if(target.startsWith('engine:'))window.cccEngineConnection?.open(target.slice(7));
    if(target==='policy')window.cccRoutingPolicy?.open('defaults');
    get('settingsHealthReturn').hidden=false;
    // Native editor hooks own their focus; the return action never saves.
  }
  function mount(integration){
    if(root||!get('sysTabContentHealth')||!get('settingsModal'))return;
    api=integration;root=make('section');root.id='settingsHealth';root.setAttribute('aria-label','Actionable health checks');
    const h=make('h2','Health checks'),note=make('p');note.id='settingsHealthStatus';note.setAttribute('role','status');
    const refreshButton=make('button','Refresh evidence');refreshButton.id='settingsHealthRefresh';refreshButton.type='button';refreshButton.className='settings-action-btn';
    const list=make('ul');list.id='settingsHealthChecks';
    root.append(h,note,refreshButton,list);
    const links=make('div');links.className='settings-health-links';
    for(const [label,target]of [['Engine setup','engines'],['Vault','connections'],['Launch policy','policy'],['Optional capabilities','capabilities'],['Service diagnostics (runs status checks)','services']]){
      const button=make('button',label);button.type='button';button.className='settings-action-btn';button.dataset.healthRepair=target;button.id='health-open-'+target;links.append(button);
    }root.append(links);
    const destination=get('sysTabContentHealth');destination.prepend(root);
    const details=make('details'),summary=make('summary','Resource diagnostics');details.append(summary);
    const load=make('button','Load resource snapshot (inspects processes)');load.type='button';load.className='settings-action-btn';details.append(load,get('sysHealthBody'));destination.prepend(details);details.id='sysResourceDetails';
    get('sysHealthBody').textContent='Not loaded. This optional diagnostic inspects local resources and processes.';
    load.addEventListener('click',async()=>{const initiated=document.activeElement===load;load.disabled=true;try{await api.resources();}finally{load.disabled=false;if(initiated&&document.activeElement===document.body)load.focus();}});
    root.addEventListener('click',e=>{const b=e.target.closest('[data-health-repair]');if(b)repair(b);});refreshButton.addEventListener('click',refresh);
    const returnButton=make('button','Back to health checks');returnButton.id='settingsHealthReturn';returnButton.type='button';returnButton.className='settings-action-btn';returnButton.hidden=true;
    get('settingsModal').querySelector('.settings-modal').prepend(returnButton);returnButton.addEventListener('click',back);
    const maintenance=get('settingsSection-maintenance');if(maintenance){const b=make('button','Open health checks');b.type='button';b.id='settingsHealthOpen';b.className='settings-action-btn';const row=make('div');row.className='settings-row';row.dataset.keywords='health checks doctor repair installation routes diagnostics';row.append(b);maintenance.prepend(row);b.addEventListener('click',()=>{api.closeSettings();api.openHealth();get('settingsHealthRefresh').focus();});}
    new MutationObserver(()=>{if(get('settingsModal').hidden){returnButton.hidden=true;}}).observe(get('settingsModal'),{attributes:true,attributeFilter:['hidden']});
    get('sysTabBtnHealth').textContent='Health checks';get('sysTabBtnStatus').textContent='Services';
    // Clock expiry changes only presentation, never polls or probes.
    document.addEventListener('visibilitychange',()=>{if(!document.hidden&&snapshot&&Date.now()>expires)render();});
    const observe=new MutationObserver(()=>{if(get('sysModal').classList.contains('open')&&snapshot&&Date.now()>expires)render();});observe.observe(get('sysModal'),{attributes:true,attributeFilter:['class']});
  }
  window.cccSettingsHealth=Object.freeze({mount,refresh});
})();
