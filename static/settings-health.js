/* Configuration observations belong in Settings > System. This module reads
 * saved policy and existing caches only; it never probes providers or writes. */
(() => {
  'use strict';
  let api, root, busy=false, snapshot=null, expires=0, failed=false, expiryTimer;
  const get=id=>document.getElementById(id);
  const make=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
  const states=new Set(['issue','unchecked','stale','unavailable','observed']);
  function render(){
    const host=get('settingsHealthInline');if(!host)return;
    const focus=host.contains(document.activeElement)?document.activeElement.id:'';
    const fresh=snapshot&&Date.now()<=expires;
    const issues=fresh?snapshot.checks.filter(row=>row.state==='issue'):[];
    const head=make('div');head.className='settings-row';
    const main=make('div');main.className='settings-row-main';main.append(Object.assign(make('div','Health checks'),{className:'settings-row-label'}));
    const label=busy?'Checking…':failed?'Unavailable':!snapshot?'Not checked':!fresh?'Stale':issues.length?issues.length+(issues.length===1?' issue':' issues'):'No confirmed issues';
    const state=make('span',label);state.id='settingsHealthStatus';state.setAttribute('role','status');state.className='settings-health-inline-state';state.dataset.state=issues.length?'issue':fresh&&!failed?'ok':'unknown';
    const refreshButton=make('button','Refresh checks');refreshButton.id='settingsHealthRefresh';refreshButton.type='button';refreshButton.className='settings-action-btn';refreshButton.disabled=busy;
    const control=make('div');control.className='settings-row-control';control.append(state,refreshButton);head.append(main,control);
    const rows=issues.map(row=>{
      const item=make('div');item.className='settings-row settings-health-inline-issue';item.dataset.healthInline=row.id;
      const body=make('div');body.className='settings-row-main';body.append(Object.assign(make('div',row.label),{className:'settings-row-label'}),Object.assign(make('div',row.message),{className:'settings-row-desc'}));item.append(body);
      const target=row.repair.startsWith('engine')?'engines':row.repair==='policy'?'defaults':row.repair==='connections'?'vault':row.repair==='capabilities'?'capabilities':'';
      if(target){const fix=make('button','Fix');fix.type='button';fix.id='health-fix-'+row.id;fix.className='settings-action-btn';fix.dataset.healthInlineFix=target;const c=make('div');c.className='settings-row-control';c.append(fix);item.append(c);}
      return item;
    });
    host.replaceChildren(head,...rows);
    if(focus&&document.activeElement===document.body)(get(focus)||get('settingsHealthRefresh'))?.focus({preventScroll:true});
  }
  async function refresh(){
    if(busy||!root)return;const initiated=document.activeElement===get('settingsHealthRefresh');busy=true;render();
    try{
      const r=await fetch('/api/settings/health',{cache:'no-store'});if(!r.ok)throw Error();
      const data=await r.json();
      if(!Array.isArray(data.checks)||!data.checks.length||data.checks.some(row=>!row||typeof row.id!=='string'||typeof row.label!=='string'||typeof row.message!=='string'||!states.has(row.state)||typeof row.repair!=='string'||!(/^(engines|engine:[a-z]+|services|policy|connections|capabilities)$/).test(row.repair)))throw Error();
      snapshot=data;expires=Date.now()+30000;failed=false;clearTimeout(expiryTimer);expiryTimer=setTimeout(render,30001);
    }catch(_){expires=0;failed=true;}
    finally{busy=false;render();if(initiated&&document.activeElement===document.body&&get('settingsHealthRefresh')?.getClientRects().length)get('settingsHealthRefresh').focus();}
  }
  function mount(integration){
    const maintenance=get('settingsSection-maintenance'),modal=get('settingsModal');if(root||!maintenance||!modal)return;
    api=integration;root=make('section');root.id='settingsHealth';root.setAttribute('aria-label','Configuration health checks');root.dataset.keywords='health checks doctor repair installation routes diagnostics';
    const inline=make('div');inline.id='settingsHealthInline';root.append(inline);maintenance.prepend(root);render();
    root.addEventListener('click',e=>{const fix=e.target.closest('[data-health-inline-fix]');if(fix)api.navigate(fix.dataset.healthInlineFix);else if(e.target.closest('#settingsHealthRefresh'))refresh();});
    const onOpen=()=>{if(maintenance.classList.contains('is-active-section')&&!modal.hidden&&(!snapshot||Date.now()>expires))refresh();};
    new MutationObserver(onOpen).observe(maintenance,{attributes:true,attributeFilter:['class']});
    new MutationObserver(onOpen).observe(modal,{attributes:true,attributeFilter:['hidden']});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden&&snapshot&&Date.now()>expires)render();});
  }
  window.cccSettingsHealth=Object.freeze({mount,refresh});
})();
