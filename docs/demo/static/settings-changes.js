/* Read-only customization review inside the existing preference workbench.
 * Fixed local keys only. No history/profile/reset scope is widened here. */
(() => {
 'use strict';
 const get=id=>document.getElementById(id), make=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
 const FIXED={theme:['ccc-theme','Theme','appearance'],font:['ccc-font','Font','appearance'],sounds_enabled:['ccc-sounds-enabled','Completion sounds','notifications'],compact_rows:['ccc-compact-rows','Compact rows','list-presentation'],wrap_titles:['ccc-wrap-titles','Wrap titles','list-presentation'],separate_tabs:['ccc-separate-tabs','Separate tabs','list-presentation'],queue_rhs_list:['ccc-queue-rhs-list','Queue health list','queue-view'],voice_mobile_button:['ccc-voice-mobile-btn','Mobile call button','voice'],chips_mode:['ccc-chips-mode','Chip colors','list-presentation'],coding_density:['ccc-coding-density','Coding density','list-presentation'],workers_density:['ccc-workers-density','Workers density','list-presentation'],rows_spacing:['ccc-rows-spacing','Row spacing','list-presentation'],view_issues:['ccc-view-gh','Include GitHub issues','list-presentation'],reader_scale:['ccc-conv-font-scale','Reader text scale','reader'],workers_dense_legacy:['ccc-workers-dense','Legacy workers density','list-presentation'],voice_call_button:['ccc-voice-call-button','Floating call button','voice'],notify_enabled:['ccc-notify-enabled','Notifications','notifications'],notify_tasks:['ccc-notify-tasks','Task notifications','notifications'],notify_digest:['ccc-notify-digest','Digest notifications','notifications'],notify_milestones:['ccc-notify-milestones','Milestone notifications','notifications']};
 let api,host,request=0,documentData=null,localRevision='',stamp=0,backId='',returnButton,timer;
 const hidden='[unsupported saved value hidden]';
 function browserRows(specs){
  const raw={},values={};for(const[key,[source]]of Object.entries(FIXED)){if(specs[key]?.source_key!==source||specs[key]?.scope!=='browser')throw Error('Invalid registry');raw[key]=localStorage.getItem(source);const s=specs[key];
   if(raw[key]===null)values[key]=s.factory_default;
   else if(s.type==='boolean')values[key]=['separate_tabs','queue_rhs_list'].includes(key)?raw[key]==='on':s.factory_default?raw[key]!=='0':raw[key]==='1';
   else if(s.type==='number'){const v=parseFloat(raw[key]);values[key]=Number.isFinite(v)&&v>=0.7&&v<=1.5?v:hidden;}
   else values[key]=s.choices.includes(raw[key])?raw[key]:hidden;
  }
  const density=lane=>['compact','cozy','detailed'].includes(raw[lane+'_density'])?raw[lane+'_density']:lane==='workers'?(raw.workers_dense_legacy==='0'?'cozy':'compact'):raw.compact_rows==='0'?'detailed':raw.wrap_titles==='1'?'cozy':'compact';
  values.coding_density=density('coding');values.workers_density=density('workers');
  const currentTab=window.CCCNavigation.getTab(),lane=currentTab==='workers'?'workers':!currentTab||currentTab==='coding'?'coding':null;
  const rows=[];for(const[key,s]of Object.entries(specs)){if(!FIXED[key])continue;
   const derived=key==='coding_density'&&raw[key]===null&&(raw.compact_rows!==null||raw.wrap_titles!==null)||key==='workers_density'&&raw[key]===null&&raw.workers_dense_legacy!==null;
   if(raw[key]===null&&!derived)continue;
   let stored=raw[key]===null?null:values[key],effective=values[key],activity=derived?'inherited':'active',explanation='Saved in this browser. A saved value may have been seeded by a native control; presence alone does not establish a deliberate user action.';
   if(lane&&['compact_rows','wrap_titles'].includes(key)){effective=key==='compact_rows'?density(lane)!=='detailed':density(lane)!=='compact';activity='shadowed';explanation='Current '+lane+' lane density controls this display. The legacy value still applies in the Active view.';}
   if(key==='workers_dense_legacy'&&raw.workers_density!==null){activity='shadowed';explanation='The Workers density preference takes precedence over this legacy key.';}
   if(key.startsWith('notify_')&&key!=='notify_enabled'&&!values.notify_enabled){effective=false;activity='shadowed';explanation='The master notification preference is Off. This saved category preference returns when notifications are enabled.';}
   if(['coding_density','workers_density'].includes(key)&&raw[key]!==null&&!s.choices.includes(raw[key])){stored=hidden;activity='unknown';explanation='Unsupported density encoding; effective value follows the native legacy fallback without seeding storage.';}
   if(s.type==='boolean'&&raw[key]!==null&&!(['separate_tabs','queue_rhs_list'].includes(key)?['on','off']:['1','0']).includes(raw[key])){stored=hidden;activity='unknown';explanation='Unsupported boolean encoding. Effective value follows the existing native adapter; raw data is hidden.';}
   if(effective===hidden)activity='unknown';
   rows.push({key:'browser.'+key,label:FIXED[key][1],scope:'browser',owner:'This browser',baseline:s.factory_default,baseline_known:true,stored,effective,explicit:raw[key]!==null,changed:effective===hidden?null:effective!==s.factory_default,stored_changed:stored===hidden?null:stored!==s.factory_default,activity,explanation,edit:{kind:'browser',context:FIXED[key][2]}});
  }
  return {rows,revision:JSON.stringify([raw,currentTab])};
 }
 function display(value){if(value===null)return 'Unresolved / no override';if(typeof value==='boolean')return value?'On':'Off';if(value==='')return 'Engine/shared default';return typeof value==='object'?JSON.stringify(value):String(value);}
 function status(text){get('settingsChangesStatus').textContent=text;}
 function focusSource(button){let note=button.parentElement.querySelector('.settings-changes-edit-error');if(!note){note=make('p');note.className='settings-changes-edit-error';note.setAttribute('role','status');button.before(note);}note.textContent=get('settingsChangesStatus').textContent;button.scrollIntoView({block:'center',inline:'nearest'});button.focus({preventScroll:true});requestAnimationFrame(()=>{if(document.activeElement===button)button.scrollIntoView({block:'center',inline:'nearest'});});}
 function returnToReview(){const id=backId;returnButton?.remove();api.review();try{render();}catch(_){status('Browser storage unavailable; Refresh to retry.');}(get(id)||get('settingsChangesRefresh'))?.focus();}
 async function edit(row,button){
  if(!api)return;button.parentElement.querySelector('.settings-changes-edit-error')?.remove();backId=button.id;
  if(row.edit.kind==='queue'){await api.queue(row.edit.context,button);return;}
  returnButton?.remove();returnButton=make('button','Back to customization review');returnButton.type='button';returnButton.className='settings-action-btn settings-changes-return';returnButton.onclick=event=>{event.stopPropagation();returnToReview();};
  const target=await api.edit(row.edit);
  if(target?.getClientRects().length && getComputedStyle(target).visibility !== 'hidden'){target.prepend(returnButton);}else{api.review();status(row.edit.context==='reader'?'Select a conversation before editing reader text size. Your configuration was not changed.':'This editor is unavailable in the current context. Your configuration was not changed.');focusSource(button);}
 }
 function list(rows){const ul=make('ul');for(const row of rows){const li=make('li');li.dataset.changeKey=row.key;const top=make('div');top.append(make('strong',row.label),make('span',row.owner+' · '+row.activity));li.append(top);
   const dl=make('dl');for(const[label,value]of [['Baseline',row.baseline_known?display(row.baseline):'Context-dependent; not inspected'],['Saved',row.explicit?display(row.stored):'Not explicitly stored'],['Effective configuration',display(row.effective)]]){dl.append(make('dt',label),make('dd',value));}li.append(dl);
   li.append(make('p',row.explanation));if(row.changed===false)li.append(make('p',row.stored_changed?'Saved differs, but the effective value matches the baseline.':'Explicitly saved at the baseline; this is not an effective change.'));
   if(row.edit?.kind!=='none'){const b=make('button','Edit '+row.label);b.type='button';b.id='change-edit-'+row.scope+'-'+encodeURIComponent(row.key).replace(/%/g,'_');b.className='settings-action-btn';b.onclick=event=>{event.stopPropagation();edit(row,b).catch(()=>{status('Could not open the canonical editor. Retry; no changes were applied.');api.review();focusSource(b);});};li.append(b);}ul.append(li);
  }return ul;}
 function render(){
  if(!documentData)return;const groups=get('settingsChangesGroups'),focus=groups.contains(document.activeElement)?document.activeElement.id:'';groups.replaceChildren();let local;try{local=browserRows(documentData.browser_registry);localRevision=local.revision;}catch(_){local={rows:[],unavailable:true};}
  const all=[{scope:'browser',status:local.unavailable?'unavailable':'available',rows:local.rows},...documentData.groups];
  for(const group of all){const section=make('section');section.dataset.changeScope=group.scope;section.append(make('h4',group.scope==='browser'?'This browser':group.scope==='machine'?'This CCC host':'Queue exceptions'));
   if(group.status==='unavailable'){section.append(make('p','Unavailable: this configuration could not be read. Refresh to retry; an empty or all-default result cannot be inferred.'));groups.append(section);continue;}
   const primary=group.rows.filter(r=>(r.changed===true||r.activity==='shadowed'||r.effective===hidden||r.stored==='[unregistered value hidden]')&&r.changed!==false);
   const secondary=group.rows.filter(r=>!primary.includes(r));
   if(primary.length)section.append(list(primary));else section.append(make('p',group.rows.length?'No confirmed effective differences from known baselines in this scope. Review saved and conditional values below.':'No saved overrides in this covered scope. This does not establish that all settings use defaults.'));
   if(secondary.length){const details=make('section');details.append(make('h4','Saved at baseline, inherited or conditional ('+secondary.length+')'),list(secondary));section.append(details);}groups.append(section);
  }
  get('settingsChangesCoverage').textContent=documentData.coverage;
  if(focus&&document.activeElement===document.body){get(focus)?.closest('details')?.setAttribute('open','');get(focus)?.focus({preventScroll:true});}
 }
 async function refresh(){
  if(!host)return;clearTimeout(timer);const generation=++request,b=get('settingsChangesRefresh'),initiated=document.activeElement===b;b.disabled=true;status('Reading supported customization…');
  try{const response=await fetch('/api/settings/changes',{cache:'no-store'});const data=await response.json();if(generation!==request)return;if(!response.ok||!Array.isArray(data.groups)||data.groups.length!==2||!data.browser_registry||typeof data.coverage!=='string')throw Error();
   for(const g of data.groups)if(!['machine','queue'].includes(g.scope)||!['available','absent','unavailable'].includes(g.status)||!Array.isArray(g.rows))throw Error();
   for(const [key,[source]]of Object.entries(FIXED))if(data.browser_registry[key]?.source_key!==source)throw Error();documentData=data;stamp=Date.now();render();clearTimeout(timer);timer=setTimeout(()=>status('Snapshot is stale. Refresh before reviewing current configuration; no settings were changed.'),30000);status('Read-only snapshot. Baselines are stated per field. Configured effects apply to future matching launches; running work and runtime quota decisions are separate.');
  }catch(_){if(generation!==request)return;get('settingsChangesGroups').replaceChildren();documentData=null;status('Customization unavailable. Refresh to retry; no empty or all-default result is inferred. No settings changed.');}
  finally{if(generation===request){b.disabled=false;if(initiated&&document.activeElement===document.body)b.focus();}}
 }
 function mount(hooks){if(hooks)api=hooks;if(host)return host;const panel=get('settingsSection-preferences');if(!panel)return null;
  const note=panel.querySelector('.settings-section-note');if(note)note.textContent='Review supported customization across scopes. Browser profile actions below apply only to their registered browser preferences.';
  host=make('section');host.id='settingsChanges';host.innerHTML='<h3>Changed from defaults</h3><p>Review saved customization across this browser, this CCC host and queue policies. Saved at baseline, inherited and shadowed values are distinguished; this view never resets or saves configuration.</p><button type="button" class="settings-action-btn" id="settingsChangesRefresh">Refresh customization</button><p role="status" id="settingsChangesStatus"></p><div id="settingsChangesGroups"></div><section><h4>Coverage &amp; protected data</h4><p id="settingsChangesCoverage"></p><p>Browser profile import, reset and history below still cover only their existing supported browser keys. This review does not widen them.</p></section>';
  panel.querySelector('[data-pref-changed]')?.before(host);const legacy=panel.querySelector('[data-pref-changed]');if(legacy)legacy.hidden=true;
  get('settingsChangesRefresh').onclick=refresh;window.addEventListener('storage',event=>{if(!documentData||event.key!==null&&!Object.values(FIXED).some(([key])=>key===event.key)&&event.key!=='ccc-sidebar-tab')return;try{const live=browserRows(documentData.browser_registry);if(live.revision!==localRevision){render();status('Browser preferences changed elsewhere; browser rows refreshed. Refresh to re-read host and queue evidence.');}}catch(_){status('Browser storage unavailable. Refresh to retry; no defaults are inferred.');}});
  window.addEventListener('ccc:queue-policy-changed',()=>{stamp=0;status('Queue configuration changed. Refresh to review current customization.');});
  panel.addEventListener('focusin',()=>{if(documentData&&Date.now()-stamp>30000)status('Snapshot is stale. Refresh before reviewing current configuration.');});refresh();return host;
 }
 window.cccSettingsChanges=Object.freeze({mount,refresh});
 new MutationObserver(()=>mount()).observe(document.documentElement,{childList:true,subtree:true});mount();
})();
