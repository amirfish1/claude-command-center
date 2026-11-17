/* Per-queue worker settings: pick engine/model/effort, press Save.
 * Uses the native queue manager's controls. Save still runs the server's
 * preview (revision + validation) before apply, so nothing is saved blind. */
(() => {
  'use strict';
  const keys = ['engine', 'model', 'effort', 'fallback_to_default_worker', 'headroom_dispatch'];
  const labels = {engine:'Engine',model:'Model',effort:'Effort',fallback_to_default_worker:'Fallback',headroom_dispatch:'Low-quota switch'};
  const node = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const button = (text, action) => { const n = node('button',text,'upd-btn'); n.type='button'; n.addEventListener('click',action); return n; };
  const value = (v, present) => !present || v == null || v === '' ? 'default' : v === false ? 'off' : v === true ? 'on' : String(v);
  async function api(body) {
    const response = await fetch('/api/queue/config', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok || !data.ok) {
      const error = new Error(data.error || 'Queue policy unavailable'); error.status = response.status; throw error;
    }
    return data;
  }
  function summary(plan) {
    const f = plan?.fields || {};
    const part = key => f[key]?.value || (key === 'engine' ? 'unresolved' : 'engine default');
    const pinned = keys.slice(0, 3).filter(key => f[key]?.source === 'queue_pin').map(key => labels[key].toLowerCase());
    return {line: [part('engine'), part('model'), part('effort')].join(' · '),
      origin: pinned.length ? 'Set on this queue: ' + pinned.join(', ') + '. The rest follows the worker defaults.' : 'Using the worker defaults.',
      warnings: plan?.warnings || []};
  }
  function mount({modal,fields,queue,hydrate,configure,saved}) {
    modal.classList.add('queue-policy-composer');
    modal.querySelector('#fqConfigTitle').textContent = queue + ' · worker settings';
    const body = modal.querySelector('.fq-config-body'), worker = fields.engine.closest('.fq-config-section');
    for (const section of body.querySelectorAll('.fq-config-section')) section.hidden = section !== worker;
    fields.path.closest('.fq-config-field').hidden = true;
    worker.querySelector('.fq-config-eyebrow').textContent = 'Change for this queue';
    const defaultChip = worker.querySelector('#fqEngineSeg [data-engine=""]');
    if (defaultChip) defaultChip.lastChild.textContent = 'Default';

    // Advanced: fallback + headroom, folded away.
    const more = node('details',null,'queue-policy-more'); more.append(node('summary','More options'));
    const moreGrid = node('div',null,'fq-config-grid');
    const fallbackField = fields.fallback.closest('.fq-config-field');
    fallbackField.querySelector('label').textContent = 'If a worker fails, retry on another engine';
    fields.fallback.options[0].textContent = 'Default';
    const headroomField = node('div',null,'fq-config-field'), headroomLabel = node('label','Switch engines when quota runs low');
    headroomLabel.htmlFor='fqConfigHeadroom'; const headroom = node('select'); headroom.id=headroomLabel.htmlFor;
    for (const [v,label] of [['','Default'],['on','On'],['off','Off']]) { const o=node('option',label);o.value=v;headroom.append(o); }
    headroomField.append(headroomLabel,headroom);
    moreGrid.append(fallbackField,headroomField); more.append(moreGrid); worker.append(more);

    const controls = {engine:fields.engine,model:fields.model,effort:fields.effort,fallback_to_default_worker:fields.fallback,headroom_dispatch:headroom};
    body.querySelector('.fq-config-help').replaceWith(node('p','Only affects new workers on this queue. Pick "Default" to follow the worker defaults.','fq-config-help queue-policy-intro'));
    const now = node('section',null,'queue-policy-saved');
    const nowLine = node('div','Reading current settings…','queue-policy-now'), nowOrigin = node('p',null,'fq-config-help');
    now.append(node('div','Runs on','fq-config-eyebrow'),nowLine,nowOrigin); body.insertBefore(now,worker);
    const status = node('p','Loading…','queue-policy-status'); status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    body.append(status);

    const footer = modal.querySelector('.upd-actions'), save = footer.querySelector('[data-fq-config-save]'); save.textContent='Save';save.disabled=true;
    const config = button('Other queue settings',configure); config.classList.add('queue-policy-other');
    footer.insertBefore(config,footer.firstChild);

    let state=null, staged={}, busy=false, loading=false;
    const dirty = () => Object.keys(staged).length > 0;
    function freeze(on) {
      busy=on; config.disabled=on;
      save.disabled=on || !state || !dirty();
      for(const c of Object.values(controls)) c.disabled=on || !state;
      worker.querySelectorAll('#fqEngineSeg [data-engine]').forEach(b=>{b.disabled=on || !state;});
      fields.customModel.disabled=on || !state;
    }
    function changed(key) {
      if (loading || busy || !state) return;
      const c=controls[key];
      staged[key]=key==='model' ? (c.value==='__custom__'?fields.customModel.value:c.value) || null
        : key==='headroom_dispatch' || key==='fallback_to_default_worker' ? (c.value===''?null:c.value==='on') : c.value || null;
      if(key==='engine') {staged.model=null;staged.effort=null;}
      // Drop picks that match what is already saved, so Save means a real change.
      for(const k of Object.keys(staged)) if(staged[k]===(state.overrides[k]??null)) delete staged[k];
      status.textContent=dirty()?'Unsaved changes.':'';freeze(false);
    }
    for(const key of keys) controls[key].addEventListener('change',()=>changed(key));
    fields.customModel.addEventListener('input',()=>changed('model'));

    function show(plan) {
      if(!plan){nowLine.textContent='Unavailable';nowOrigin.textContent='Could not work out the current route.';return;}
      const s=summary(plan);nowLine.textContent=s.line;nowOrigin.textContent=s.origin;
      for(const w of s.warnings) nowOrigin.append(node('span',' '+w,'settings-launch-warning'));
    }
    async function load() {
      state=null;loading=true;freeze(true);
      try {
        const data=await api({queue,policy_action:'read'});if(!modal.isConnected)return;
        state=data;staged={};hydrate(data.config);headroom.value=typeof data.overrides.headroom_dispatch==='boolean'?(data.overrides.headroom_dispatch?'on':'off'):'';
        more.open = typeof data.overrides.headroom_dispatch==='boolean' || typeof data.overrides.fallback_to_default_worker==='boolean' || more.open;
        show(data.plan);
        status.textContent='';
      } catch(error) {show(null);status.textContent='Could not load settings: '+error.message+' Close and reopen to retry.';}
      finally {loading=false;freeze(false);}
    }
    async function rejected(error) {
      // Someone else saved meanwhile: reload so the next Save uses the latest.
      if(/changed/i.test(error.message)){busy=false;await load();status.textContent='Not saved: these settings changed elsewhere. Showing the latest now; make your change again.';}
      else status.textContent='Not saved: '+error.message;
    }
    save.addEventListener('click',async()=>{
      if(busy||!state||!dirty())return;
      freeze(true);status.textContent='Saving…';
      const submitted=structuredClone(staged);
      let reviewed;
      try {
        reviewed=await api({queue,policy_action:'preview',expected_revision:state.policy_revision,changes:submitted});
        if(!modal.isConnected)return;
        if(!reviewed.changes.length){staged={};status.textContent='Nothing changed.';return;}
      } catch(error) {await rejected(error);return;}
      finally {if(!reviewed?.changes?.length)freeze(false);}
      try{
        const result=await api({queue,policy_action:'apply',expected_revision:state.policy_revision,changes:submitted,preview_token:reviewed.preview_token});
        staged={};
        if(result.applied){saved();window.dispatchEvent(new CustomEvent('ccc:queue-policy-changed',{detail:{queue}}));window.cccLaunchPreview?.refresh();}
        const list=reviewed.changes.map(c=>labels[c.key]+' '+value(c.before,c.before_set)+' → '+value(c.after,c.after_set)).join(', ');
        freeze(false);await load();
        status.textContent=(result.applied?'Saved: '+list+'.':'Nothing changed.')+(state?'':' Could not reload; close and reopen.')+(result.configuration_history?.warning?' '+result.configuration_history.warning:'');
      }catch(error){
        freeze(false);
        if(error.status)await rejected(error);
        else status.textContent='Save could not be confirmed (connection lost): '+error.message+' Close and reopen to see what is saved.';
      }
    });
    load();requestAnimationFrame(()=>modal.querySelector('[aria-label="Close"]').focus());
  }
  window.cccQueuePolicy=Object.freeze({mount,summary});
})();
