/* A single engine connection workflow. Native app.js handlers own all actions. */
(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let api, host, selected = '', rows = [], context = {}, mounted = false, panelSignature = '';

  function assessment(row) {
    if (!row) return {kind:'unchecked', label:'Status unavailable', session:false};
    if (!row.installed) return {kind:'setup', label:'Installation needed', session:false};
    if (row.auth === false) return {kind:'setup', label:'Sign-in not detected', session:false};
    if (row.disabled) return {kind:'off', label:'Disabled in CCC', session:false};
    if (row.auth !== true) return {kind:'unchecked', label:'Sign-in unchecked', session:true};
    return {kind:'configured', label:'Local setup detected', session:true};
  }
  const button = (label, action, row, disabled=false, extra='') => '<button type="button" class="settings-action-btn" id="engineConnection-'+action+'" data-eng-act="'+action+'" data-eng="'+escape(row.engine)+'"'+(disabled?' disabled':'')+extra+'>'+escape(label)+'</button>';
  const command = (label, value) => value ? '<div class="eng-cmd"><span class="eng-cmd-label">'+label+'</span><code>'+escape(value)+'</code><button type="button" class="eng-cmd-copy" data-eng-copy="'+escape(value)+'">Copy</button></div>' : '';
  const section = (title, body, collapsed=false) => collapsed
    ? '<details class="engine-connection-step" data-connection-step="'+escape(title.split(' · ')[0])+'"><summary>'+title+'</summary>'+body+'</details>'
    : '<section class="engine-connection-step"><h3>'+title+'</h3>'+body+'</section>';

  function render(next, state={}) {
    if (!mounted) return;
    if(cachedOnly)state={...state,unavailable:!!state.unavailable || cachedUnavailable};
    rows=next;context=state;
    if (!cachedOnly && rows.length && !rows.some(row=>row.engine===selected)) selected=rows.find(row=>row.isDefault)?.engine || rows[0]?.engine || '';
    const select=get('engineConnectionSelect');
    const oldOptions=JSON.stringify([...select.options].map(option=>[option.value,option.textContent]));
    const options=rows.map(row=>[row.engine,row.meta.label+' · '+assessment(row).label]);
    const missing=!!selected && !rows.some(row=>row.engine===selected);
    if(cachedOnly && missing)options.unshift([selected,selected+' · status unavailable']);
    if (oldOptions!==JSON.stringify(options)) select.innerHTML=options.map(([id,label])=>'<option value="'+escape(id)+'">'+escape(label)+'</option>').join('');
    select.value=selected;select.disabled=!options.length;
    get('engineConnectionStatus').textContent=state.unavailable
      ? 'Could not refresh engine status. Retry below; the last snapshot is not current.'
      : 'This machine · Binary and local sign-in evidence from the existing engine doctor. Credentials and provider connectivity have not been validated.';
    if(cachedOnly)get('engineConnectionStatus').textContent='Cached snapshot only · '+(missing?'Requested engine status is not available. Choose an engine below or recheck. ':get('engineConnectionStatus').textContent+' ')+'Refresh is an explicit check; this handoff runs no probes.';
    get('engineConnectionStatus').classList.toggle('is-error',!!state.unavailable);
    const list=get('engList'), row=rows.find(item=>item.engine===selected);
    if (!row) { panelSignature='';list.textContent='No engine status available. Recheck to retry.';return; }
    const oldFocus=list.contains(document.activeElement)?document.activeElement.id:'';
    const disclosureState=list.querySelector('#engineConnectionPanel')?.dataset.engine===row.engine
      ? new Map([...list.querySelectorAll('details')].map(node=>[node.dataset.connectionStep||'availability',node.open])) : new Map();
    const loginPanel=list.querySelector('.onb-login-panel');
    const loginFocus=loginPanel?.contains(document.activeElement)?document.activeElement:null;

    const found=assessment(row), limited=row.limited, unavailable=!!state.unavailable;
    const checking=api.checking?.(row.engine), login=api.loginState?.(row.engine);
    const terminal=api.terminalAvailable?.()!==false;
    const note=state.notes?.[row.engine];
    const signature=JSON.stringify([row,unavailable,!!checking,note,!!login?.running]);
    if(signature===panelSignature && list.querySelector('#engineConnectionPanel'))return;
    panelSignature=signature;
    if(loginPanel)loginPanel.remove();
    const links=[['Setup guide',row.meta.docs],...(row.meta.links||[])].filter(([,url])=>/^https:\/\//.test(url||''));
    let install=row.installed?'<p>CLI detected'+(row.version?' · '+escape(row.version):'')+'.</p>'
      : '<p>Install on the machine running CCC'+(terminal?' using its terminal.':' in a host terminal. This dashboard cannot open a visible terminal here.')+'</p>'
        +command('Install',row.install)+(row.install?button(terminal?'Install in host terminal':'Copy install command','install',row,unavailable):'<p>No automatic install command is registered. Follow the setup guide.</p>');
    let auth=!row.installed?'<p>Install first, then return here to sign in.</p>'
      : '<p>'+(row.auth===true?'Local sign-in evidence detected. This does not establish that the credential is valid or the provider is reachable.'
        : row.auth===false?'No local sign-in evidence detected. Sign in, then recheck.': 'CCC has no sign-in probe for this engine. Use its native authentication or provider setup; a session must confirm access.')+'</p>'
        +command('Sign in',row.login)
        +(row.login?button(row.auth===true?'Sign in / switch account':row.canTerminal?'Sign in':'Copy sign-in command','login',row,unavailable||!!login?.running):'<p>Authentication is managed by this CLI or its provider configuration. Use the setup guide.</p>')
        +(row.byokReady?'<p>Optional provider profiles are managed in Connections; a saved profile is not a connection test.</p><button type="button" class="settings-action-btn" data-settings-go="api-keys">Manage provider keys</button>':'')
        +'<div id="engineConnectionLoginHost"></div>';
    const test='<p>Recheck refreshes binary and sign-in evidence only. It sends no prompt and does not prove a usable provider connection.</p>'
      +button('Recheck installation & sign-in','verify',row,!!checking)
      +(row.engine==='kimi'?'<p>Kimi also supports an explicit ACP test. It starts a CLI process and opens a test session without a prompt.</p>'+button('Test Kimi ACP connection','test',row,unavailable||!found.session||!!checking):'<p>A live connection test is not available here for this engine. Continue to the composer and send a session prompt when you choose.</p>')
      +(note?'<p id="engineConnectionOutcome" role="status" class="settings-row-note is-'+escape(note.kind)+'">'+escape(note.text)+'</p>':'');
    const defaultBody='<p>Optional · Review session and worker routes in Launch policy. Nothing is saved until you preview and apply there. Return here to continue with this engine.</p>'
      +button('Review session default','default',row,unavailable||!api.sessionEngines.includes(row.engine))
      +button('Review worker default','worker',row,unavailable||!api.workerEngines.includes(row.engine));
    const launch='<p>'+(api.sessionEngines.includes(row.engine)
      ? (found.session?'Open the existing new-session composer with '+escape(row.meta.label)+'. Choose a repository and review model, effort and prompt before sending.':'Complete installation/sign-in and enable this engine before opening a session.')
      : 'This engine is supported for workers only in CCC. Review its worker route in Launch policy; it has no interactive session entry here.')+'</p>'
      +(limited?'<p class="engine-connection-warning">The last usage snapshot reports a limit. Runtime quota or fallback decisions may still prevent this engine from running.</p>':'')
      +(api.sessionEngines.includes(row.engine)?button('Continue to new session','session',row,unavailable||!found.session):'')
      +'<p>Opening the composer follows native behavior, which may prewarm an agent process. This setup never sends a prompt automatically or changes saved defaults.</p>';
    list.innerHTML='<div id="engineConnectionPanel" class="engine-connection-panel" data-engine="'+escape(row.engine)+'"><h2>'+escape(row.meta.label)+'</h2><p>'+escape(found.label)+'</p>'
      +section(row.installed?'Install · CLI detected':'Install',install,row.installed)+section(row.auth===true?'Authenticate · local sign-in evidence detected':'Authenticate',auth,row.auth===true||!row.installed)+section('Check connection',test)+section('Choose defaults',defaultBody)+section('Use this engine',launch)
      +'<details class="engine-connection-details"><summary>Engine details & availability</summary><p>'+escape(row.bin||'No binary detected')+'</p>'
      +(row.email?'<p>Account: '+escape(row.email)+'</p>':'')+(row.usage?api.usageHtml(row):'<p>No current usage signal for this engine.</p>')
      +'<p>Availability controls which engine pickers offer this engine; it does not install or sign in.</p><button type="button" id="engineConnectionAvailability" class="settings-toggle'+(row.disabled?'':' is-on')+'" role="switch" aria-checked="'+!row.disabled+'" data-eng-enable="'+escape(row.engine)+'" aria-label="Use '+escape(row.meta.label)+' in CCC"><span class="settings-toggle-track"><span class="settings-toggle-thumb"></span></span></button></details>'
      +'<div class="eng-links">'+links.map(([label,url])=>'<a class="eng-link" target="_blank" rel="noopener noreferrer" href="'+escape(url)+'">'+escape(label)+' ↗</a>').join('')+'</div></div>';
    for(const node of list.querySelectorAll('details')){const key=node.dataset.connectionStep||'availability';if(disclosureState.has(key))node.open=disclosureState.get(key);}
    if(login?.panel)get('engineConnectionLoginHost')?.append(login.panel);
    if(loginFocus?.isConnected)loginFocus.focus({preventScroll:true});
    else if(oldFocus)get(oldFocus)?.focus({preventScroll:true});
  }
  let cachedOnly=false,cachedUnavailable=false,openRequest=0;
  function open(engine, options={}) {
    if (!mounted) return false;
    if(engine)selected=engine;
    const request=++openRequest;
    cachedOnly=!!options.cachedOnly;
    cachedUnavailable=false;
    if(cachedOnly) { const observed=Date.parse(options.observedAt || '');cachedUnavailable=!!options.unavailable || !Number.isFinite(observed) || Date.now()-observed>300000 || observed>Date.now()+60000; }
    api.navigate(options);render(rows,context);
    if(!cachedOnly&&engine&&!rows.some(row=>row.engine===engine))get('engineConnectionStatus').textContent='Requested engine status is not available. Choose an engine below or recheck.';
    requestAnimationFrame(()=>{if(request!==openRequest)return;get('engineConnectionSelect').scrollIntoView({block:'nearest'});get('engineConnectionSelect').focus({preventScroll:true});});
    return true;
  }
  function policyReturn(engine=selected) {
    const editor=get('routingPolicyEditor');if(!editor)return;
    let back=get('engineConnectionPolicyReturn');
    if(!back){back=document.createElement('button');back.id='engineConnectionPolicyReturn';back.type='button';back.className='settings-action-btn';editor.prepend(back);}
    back.textContent='Return to engine setup · policy draft kept';
    back.onclick=()=>open(engine);
  }
  function startSession(engine) {
    const row=rows.find(item=>item.engine===engine);
    if(!row||context.unavailable||!assessment(row).session||!api.sessionEngines.includes(engine))return;
    api.session(engine);
  }
  function loginHost(engine) {
    if(engine!==selected)open(engine);
    return get('engineConnectionLoginHost');
  }
  function route() {
    let hash;try{hash=decodeURIComponent(location.hash.slice(1));}catch(_){return;}
    const match=hash.match(/^(?:settings[/-])?(?:engines|engine-connection)(?:[/:]([a-z]+))?$/);
    if(match)open(match[1]);
    else if(['engList','onbCliList','onboardingModal','settingsSection-engines','settingsRailTab-engines','engFirstRunDefaults'].includes(hash))open();
  }
  function mount(integration) {
    if(mounted)return;host=get('settingsSection-engines');if(!host||!get('engList'))return;api=integration;host.dataset.engineConnectionMounted='true';
    const header=document.createElement('div');header.className='engine-connection';header.id='engineConnection';
    header.innerHTML='<h2>Connect an engine</h2><p>Choose one engine, complete its setup, check what CCC can verify, then continue to a session. Install and sign-in actions run on this machine; opening this flow changes no configuration.</p><label for="engineConnectionSelect">Engine</label><select id="engineConnectionSelect" class="bug-input" disabled></select><p id="engineConnectionStatus" role="status" aria-live="polite">Reading engine status…</p><button type="button" id="engineConnectionRefresh" class="settings-action-btn">Refresh engine status</button>';
    get('engList').before(header);
    for(const id of ['engHeroTitle','engListLabel']){const node=get(id);if(node)node.closest(id==='engHeroTitle'?'.eng-hero':'.eng-list-head').hidden=true;}
    get('engineConnectionSelect').addEventListener('change',event=>{selected=event.target.value;render(rows,context);});
    get('engineConnectionRefresh').addEventListener('click',()=>{cachedOnly=false;cachedUnavailable=false;api.refresh();});
    const old=get('onboardingModal');if(old){old.classList.remove('open');old.hidden=true;old.removeAttribute('role');old.removeAttribute('aria-modal');}
    mounted=true;
    host.addEventListener('click',event=>{if(event.target.closest('[data-policy-open]'))policyReturn();},true);
    window.addEventListener('hashchange',route);route();
  }
  window.cccEngineConnection=Object.freeze({mount,mounted:()=>mounted,cachedOnly:()=>cachedOnly,endCachedOnly:()=>{cachedOnly=false;openRequest++;},render,open,loginHost,startSession,policyReturn,assessment});
})();
