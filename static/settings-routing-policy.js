/* One staged editor for existing launch policies; runtime consumers stay distinct. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), copy = value => JSON.parse(JSON.stringify(value));
  const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
  const fields = {spawnDefaultsEngine:'engine',spawnDefaultsEffort:'reasoning_effort',spawnDefaultsAutoCompact:'auto_compact_k',
    spawnDefaultsWorkerEngine:'worker_engine',spawnDefaultsWorkerEffort:'worker_reasoning_effort',spawnDefaultsWorkerAutoCompact:'worker_auto_compact_k',spawnDefaultsWorkerInherit:'worker_inherit_session'};
  const workerKeys = ['worker_engine','worker_model','worker_reasoning_effort','worker_auto_compact_k'];
  let api, host, base, draft, chains, initialChains, preview, busy=false, dirty=false, extraDraft={}, context='defaults';
  const manual = new Set();
  function el(tag,text,cls) { const node=document.createElement(tag); if(text!=null)node.textContent=text;if(cls)node.className=cls;return node; }
  function option(select,value,label) { const node=el('option',label ?? value);node.value=value;select.append(node);return node; }
  function error(text='') { $('routingPolicyError').textContent=text;$('routingPolicyError').hidden=!text;if(text&&$('routingPolicyError').getClientRects().length)$('routingPolicyError').scrollIntoView({block:'nearest'}); }
  function status(text) { $('routingPolicyStatus').textContent=text; }
  function levels(engine) { return base?.efforts_by_engine?.[engine || 'claude'] || []; }
  function levelIds(engine) { return levels(engine).map(row=>typeof row==='string'?row:row.id); }
  function effortSelect(select,engine,value) {
    select.replaceChildren();option(select,'','Engine default');
    for(const row of levels(engine))option(select,typeof row==='string'?row:row.id,typeof row==='string'?row:row.label||row.id);
    if(value&&!levelIds(engine).includes(value))option(select,value,value+' · stored, unsupported');select.value=value||'';
  }
  function modelChoices(engine,current) { return (api.models?.(engine,current)||[]).filter(row=>row.id&&row.id!=='__other__'); }
  function modelSelect(select,engine,current,allowManual=false) {
    select.replaceChildren();option(select,'',allowManual?'Engine/shared model default':'Choose a catalog model');
    for(const row of modelChoices(engine,current)) { const node=option(select,row.id,row.label||row.id);node.disabled=!!row.disabled; }
    if(current&&!Array.from(select.options).some(row=>row.value===current))option(select,current,current+' · stored identifier');
    if(allowManual)option(select,'__other__','Manual identifier…');select.value=current||'';
  }
  function setValue(id,value) { const node=$(id);if(!node)return; if(node.tagName==='SELECT'&&!Array.from(node.options).some(row=>row.value===value))option(node,value,value+' · stored, unsupported');node.value=value??''; }
  function chainFrom(doc) { return (Array.isArray(doc?.models)?doc.models:[]).map((row,index)=>({engine:row?.engine||'',model:row?.model||'',effort:row?.effort||'',origin:index})); }
  function reset() {
    draft=copy(base);extraDraft={};manual.clear();const docs=base.policy_documents||{};
    chains={worker_fallback:chainFrom(docs.worker_fallback),fast:chainFrom(docs.model_profiles?.fast),standard:chainFrom(docs.model_profiles?.standard),deep:chainFrom(docs.model_profiles?.deep)};
    draft.worker_fallback_enabled=docs.worker_fallback?.enabled===true;
    initialChains=copy(chains);preview=null;dirty=false;$('routingPolicyDiff').hidden=true;$('routingPolicyDiff').replaceChildren();error();render();updateButtons();
  }
  function independentValues() { const source=base.policy_documents?.worker_overrides||base;return Object.fromEntries(workerKeys.map(key=>[key,source[key]])); }
  function inheritedValues() {
    return {worker_engine:draft.engine,worker_model:draft.models?.[draft.engine]||'',worker_reasoning_effort:draft.reasoning_effort||'',worker_auto_compact_k:draft.auto_compact_k};
  }
  function renderDefaults() {
    const worker=draft.worker_inherit_session?inheritedValues():draft;
    for(const [id,key] of Object.entries(fields)) {
      if(key==='worker_inherit_session')$(id).checked=draft[key]===true;
      else if(!key.endsWith('reasoning_effort'))setValue(id,(key.startsWith('worker_')?worker:draft)[key]);
    }
    effortSelect($('spawnDefaultsEffort'),draft.engine,draft.reasoning_effort);
    effortSelect($('spawnDefaultsWorkerEffort'),worker.worker_engine,worker.worker_reasoning_effort);
    for(const which of ['session','worker']) {
      const engine=which==='session'?draft.engine:worker.worker_engine;
      const value=which==='session'?(draft.models?.[engine]||''):(worker.worker_model||'');
      const id=which==='session'?'spawnDefaultsModel':'spawnDefaultsWorkerModel';
      modelSelect($(id),engine,value,true);if(manual.has(which))$(id).value='__other__';
      const input=$(which==='session'?'spawnDefaultsOtherModel':'spawnDefaultsWorkerOtherModel');input.style.display=manual.has(which)?'':'none';input.value=value;
    }
    $('spawnDefaultsEffortField').style.display='';$('spawnDefaultsWorkerEffortField').style.display='';$('spawnDefaultsWorkerModelField').style.display='';
    $('spawnDefaultsWorkerOverrides').hidden=draft.worker_inherit_session===true;
    $('spawnDefaultsWorkerInheritanceStatus').textContent=draft.worker_inherit_session
      ? (dirty?'Draft: workers follow ':'Saved: workers follow ')+api.label(worker.worker_engine)+' · '+(worker.worker_model||'Engine default model')+'. Saved independent overrides remain available for return.'
      : 'Independent worker defaults. Queue pins take precedence.';
  }
  function changed() { preview=null;dirty=true;$('routingPolicyDiff').hidden=true;error();status('Draft only · nothing saved. Preview all changes before applying.');updateButtons(); }
  function defaultChange(event) {
    const id=event.target.id, key=fields[id], which=id.includes('Worker')?'worker':'session';
    if(!key&&!['spawnDefaultsModel','spawnDefaultsOtherModel','spawnDefaultsWorkerModel','spawnDefaultsWorkerOtherModel'].includes(id))return;
    event.stopImmediatePropagation();if(!draft||busy)return;
    if(key) {
      if(key==='worker_inherit_session') {
        draft[key]=event.target.checked;
        if(!draft[key])Object.assign(draft,independentValues());
      } else draft[key]=key.endsWith('auto_compact_k')?Number(event.target.value):event.target.value;
      if(key==='engine'&&!levelIds(draft.engine).includes(draft.reasoning_effort))draft.reasoning_effort='';
      if(key==='worker_engine') { draft.worker_model='';if(!levelIds(draft.worker_engine).includes(draft.worker_reasoning_effort))draft.worker_reasoning_effort=''; }
    } else {
      const isSelect=event.target.tagName==='SELECT';
      if(isSelect&&event.target.value==='__other__')manual.add(which);else if(isSelect)manual.delete(which);
      const value=isSelect&&event.target.value==='__other__'?(which==='session'?draft.models[draft.engine]:draft.worker_model):event.target.value;
      if(which==='session')draft.models[draft.engine]=value;else draft.worker_model=value;
    }
    changed();if(event.type!=='input')renderDefaults();
  }
  function button(label,fn,disabled=false) { const node=el('button',label,'settings-action-btn');node.type='button';node.disabled=disabled;node.addEventListener('click',fn);return node; }
  function renderChain() {
    const container=$('routingPolicyChain');container.replaceChildren();const worker=context==='worker_fallback';
    if(worker) {
      const label=el('label',' Enable fallback for workers and managed runs');const box=el('input');box.type='checkbox';box.id='routingPolicyFallbackEnabled';box.checked=draft.worker_fallback_enabled;
      box.addEventListener('change',()=>{draft.worker_fallback_enabled=box.checked;changed();});label.prepend(box);container.append(label);
    } else container.append(el('p','Managed profiles use Claude only. The first model is primary; later models are candidates only when the shared worker/managed fallback switch is enabled. Fallback is '+(draft.worker_fallback_enabled?'On':'Off')+'.','settings-row-desc'));
    const rows=chains[context],engines=worker?base.worker_fallback_engines:base.model_profile_engines;
    rows.forEach((entry,index)=>{
      const row=el('fieldset',null,'routing-policy-route');row.append(el('legend',(index===0&&!worker?'Primary route':'Route '+(index+1))));
      const engine=el('select');for(const id of engines)option(engine,id,api.label(id));if(!engines.includes(entry.engine))option(engine,entry.engine,entry.engine+' · stored, unsupported');engine.value=entry.engine;
      const model=el('select');modelSelect(model,entry.engine,entry.model);
      const effort=el('select');effortSelect(effort,entry.engine,entry.effort);
      for(const [labelText,node] of [['Engine',engine],['Model',model],['Effort',effort]]) { const label=el('label',labelText);label.append(node);row.append(label); }
      engine.addEventListener('change',()=>{entry.engine=engine.value;entry.model='';entry.effort='';changed();renderChain();container.querySelectorAll('fieldset')[index]?.querySelectorAll('select')[1]?.focus();});
      model.addEventListener('change',()=>{entry.model=model.value;changed();});effort.addEventListener('change',()=>{entry.effort=effort.value;changed();});
      const actions=el('div',null,'routing-policy-toolbar');
      for(const [label,delta] of [['Move up',-1],['Move down',1]])actions.append(button(label,()=>{const other=index+delta;[rows[index],rows[other]]=[rows[other],rows[index]];changed();renderChain();container.querySelectorAll('fieldset')[other]?.querySelector('select')?.focus();},index+delta<0||index+delta>=rows.length));
      actions.append(button('Remove route',()=>{rows.splice(index,1);changed();renderChain();(container.querySelectorAll('fieldset')[Math.min(index,rows.length-1)]?.querySelector('select')||container.lastElementChild)?.focus();}));row.append(actions);container.append(row);
    });
    if(!rows.length)container.append(el('p',worker?'No replacement candidates configured.':'No route configured for this capability.'));
    container.append(button('Add route',()=>{const engine=engines.find(id=>!worker||!rows.some(row=>row.engine===id))||engines[0];rows.push({engine,model:'',effort:'',origin:null});changed();renderChain();container.querySelectorAll('fieldset')[rows.length-1]?.querySelectorAll('select')[1]?.focus();},rows.length>=4));
  }
  function render() {
    if(!draft)return;
    $('routingPolicyContext').value=context;
    host.querySelector('[data-policy-defaults]').hidden=context!=='defaults';$('routingPolicyChain').hidden=context==='defaults';
    $('routingPolicyScope').textContent=context==='defaults'
      ? 'Default routes · new local sessions and queue workers without pins. Worker inheritance is opt-in; prior overrides are retained. Manual model identifiers are allowed for defaults; provider readiness is not checked.'
      : context==='worker_fallback'?'Worker fallback · Claude, Codex, Kimi and Grok; at most one route per engine. Queue On/Off and effort pins override this policy. Runtime exhaustion may park work.'
      : 'Capability profile · '+context+' · WatchTower managed model runs, Claude only, at most four distinct engine/model pairs. This does not route ordinary new sessions.';
    renderDefaults();if(context!=='defaults')renderChain();
  }
  function payload() {
    const changes={...extraDraft},origins={model_profiles:{}},reference=base.worker_inherit_session&&!draft.worker_inherit_session?(base.policy_documents?.worker_overrides||base):base;
    for(const key of Object.values(fields)) {
      const baseline=workerKeys.includes(key)?reference:base;
      if(key.startsWith('worker_')&&key!=='worker_inherit_session'&&draft.worker_inherit_session)continue;
      if(!same(draft[key],baseline[key]))changes[key]=draft[key];
    }
    const models={};for(const engine of base.supported_engines)if(draft.models[engine]!==base.models[engine])models[engine]=draft.models[engine];if(Object.keys(models).length)changes.models=models;
    if(!draft.worker_inherit_session&&draft.worker_model!==reference.worker_model)changes.worker_model=draft.worker_model;
    const rows=name=>chains[name].map(({engine,model,effort})=>({engine,model,effort}));
    if(!same(chains.worker_fallback,initialChains.worker_fallback)||draft.worker_fallback_enabled!==(base.policy_documents?.worker_fallback?.enabled===true)) {
      changes.worker_fallback={enabled:draft.worker_fallback_enabled,models:rows('worker_fallback')};origins.worker_fallback=chains.worker_fallback.map(row=>row.origin);
    }
    for(const name of ['fast','standard','deep'])if(!same(chains[name],initialChains[name])) {
      (changes.model_profiles||={})[name]={models:rows(name)};origins.model_profiles[name]=chains[name].map(row=>row.origin);
    }
    return {changes,origins};
  }
  function updateButtons() {
    if(!host)return;host.setAttribute('aria-busy',String(busy));
    host.querySelectorAll('input,select,button').forEach(node=>{node.disabled=busy||!base;});
    $('routingPolicyReload').disabled=busy;$('routingPolicyReload').textContent=dirty?'Reload and discard draft':'Reload saved policy';
    $('routingPolicyPreview').disabled=busy||!base||!Object.keys(payload().changes).length;
    $('routingPolicyApply').disabled=busy||!preview?.changes?.length;
    if(base&&draft.worker_inherit_session)$('spawnDefaultsWorkerOverrides').querySelectorAll('input,select,button').forEach(node=>node.disabled=true);
    if(base&&context!=='defaults') { // Retain per-row boundary controls after the global busy toggle.
      const rows=$('routingPolicyChain').querySelectorAll('fieldset');rows.forEach((row,index)=>{row.querySelectorAll('button')[0].disabled=busy||index===0;row.querySelectorAll('button')[1].disabled=busy||index===rows.length-1;});
      $('routingPolicyChain').lastElementChild.disabled=busy||chains[context].length>=4;
    }
  }
  async function json(url,body) {
    const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'});
    const data=await response.json();if(!response.ok||data.ok===false)throw new Error(data.error||'Unable to read launch policy');return data;
  }
  async function load() {
    if(busy)return;busy=true;preview=null;$('routingPolicyDiff').hidden=true;error();status('Reading saved launch policy…');updateButtons();
    try { base=await json('/api/spawn-defaults?policy=1');reset();status('Saved policy loaded · edits stay in a draft until applied.'); }
    catch(err) { error(err.message);status('Saved policy unavailable. Reload to retry; nothing changed.'); }
    finally {busy=false;updateButtons();}
  }
  const labels={engine:'Session engine',models:'Session/shared models',reasoning_effort:'Session effort',auto_compact_k:'Session autocompact',worker_engine:'Worker engine',worker_model:'Worker model',worker_reasoning_effort:'Worker effort',worker_auto_compact_k:'Worker autocompact',worker_inherit_session:'Workers follow sessions',worker_overrides:'Saved independent worker defaults',worker_fallback:'Worker/managed fallback',model_profiles:'Capability profiles',disabled_engines:'Disabled engines'};
  function describe(value) {
    if(value?.absent===true)return 'Not stored';if(typeof value==='boolean')return value?'On':'Off';if(value==null||value==='')return 'Engine default';
    if(typeof value!=='object')return String(value);if(Array.isArray(value))return value.join(', ')||'None';
    if(Array.isArray(value.models))return (Object.hasOwn(value,'enabled')?(value.enabled?'On: ':'Off: '):'')+(value.models.map(row=>[row.engine,row.model,row.effort||'Default effort'].join(' / ')).join(' → ')||'No routes');
    return Object.entries(value).filter(([key])=>['fast','standard','deep',...workerKeys,...(base?.supported_engines||[])].includes(key)).map(([key,row])=>key+': '+(typeof row==='object'?describe(row):String(row))).join('; ');
  }
  async function request(action) {
    if(busy||!base)return;const submitted=payload();if(!Object.keys(submitted.changes).length)return;
    busy=true;error();status(action==='preview'?'Validating the complete draft…':'Saving the previewed policy…');updateButtons();
    try {
      const result=await json('/api/spawn-defaults',{policy_action:action,expected_revision:base.policy_revision,...submitted,preview_token:preview?.preview_token});
      if(action==='preview') {
        preview=result;const view=$('routingPolicyDiff');view.replaceChildren(el('h3','Review all staged changes'));
        for(const change of result.changes) { const row=el('div',null,'routing-policy-diff');row.append(el('strong',labels[change.key]||change.key),el('p','Saved: '+describe(change.before)),el('p','After apply: '+describe(change.after)));view.append(row); }
        view.append(el('p','This changes saved policy only. Queue pins, runtime quota/headroom decisions and active runs remain independent. Metadata on retained policies and routes is preserved.','settings-row-desc'));view.hidden=false;
        status(result.changes.length?'Validated · review the changes, then apply.':'No effective changes; nothing to apply.');view.scrollIntoView({block:'nearest'});
      } else {
        const saved={...base,...result.effective};if(result.applied){api.saved?.(saved);window.dispatchEvent(new CustomEvent('ccc:configuration-saved',{detail:{scope:'machine'}}));}base={...saved,policy_documents:copy(result.effective)};
        // Obtain the new revision without migrating any configuration.
        try {base=await json('/api/spawn-defaults?policy=1');reset();status((result.applied?'Launch policy saved. New launches use the saved policy; running work is unchanged.':'No effective changes saved.')+(result.configuration_history?.warning?' '+result.configuration_history.warning:''));}
        catch(err) {preview=null;error('Policy was saved, but its new revision could not be read. Reload before editing again.');base=null;}
      }
    } catch(err) { preview=null;$('routingPolicyDiff').hidden=true;error(err.message);status('Draft retained · no successful apply was reported. Review or reload before retrying.'); }
    finally {busy=false;updateButtons();if(preview?.changes?.length)$('routingPolicyApply').focus({preventScroll:true});}
  }
  async function open(which='defaults',preset) {
    api?.navigate?.();if(busy){error('Launch policy is busy. Wait for it to finish, then choose this shortcut again.');return;}context=which;if(!base)await load();if(!draft)return;
    if(preset) {
      if(preset.changes) { Object.assign(draft,preset.changes);if(preset.changes.disabled_engines)extraDraft.disabled_engines=preset.changes.disabled_engines;for(const prefix of ['', 'worker_']) {const key=prefix+'reasoning_effort',engine=draft[prefix+'engine'];if(!levelIds(engine).includes(draft[key]))draft[key]='';} }
      else if(preset.target==='worker') {draft.worker_inherit_session=false;Object.assign(draft,independentValues());draft.worker_engine=preset.engine;draft.worker_model='';if(!levelIds(preset.engine).includes(draft.worker_reasoning_effort))draft.worker_reasoning_effort='';}
      else {draft.engine=preset.engine;if(!levelIds(preset.engine).includes(draft.reasoning_effort))draft.reasoning_effort='';}
      changed();
    }
    render();updateButtons();$('routingPolicyContext').focus();host.scrollIntoView({block:'nearest'});
  }
  function mount(adapter) {
    if(host)return;host=$('routingPolicyEditor');if(!host)return;api=adapter;
    for(const [id,key] of Object.entries(fields))$(id)?.setAttribute('aria-label',labels[key]||key);
    for(const [id,label] of [['spawnDefaultsModel','New session model'],['spawnDefaultsOtherModel','New session manual model'],['spawnDefaultsWorkerModel','Worker model'],['spawnDefaultsWorkerOtherModel','Worker manual model']])$(id)?.setAttribute('aria-label',label);
    // Capture before old default-control listeners: unsaved choices never alter global spawn state.
    host.addEventListener('change',defaultChange,true);host.addEventListener('input',defaultChange,true);
    $('routingPolicyContext').addEventListener('change',event=>{context=event.target.value;render();updateButtons();});
    $('routingPolicyReload').addEventListener('click',load);$('routingPolicyDiscard').addEventListener('click',()=>{if(base){reset();status('Draft discarded; saved policy unchanged.');}});
    $('routingPolicyPreview').addEventListener('click',()=>request('preview'));$('routingPolicyApply').addEventListener('click',()=>request('apply'));
    document.addEventListener('click',event=>{const link=event.target.closest('[data-policy-open]');if(link){event.preventDefault();event.stopPropagation();open(link.dataset.policyOpen);}});
    const deepLink=()=>{const id=location.hash.slice(1);if(id==='workerFallbackSettings')open('worker_fallback');else if(id==='modelProfilesSettings')open('standard');else if(Object.keys(fields).includes(id)||['spawnDefaultsModel','spawnDefaultsWorkerModel'].includes(id))open('defaults');};
    window.addEventListener('hashchange',deepLink);deepLink();
    if('IntersectionObserver'in window)new IntersectionObserver(entries=>{if(!base&&!busy&&entries.some(entry=>entry.isIntersecting))load();}).observe(host);
    updateButtons();
  }
  window.cccRoutingPolicy=Object.freeze({mount,open,ownsControls:()=>!!host});
})();
