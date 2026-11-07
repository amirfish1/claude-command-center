/* Cached observations help choose future policy; native validators remain authoritative. */
(() => {
  'use strict';
  const MAX_AGE = 5 * 60 * 1000;
  let api = {}, serial = 0;
  const controls = new Map(), labels = new WeakMap();
  const norm = value => String(value || '').trim().toLowerCase();
  function configure(integration) { api = integration; }
  function evidence(engine, snapshot = {}, now = Date.now()) {
    snapshot=snapshot || {};
    const fact = snapshot.engines?.[engine], time = Date.parse(fact?.observed_at || snapshot.generated_at || '');
    if (snapshot.unavailable) return {rank:3, text:'Local status unavailable; last observations are not current'};
    if (!fact) return {rank:3, text:'Installation and sign-in unchecked'};
    if (!Number.isFinite(time) || now - time > MAX_AGE || time > now + 60000)
      return {rank:3, text:'Local observations stale or undated; installation and sign-in unconfirmed'};
    if (fact.cli_present === false) return {rank:2, text:'Installation not detected in cached observation'};
    if (fact.auth_present === false) return {rank:2, text:'Local sign-in not detected in cached observation'};
    if (fact.cli_present === true && fact.auth_present === true)
      return {rank:0, text:'Installation and local sign-in evidence detected'};
    return {rank:1, text:fact.cli_present === true ? 'Installation detected; sign-in unchecked' : 'Installation unchecked; sign-in unconfirmed'};
  }
  function project(engine, row = {}, context = {}, facts = {}) {
    const id = row.id || '', catalog = facts.catalog || {}, own = (catalog[engine] || []).find(item=>norm(item.id)===norm(id));
    const other = id && !own && Object.entries(catalog).some(([key, values])=>key!==engine && values.some(item=>norm(item.id)===norm(id)));
    const reasons = [];
    if (context.engines && !context.engines.includes(engine)) reasons.push('Unsupported engine for this policy context');
    if (row.disabled || row.available === false || own?.available === false)
      reasons.push(row.reason || row.availability_reason || own?.availability_reason || 'Unavailable in this engine catalog');
    if (!context.policyConfirmation && id && (row.policy_blocked || own?.policy_blocked || (facts.blocked || []).some(value=>norm(value)===norm(id))))
      reasons.push('Blocked by model policy');
    if (other) reasons.push('Catalog identifier belongs to another engine');
    const observation = evidence(engine, facts.snapshot, facts.now);
    const disabledEngine = (context.disabled || facts.disabled || []).includes(engine);
    const note = disabledEngine ? 'Engine switched off in CCC; setup policy may still be staged' : observation.text;
    return {...row, disabled:reasons.length>0, reason:reasons.join('; '), observation:note,
      rank:reasons.length ? 5 : disabledEngine ? 4 : id && !own ? Math.max(3,observation.rank) : observation.rank,
      unknown:!!id && !own, capabilities:own ? [own.supports_reasoning && 'Reasoning', own.supports_vision && 'Images', own.supports_tool_call && 'Tools'].filter(Boolean) : []};
  }
  function facts() { return api.facts?.() || {}; }
  function modelRows(engine, rows, context = {}) {
    const state=facts();return rows.map(row=>({...project(engine,row,context,state),nativeDisabled:!!row.disabled})).sort((a,b)=>a.rank-b.rank);
  }
  function refreshOne(select, spec) {
    const engine=spec.engine(), context=spec.context?.() || {}, state=facts(), value=select.value;
    const rows=[...select.options].map((node,index)=>{
      if(!labels.has(node))labels.set(node,node.textContent);
      const sentinel=!node.value || node.value.startsWith('__');
      const row=project(spec.kind==='engine'?node.value:engine,{id:spec.kind==='engine'||sentinel?'':node.value,disabled:node.dataset.catalogDisabled==='true',reason:node.dataset.catalogReason||''},context,state);
      if(sentinel && spec.kind!=='engine') {node.textContent=labels.get(node);return {node,rank:node.value?9:-1,index};}
      if(spec.kind==='engine'&&!node.value)return {node,rank:-1,index};
      node.disabled=row.disabled;
      const suffix=spec.kind==='engine'?row.reason||row.observation:row.reason||(row.unknown?'Stored/manual identifier · catalog unchecked':row.capabilities.join(', '));
      node.textContent=labels.get(node)+(suffix?' · '+suffix:'');node.title=suffix;
      return {node,rank:row.rank,index};
    });
    rows.sort((a,b)=>a.rank-b.rank||a.index-b.index).forEach(row=>select.append(row.node));select.value=value;
    const current=project(engine,{id:spec.kind==='engine'?'':value.startsWith('__')?'':value},context,state);
    const selected=select.selectedOptions[0];
    spec.note.textContent=(selected?.disabled ? selected.title+'. ' : '')+current.observation+'. Connection validity and runtime quota are not verified. '+(spec.kind==='model'&&value&&!value.startsWith('__')&&current.unknown?'Current identifier is retained; native validation decides whether it can be saved. ':'')+'Future policy only; setup is not run automatically.';
    spec.button.hidden=!engine || !api.open;
    if(spec.chips) { const ranked=[...spec.chips.querySelectorAll('[data-engine]')].map((button,index)=>{const result=project(button.dataset.engine,{},context,state);button.title=result.reason||result.observation;return {button,index,rank:button.dataset.engine?result.rank:-1};});ranked.sort((a,b)=>a.rank-b.rank||a.index-b.index).forEach(row=>spec.chips.append(row.button)); }
  }
  function decorate(select, spec) {
    if(!select?.parentNode)return;
    let saved=controls.get(select);
    if(!saved) {
      const wrap=document.createElement('details');wrap.className='choice-eligibility';
      const summary=document.createElement('summary');summary.textContent='Choice details & engine setup';
      const note=document.createElement('p');note.id='choiceEvidence-'+(++serial);
      const button=document.createElement('button');button.type='button';button.className='settings-action-btn';button.textContent='Engine setup · keep draft';
      wrap.append(summary,note,button);select.after(wrap);
      select.setAttribute('aria-describedby',[select.getAttribute('aria-describedby'),note.id].filter(Boolean).join(' '));
      saved={...spec,note,button};controls.set(select,saved);
      select.addEventListener('change',()=>refreshOne(select,controls.get(select)));
      select.addEventListener('focus',()=>refreshOne(select,controls.get(select)));
      wrap.addEventListener('toggle',()=>{if(wrap.open&&controls.has(select))refreshOne(select,controls.get(select));});
      button.addEventListener('click',()=>api.open?.(controls.get(select).engine(),button));
    }
    Object.assign(saved,spec);refreshOne(select,saved);
  }
  function refresh() {for(const [select,spec] of controls) {if(!select.isConnected)controls.delete(select);else refreshOne(select,spec);} }
  window.cccChoiceEligibility=Object.freeze({configure,evidence,project,modelRows,decorate,refresh});
})();
