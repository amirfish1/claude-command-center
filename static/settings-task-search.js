/* Curated navigation suggestions. Search never writes or stages preferences. */
(() => {
  'use strict';
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
  const tasks=[
    ['sounds','Stop or change completion sounds','notifications','settingsSoundsToggle','sound sounds chime chimes mute unmute beep audio','browser'],
    ['notifications','Turn notifications on or off','notifications','settingsNotifyEnabledToggle','notification notifications alerts quiet digest interruptions','browser'],
    ['queue','Use an engine for a queue','queues','settingsQueueTable','queue queues dispatch pin pins','queue'],
    ['defaults','Change new-session defaults','routing','routingPolicyEditor','session sessions default defaults engine model codex claude effort','host'],
    ['worker','Follow session defaults or set worker overrides','routing','routingPolicyEditor','worker workers inherit inheritance','host'],
    ['fallback','Change worker fallback order','routing','routingPolicyEditor','fallback failover exhausted backup','host'],
    ['profiles','Edit Fast, Standard or Deep routes','routing','routingPolicyEditor','fast standard deep capability profiles route routing','host'],
    ['engine','Connect or repair an engine','engines','engineConnection','install login authenticate authentication engine missing connect codex claude','editor'],
    ['connections','Manage service credentials','vault','serviceConnections','key keys credential credentials account provider token secret','editor'],
    ['voice','Choose a read-aloud voice','voice','voiceSetup-reading','speak spoken speech reading read aloud voice output','browser'],
    ['calls','Configure live conversation','voice','voiceSetup-conversation','call calls microphone listening conversation','editor'],
    ['theme','Change theme or font','appearance','appearancePopover','theme dark light font appearance','browser'],
    ['reader','Change reader text size','reader','settingsModalFontSlot','text size zoom bigger smaller reader readability','browser'],
    ['list','Change session list presentation','list-presentation','contextListPreferences','list chips density compact titles tabs issues','browser'],
    ['branding','Change repository appearance','repository-branding','contextRepoPreferences','repository repo avatar branding icon','editor'],
    ['history','Review or undo configuration changes','preferences','settingsConfigurationHistory','undo history revert changes restore','editor'],
    ['transfer','Export or import preferences','preferences','settingsHostProfiles','export import transfer portable preferences profile','editor'],
    ['reset','Preview a browser preference reset','preferences','settingsSection-preferences','reset defaults browser clear preferences','editor'],
    ['health','Find configuration problems','health','settingsHealth','health broken failure invalid diagnose repair','editor'],
    ['capabilities','Review optional capabilities','capabilities','settingsCapabilities','optional capability capabilities enable remote offline free','editor'],
  ].map(([id,title,section,target,words,scope])=>Object.freeze({id,title,section,target,words:words.split(' '),scope}));
  const ignored=new Set('a an the to for of my this that please how do i can want use set change turn on off stop make in with and'.split(' '));
  function match(query){
    const words=String(query||'').toLowerCase().match(/[a-z0-9]+/g)||[];
    const meaningful=[...new Set(words.filter(word=>!ignored.has(word)))];
    if(!meaningful.length)return [];
    return tasks.map(task=>({task,score:meaningful.filter(word=>task.words.includes(word)).length})).filter(row=>row.score>0)
      .sort((a,b)=>b.score-a.score||tasks.indexOf(a.task)-tasks.indexOf(b.task)).slice(0,6).map(row=>row.task);
  }
  let hooks,host,input,query='',generation=0,requestController,debounce,expiry,back;
  function localState(task){
    try {
      if(task.id==='sounds')return 'This browser: completion sounds '+(localStorage.getItem('ccc-sounds-enabled')==='0'?'Off':'On')+'.';
      if(task.id==='notifications')return 'This browser: '+(window.cccNotify?.policy?.()||'policy unavailable')+'. Browser permission is separate.';
      if(task.id==='reader'){const n=parseFloat(localStorage.getItem('ccc-conv-font-scale')||'1');return 'This browser: reader scale '+(Number.isFinite(n)&&n>=0.7&&n<=1.5?Math.round(n*100)+'%':'unavailable')+'. Select a conversation before editing.';}
      if(task.id==='theme')return 'This browser: theme '+(['light','dark','system'].includes(localStorage.getItem('ccc-theme'))?localStorage.getItem('ccc-theme'):'system')+'.';
    } catch (_) { return 'Browser state unavailable. Open the editor to review; no default is inferred.'; }
    return task.scope==='browser'?'Current values are shown in the native browser editor.':task.scope==='host'?'Reading saved host policy…':task.scope==='queue'?'Reading active queues and their saved routing…':'Current status is shown in the canonical editor. Opening is not a save or a readiness test.';
  }
  function status(text){host.querySelector('[role=status]').textContent=text;}
  function row(task){
    const item=make('li');item.dataset.taskId=task.id;
    const button=make('button',task.title);button.type='button';button.className='settings-action-btn';button.dataset.settingsTask=task.id;
    button.onclick=event=>{event.preventDefault();event.stopPropagation();navigate(task,button);};
    const state=make('p',localState(task));state.dataset.taskState='';item.append(button,state);return item;
  }
  function updateState(id,text){const node=host.querySelector('[data-task-id="'+id+'"] [data-task-state]');if(node)node.textContent=text;}
  function safe(value){return typeof value==='string'&&value.length<=160?value:'Unknown';}
  async function loadState(selected,seq){
    const queue=selected.some(t=>t.scope==='queue'),policy=selected.some(t=>t.scope==='host');
    if(!queue&&!policy)return;
    requestController=new AbortController();const signal=requestController.signal;
    await Promise.allSettled([
      ...(queue?[fetch('/api/settings/queue-engines',{cache:'no-store',signal}).then(async response=>{
        const data=await response.json();if(!response.ok||!Array.isArray(data.queues))throw Error();if(seq!==generation)return;
        const item=host.querySelector('[data-task-id=queue]');if(!item)return;
        updateState('queue',data.queues.length+' active queues. Choose the exact queue; no engine is selected or staged from your query.');
        const list=make('div');list.className='settings-task-queues';
        for(const entry of data.queues){if(typeof entry.queue!=='string'||!entry.queue||entry.queue.length>160)continue;
          const button=make('button',entry.queue+' · '+safe(entry.engine)+' / '+safe(entry.model));button.type='button';button.className='settings-action-btn';button.dataset.settingsTaskQueue=entry.queue;
          button.onclick=event=>{event.preventDefault();event.stopPropagation();hooks.queue(entry.queue,button);};list.append(button);
        }
        item.append(list);
      }).catch(()=>{if(seq===generation)updateState('queue','Queue state unavailable. Open the queue report to retry; no empty/default result is inferred.');})]:[]),
      ...(policy?[fetch('/api/spawn-defaults?policy=1',{cache:'no-store',signal}).then(async response=>{
        const data=await response.json();if(!response.ok||!data.ok)throw Error();if(seq!==generation)return;
        updateState('defaults','Saved host defaults: '+safe(data.engine)+' / '+safe(data.models?.[data.engine])+' / '+(safe(data.reasoning_effort)||'Engine default')+'. Future launches; not a readiness check.');
        updateState('worker','Saved worker relationship: '+(data.worker_inherit_session===true?'Follow session defaults':'Independent')+'. Worker engine: '+safe(data.worker_engine)+'. Queue pins can override this.');
        updateState('fallback','Saved fallback policy loaded. Open the staged editor to review the ordered routes; runtime quota is unresolved.');
        updateState('profiles','Saved capability profiles loaded. Open the staged editor to review Fast, Standard and Deep; no draft changes made.');
      }).catch(()=>{if(seq===generation)for(const task of selected.filter(t=>t.scope==='host'))updateState(task.id,'Host policy unavailable. Open the editor to retry; no defaults are inferred.');})]:[]),
    ]);
    if(seq===generation)expiry=setTimeout(()=>status('These saved-state summaries may be stale. Refresh results before relying on them; editors re-read their own state.'),30000);
  }
  function search(value){
    if(!host)return;query=String(value||'').trim();const seq=++generation;clearTimeout(debounce);clearTimeout(expiry);requestController?.abort();
    const selected=match(query);host.hidden=!selected.length;const list=host.querySelector('ul');list.replaceChildren(...selected.map(row));
    status('Suggested tasks. Choose an editor; search never saves, stages or launches anything. Literal setting matches remain below.');
    if(selected.length)debounce=setTimeout(()=>loadState(selected,seq),250);
  }
  async function navigate(task,source){
    const original=query;
    try {
      const target=await hooks.open(task);
      if(!target||!target.isConnected||!target.getClientRects().length)throw Error('This editor is unavailable in the current context. For reader settings, select a conversation first.');
      back?.remove();back=make('button','Back to search');back.type='button';back.className='settings-action-btn settings-task-back';back.dataset.settingsTaskBack='';
      back.onclick=event=>{event.preventDefault();event.stopPropagation();back.remove();hooks.returnToSearch(original);const button=host.querySelector('[data-settings-task="'+task.id+'"]');button?.focus();button?.scrollIntoView({block:'center'});};
      target.prepend(back);
      const focus=[...target.querySelectorAll('input:not([disabled]),select:not([disabled]),button:not([data-settings-task-back]):not([disabled]),summary')].find(node=>node.getClientRects().length&&getComputedStyle(node).visibility!=='hidden');
      (focus||back).focus();(focus||back).scrollIntoView({block:'nearest'});
    } catch(error) {
      hooks.returnToSearch(original);status(error.message||'Editor unavailable. No settings changed.');
      const button=host.querySelector('[data-settings-task="'+task.id+'"]');button?.focus();button?.scrollIntoView({block:'center'});
    }
  }
  function mount(api){
    hooks=api;if(host)return;input=document.getElementById('settingsSearchInput');const pane=document.getElementById('settingsPane');if(!input||!pane)return;
    host=make('section');host.id='settingsTaskResults';host.hidden=true;host.setAttribute('aria-label','Suggested configuration tasks');
    host.append(make('h3','Suggested tasks'));const message=make('p');message.setAttribute('role','status');host.append(message);
    const refresh=make('button','Refresh current state');refresh.type='button';refresh.className='settings-action-btn';refresh.dataset.settingsTaskRefresh='';refresh.onclick=()=>search(input.value);host.append(refresh,make('ul'));pane.prepend(host);
    input.addEventListener('input',()=>search(input.value));
    window.addEventListener('storage',()=>{if(!host.hidden)status('Preferences changed in another tab. Refresh current state; nothing has been changed by search.');});
  }
  window.cccSettingsTaskSearch=Object.freeze({mount,search,match,tasks});
})();
