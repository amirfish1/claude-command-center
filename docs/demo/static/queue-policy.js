/* Contextual queue exceptions, using the native queue manager's controls. */
(() => {
  'use strict';
  const keys = ['engine', 'model', 'effort', 'fallback_to_default_worker', 'headroom_dispatch'];
  const labels = {engine:'Engine',model:'Model',effort:'Effort',fallback_to_default_worker:'Failure fallback',headroom_dispatch:'Headroom dispatch'};
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const button = (text, action) => { const n = node('button',text,'upd-btn'); n.type='button'; n.addEventListener('click',action); return n; };
  const value = (v, present) => !present ? 'Follow default (unset)' : v === false ? 'Off · explicit override' : v === true ? 'On · explicit override' : String(v);
  async function api(body) {
    const response = await fetch('/api/queue/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok || !data.ok) {
      const error = new Error(data.error || 'Queue policy unavailable'); error.status = response.status; throw error;
    }
    return data;
  }
  function mount({modal,fields,queue,hydrate,configure,saved}) {
    modal.classList.add('queue-policy-composer');
    const title = modal.querySelector('#fqConfigTitle'); title.textContent = queue + ' · worker policy';
    const body = modal.querySelector('.fq-config-body'), worker = fields.engine.closest('.fq-config-section');
    for (const section of body.querySelectorAll('.fq-config-section')) section.hidden = section !== worker;
    fields.path.closest('.fq-config-field').hidden = true;
    worker.querySelector('.fq-config-eyebrow').textContent = 'Queue exceptions';
    worker.querySelector('.fq-config-grid').append(fields.fallback.closest('.fq-config-field'));
    const headroomField = node('div',null,'fq-config-field'), headroomLabel = node('label','Headroom dispatch');
    headroomLabel.htmlFor='fqConfigHeadroom'; const headroom = node('select'); headroom.id=headroomLabel.htmlFor;
    for (const [v,label] of [['','Follow worker default'],['on','On'],['off','Off']]) { const o=node('option',label);o.value=v;headroom.append(o); }
    headroomField.append(headroomLabel,headroom,node('span','Runtime quota may choose another route; this does not select a replacement.','fq-config-help'));
    worker.querySelector('.fq-config-grid').append(headroomField);
    const controls = {engine:fields.engine,model:fields.model,effort:fields.effort,fallback_to_default_worker:fields.fallback,headroom_dispatch:headroom};
    const intro = node('p','This machine · This queue only. Preview before applying. Changing the engine override also clears model and effort pins; Follow default clears only the selected exception. Running workers, roles and other queues are unaffected.','fq-config-help');
    body.querySelector('.fq-config-help').replaceWith(intro);
    const savedHost = node('section',null,'queue-policy-saved'); savedHost.append(node('h3','Next launch · saved routing'));
    const savedOutput = node('div','Reading saved policy…'); savedHost.append(savedOutput); body.insertBefore(savedHost,worker);
    const status = node('p','Loading queue policy…','queue-policy-status'); status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    const previewHost = node('section',null,'queue-policy-preview'); previewHost.hidden=true;
    body.append(previewHost,status);
    const footer = modal.querySelector('.upd-actions'), apply = footer.querySelector('[data-fq-config-save]'); apply.textContent='Apply queue policy';apply.disabled=true;
    const preview = button('Preview changes',doPreview), refresh = button('Refresh saved policy',load), config = button('Other queue configuration',configure);
    footer.insertBefore(config,footer.firstChild);footer.insertBefore(refresh,apply);footer.insertBefore(preview,apply);
    let state=null, staged={}, reviewed=null, busy=false, loading=false, generation=0;
    const rows={};
    function invalidate() { generation++;reviewed=null;apply.disabled=true;previewHost.hidden=true; }
    function freeze(on) {
      busy=on; for(const b of [preview,refresh,config]) b.disabled=on;
      apply.disabled=on || !reviewed || !reviewed.changes.length;
      for(const row of Object.values(rows)) {row.override.disabled=on || !state;row.follow.disabled=on || !state;for(const c of row.inputs)c.disabled=on || !row.editing;}
    }
    function changed(key) {
      if (loading || busy || !state) return;
      invalidate();
      const c=controls[key];staged[key]=key==='model' ? (c.value==='__custom__'?fields.customModel.value:c.value) : key.includes('dispatch') || key==='fallback_to_default_worker' ? (c.value===''?null:c.value==='on') : c.value || null;
      if(key==='engine') {staged.model=null;staged.effort=null;}
      status.textContent='Unsaved queue exceptions. Preview to review their scope.';
    }
    for(const key of keys) {
      const c=controls[key], field=c.closest('.fq-config-field'), source=node('p',null,'fq-config-help queue-policy-source'), actions=node('div',null,'queue-policy-actions');
      const inputs=Array.from(field.querySelectorAll('select,input,button'));
      const override=button('Override',()=>{rows[key].editing=true;freeze(false);(key==='engine'?field.querySelector('[data-engine]'):c).focus();status.textContent='Choose a '+labels[key].toLowerCase()+' override; nothing is saved yet.';});
      const follow=button('Follow default',()=>{
        invalidate();staged[key]=null;rows[key].editing=false;
        if(key==='engine') {
          const draft={...state.config};for(const [field,v] of Object.entries(staged)) {if(v==null||v==='')delete draft[field];else draft[field]=v;}
          loading=true;hydrate(draft);loading=false;
        }
        else c.value='';
        rows[key].source.textContent=labels[key]+': staged to follow default.';freeze(false);
        status.textContent='Follow default is staged. Preview before applying.';
      });
      override.setAttribute('aria-label','Override '+labels[key]);follow.setAttribute('aria-label','Follow default for '+labels[key]);
      actions.append(override,follow);field.append(source,actions);rows[key]={source,override,follow,inputs,editing:false};
      c.addEventListener('change',()=>changed(key));
    }
    fields.customModel.addEventListener('input',()=>changed('model'));
    async function load() {
      if(busy)return;invalidate();state=null;loading=true;freeze(true);savedOutput.textContent='Reading saved routing…';
      try {
        const data=await api({queue,policy_action:'read'});if(!modal.isConnected)return;
        state=data;staged={};hydrate(data.config);headroom.value=typeof data.overrides.headroom_dispatch==='boolean'?(data.overrides.headroom_dispatch?'on':'off'):'';
        for(const key of keys){rows[key].editing=false;rows[key].source.textContent=labels[key]+': '+value(data.overrides[key],Object.hasOwn(data.overrides,key));}
        if(data.plan)window.cccLaunchPreview.renderPlan(savedOutput,data.plan,new Date(data.observed_at).toLocaleTimeString());
        else savedOutput.textContent='Saved consumer routing unavailable. No actual route is inferred.';
        status.textContent='Saved queue policy loaded. Override or follow default, then preview.';
      } catch(error) {savedOutput.textContent='Saved launch preview unavailable.';status.textContent='Could not read queue policy: '+error.message+' Refresh to retry; nothing changed.';}
      finally {loading=false;freeze(false);if(!state)preview.disabled=true;}
    }
    async function doPreview() {
      if(busy||!state)return;invalidate();const request=generation;
      if(!Object.keys(staged).length){status.textContent='No staged changes. Choose Override or Follow default first.';return;}
      freeze(true);status.textContent='Validating queue exceptions…';
      try {
        const data=await api({queue,policy_action:'preview',expected_revision:state.policy_revision,changes:staged});
        if(!modal.isConnected||request!==generation)return;
        reviewed={...data,submitted:structuredClone(staged)};
        previewHost.replaceChildren(node('h3','Preview · '+queue+' only'));
        previewHost.append(node('p','Only the exceptions listed below will change. Other queue configuration, default policy and running workers are unaffected. No worker is launched.'));
        const list=node('ul');for(const change of data.changes)list.append(node('li',labels[change.key]+': '+value(change.before,change.before_set)+' → '+value(change.after,change.after_set)));previewHost.append(list);
        const projection=node('details');projection.append(node('summary','After apply · configured projection (not a runtime decision)'));const out=node('div');projection.append(out);window.cccLaunchPreview.renderPlan(out,data.plan,'Unsaved projection');previewHost.append(projection);previewHost.hidden=false;
        status.textContent=data.changes.length?'Preview ready. Apply saves these exceptions for future launches.':'No changes to save.';
        previewHost.scrollIntoView({block:'nearest'});
      }catch(error){reviewed=null;status.textContent='Preview failed: '+error.message+' Nothing saved. Refresh saved policy if it changed.';}
      finally{freeze(false);}
    }
    apply.addEventListener('click',async()=>{
      if(busy||!reviewed)return;const pending=reviewed;freeze(true);status.textContent='Applying queue policy…';
      try{
        const result=await api({queue,policy_action:'apply',expected_revision:state.policy_revision,changes:pending.submitted,preview_token:pending.preview_token});
        reviewed=null;staged={};previewHost.hidden=true;
        if(result.applied){saved();window.dispatchEvent(new CustomEvent('ccc:queue-policy-changed',{detail:{queue}}));window.cccLaunchPreview?.refresh();}
        freeze(false);await load();status.textContent=(result.applied?'Saved queue exceptions. Future launches use this policy.':'No changes saved.')+(state?'':' Saved launch preview could not be refreshed; retry Refresh saved policy.')+(result.configuration_history?.warning?' '+result.configuration_history.warning:'');
      }catch(error){
        invalidate();status.textContent=(error.status?'Not applied: ':'Save could not be confirmed: ')+error.message+' Refresh saved policy and preview again; staged choices remain until refresh.';
      }
      finally{freeze(false);}
    });
    load();requestAnimationFrame(()=>modal.querySelector('[aria-label="Close"]').focus());
  }
  window.cccQueuePolicy=Object.freeze({mount});
})();
