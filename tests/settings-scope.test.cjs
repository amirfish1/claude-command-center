'use strict';
// Actual Settings markup, grouping/search and canonical contextual composition.
// Requests are forbidden: scope guidance must not read values or run checks.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'settings-scope-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
function between(a,b){const start=app.indexOf(a),end=app.indexOf(b,start+a.length);assert(start>=0&&end>start,a);return app.slice(start,end);}
const grouping=between('  function organizeSettings() {','  organizeSettings();')+'  organizeSettings();';
const search=between('  function ensureSettingsRowCrumb(','  function clearSettingsSearch(');
const manager=between('  async function openQueueManager(','  // Routes a ticket');
const queueMarkup=manager.slice(manager.indexOf('    modal.innerHTML ='),manager.indexOf('    document.body.appendChild(modal);'));
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const preference=between('    settingsPreferenceClick = function handleSettingsPreferenceClick(e)', "    $settingsModal.addEventListener('click', settingsPreferenceClick);");
const dynamicFiles=[['static/free-settings.js','free','fsProviders'],['static/fleet-failover.js','limits','cccFleetLimitsPanel'],['static/leftover.js','leftover','loTasks'],['static/free-key-wizard.js','free','wizard']];
const dynamic=dynamicFiles.map(([file,section,container])=>({file,section,container,tags:[...fs.readFileSync(path.join(root,file),'utf8').matchAll(/<(button|input|select|textarea|a)\b[^>]*>/g)].map(m=>m[0].replace(/'\s*\+\s*'/g,''))}));
const fixture=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/settings-scope.css"><link rel="stylesheet" href="/static/voice-setup.css"><style>body{display:block}#settingsModal{display:flex!important}#settingsPane>.settings-section:not(.is-active-section){display:none!important}#settingsPane>.settings-section.is-active-section{display:block!important}#contextPreferenceParking{display:none}#scopeExternal{display:none}</style><body><div id="scopeExternal"><div class="spawn-cwd-row"></div><div class="conv-pane"><div data-context-reader-slot></div></div></div><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');for(const id of ['settingsModal','networkModal','fedPeersModal','voiceModal','carModeModal'])document.body.append(parsed.getElementById(id));
const $settingsPane=document.getElementById('settingsPane'),$settingsModal=document.getElementById('settingsModal'),$settingsModalInner=$settingsModal.querySelector('.settings-modal'),$settingsEmptyState=document.getElementById('settingsEmptyState'),$settingsEmptyQuery=document.getElementById('settingsEmptyQuery');let _settingsSearchIndex=null;
function refreshSettingsQueues(){}function startEnginesHub(){}function closeSettingsModal(){}function clearSettingsSearch(){}function refreshAppearanceChecks(){}function showSettingsSavedPulse(){}function applyTheme(v){document.documentElement.dataset.theme=v;}let settingsPreferenceClick;
${grouping}
${search}
${preference}
$settingsModal.addEventListener('click',settingsPreferenceClick);
window.__show=id=>{$settingsModal.hidden=false;$settingsModal.classList.add('open');document.querySelectorAll('#settingsPane>.settings-section').forEach(n=>{const on=n.dataset.sectionId===id;n.classList.toggle('is-active-section',on);n.setAttribute('aria-hidden',String(!on));if(on){const d=n.querySelector(':scope>details');if(d)d.open=true;}});};window.__search=applySettingsSearch;
const external=document.getElementById('scopeExternal');external.append(parsed.getElementById('convSimpleActionsMenu'));const font=document.createElement('div');font.className='font-size-controls';for(const id of ['fontMinus','fontPlus'])font.append(parsed.getElementById(id));external.append(font);
const slot=document.createElement('div');slot.dataset.contextReaderSlot='';external.append(slot);
const q=document.createElement('div');q.className='fq-config-composer';const modal=q,queueChoices='',pathChoices='',githubRepoChoices='';${queueMarkup}external.append(q);
window.__dynamic=${JSON.stringify(dynamic).replace(/</g,'\\u003c')};
window.__dynamicMount=()=>{for(const sample of window.__dynamic){const section=document.createElement('section');section.className='settings-section';section.dataset.sectionId=sample.section;const root=document.createElement('div');root.id=sample.container;if(sample.container==='wizard')root.className='ccc-fkw-modal';section.append(root);document.getElementById('settingsPane').append(section);for(const tag of sample.tags){const t=document.createElement('template');t.innerHTML=tag+'</button></select></textarea></a>';const node=t.content.firstElementChild;if(node){node.dataset.scopeFixtureSource=sample.file;root.append(node);}}}};
window.__forbidden=[];window.fetch=(...args)=>{window.__forbidden.push('fetch');return Promise.reject(Error('No requests permitted'));};const put=Storage.prototype.setItem;Storage.prototype.setItem=function(...args){window.__forbidden.push('storage');return put.apply(this,args);};
</script><script src="/static/settings-context.js"></script><script src="/static/voice-setup.js"></script><script src="/static/service-connections.js"></script><script src="/static/engine-connection.js"></script><script>
window.cccSettingsContext.mount({closeSettings(){},openReader(){},openList(){},openQueue(){},openRepoPicker(){},refreshBranding(){},resetList(){},resetReader(){},preferenceClick:settingsPreferenceClick,refresh(){}});
window.cccVoiceSetup.mount({navigate(){},load(){window.cccVoiceSetup.loadFailed();},refreshVoices(){},loadOperator(){}});
window.cccServiceConnections.mount({navigate(){},refresh(){}});
window.cccEngineConnection.mount({refresh(){}});
</script><script src="/static/settings-scope.js"></script>`;
let browser,server,url;
test.before(async()=>{server=http.createServer((req,res)=>{const target=new URL(req.url,'http://fixture').pathname;if(target==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(fixture);return;}if(target.startsWith('/static/')&&/^\/static\/[a-z-]+\.(js|css)$/.test(target)){res.setHeader('Content-Type',target.endsWith('.js')?'text/javascript':'text/css');res.end(fs.readFileSync(path.join(root,target)));return;}res.statusCode=403;res.end();});await new Promise(r=>server.listen(0,'127.0.0.1',r));url='http://127.0.0.1:'+server.address().port;browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});});
test.after(async()=>{await browser?.close();if(server)await new Promise(r=>server.close(r));console.log('Scope screenshots:',scratch);});
async function open(t,width=1440){const ctx=await browser.createBrowserContext(),page=await ctx.newPage(),errors=[];t.after(async()=>{await ctx.close();assert.deepEqual(errors,[]);});page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(7000);await page.setViewport({width,height:1000});await page.goto(url,{waitUntil:'load'});await page.waitForFunction(()=>window.cccSettingsScope);await page.evaluate(()=>window.__show('appearance'));return page;}
test('source inventory covers all native Settings and contextual controls without reading or writing',async t=>{const page=await open(t);const missing=await page.evaluate(()=>cccSettingsScope.audit());console.log('Unclassified native controls:',JSON.stringify(missing));assert.deepEqual(missing,[]);assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);const count=await page.$$eval('[data-settings-scope]',n=>n.length);console.log('Annotated native controls:',count);assert(count>100);});
for(const width of [1440,390])test('ownership help stays hidden metadata; search and native theme handler '+width,async t=>{const page=await open(t,width);const summary='[data-section-id="appearance"] .settings-scope-help summary';assert.equal(await page.$eval(summary,n=>n.getClientRects().length),0);assert.match(await page.$eval('[data-section-id="appearance"] .settings-scope-help',n=>n.textContent),/This browser/);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:path.join(scratch,'ownership-'+width+'.png'),fullPage:true});await page.evaluate(()=>window.__search('theme'));assert.equal(await page.$eval(summary,n=>n.getClientRects().length),0);assert.equal(await page.$eval('[data-theme="light"]',n=>!!n.getClientRects().length),true);await page.click('[data-theme="light"]');assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-theme')),'light');assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');assert.deepEqual(await page.evaluate(()=>window.__forbidden),['storage']);});
test('distinct voice owners, repository and reader scope come from the shared catalog',async t=>{const page=await open(t,390);const specs=await page.evaluate(()=>Object.fromEntries(['settingsTtsVoiceDefault','voiceSelect','settingsVoiceCallBtnToggle','carModeAnthropicKey','repoAvatarUpload','fontPlus'].map(id=>{const n=document.getElementById(id);if(!n)throw Error('Missing '+id);const s=cccSettingsScope.describe(n);return[id,{scope:s.scope,owner:s.owner,text:document.getElementById(n.getAttribute('aria-describedby').split(' ').at(-1))?.textContent}];})));assert.equal(specs.settingsTtsVoiceDefault.scope,'browser');assert.equal(specs.voiceSelect.scope,'host');assert.equal(specs.settingsVoiceCallBtnToggle.scope,'browser');assert.equal(specs.carModeAnthropicKey.scope,'host');assert.equal(specs.repoAvatarUpload.scope,'repository');assert.match(specs.repoAvatarUpload.text,/across sessions and browsers/);assert.equal(specs.fontPlus.scope,'browser');assert.match(specs.fontPlus.text,/not a per-conversation/);assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);});
test('late controls are annotated; an unknown family stays visibly unverified and fails the audit',async t=>{const page=await open(t);await page.evaluate(()=>{const input=document.createElement('input');input.id='scopeNewUnreviewedControl';document.querySelector('[data-section-id="appearance"]').append(input);});await page.waitForSelector('[data-settings-scope="unverified"]');assert.deepEqual(await page.evaluate(()=>cccSettingsScope.audit().map(n=>n.id)),['scopeNewUnreviewedControl']);assert.match(await page.$eval('[data-section-id="appearance"] .settings-scope-help',n=>n.textContent),/Scope not verified/);await page.evaluate(()=>{const n=document.getElementById('scopeNewUnreviewedControl');n.dataset.theme='light';cccSettingsScope.mount();});assert.deepEqual(await page.evaluate(()=>cccSettingsScope.audit()),[]);});
test('native described-by help survives and annotation is idempotent after nodes move',async t=>{const page=await open(t);const result=await page.evaluate(()=>{const n=document.getElementById('voiceMaxMinutes');n.setAttribute('aria-describedby','nativeError');const moved=document.createElement('section');moved.className='settings-section';document.getElementById('settingsPane').append(moved);moved.append(n);cccSettingsScope.mount();const before=n.getAttribute('aria-describedby');cccSettingsScope.mount();const same=before===n.getAttribute('aria-describedby');return {same,native:before.startsWith('nativeError '),resolved:!!document.getElementById(before.split(' ').at(-1)),direct:document.querySelector('[data-section-id="appearance"]>:scope>details.settings-scope-help')!==null};});assert.equal(result.same,true);assert.equal(result.native,true);assert.equal(result.resolved,true);assert.equal(result.direct,false);});

test('dynamic known-control templates remain classified across optional Settings families',async t=>{const page=await open(t);await page.evaluate(()=>{window.__dynamicMount();cccSettingsScope.mount();});const missing=await page.evaluate(()=>cccSettingsScope.audit());console.log('Unclassified dynamic controls:',JSON.stringify(missing));assert.deepEqual(missing,[]);assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);});

test('contextual return actions override broad editor-family selectors',async t=>{const page=await open(t);const result=await page.evaluate(()=>{const button=document.createElement('button');button.className='settings-changes-return';document.getElementById('settingsModalFontSlot').append(button);cccSettingsScope.mount();return {scope:cccSettingsScope.describe(button).scope,help:document.getElementById(button.getAttribute('aria-describedby').split(' ').at(-1)).textContent};});assert.equal(result.scope,'one-off');assert.match(result.help,/navigate only/);});

for(const width of [1440,390])test('mixed voice ownership keeps native unavailable state and hidden guidance '+width,async t=>{const page=await open(t,width);await page.evaluate(()=>{window.__show('voice');cccVoiceSetup.loadFailed();});const summary='#voiceSetup-conversation .settings-scope-help summary';assert.equal(await page.$eval(summary,n=>n.getClientRects().length),0);const guide=await page.$eval('#voiceSetup-conversation .settings-scope-help',n=>n.textContent);assert.match(guide,/This CCC host/);assert.match(await page.$eval('#voiceSetup-reading .settings-scope-help',n=>n.textContent),/This browser/);assert.match(await page.$eval('#voiceSetupCallStatus',n=>n.textContent),/unavailable.*saving is disabled/);assert.equal(await page.$eval('#voiceSetupCallStatus',n=>!!n.getClientRects().length),true);assert.deepEqual(await page.evaluate(()=>cccSettingsScope.audit()),[]);assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.$eval('#voiceSetup-conversation',n=>n.scrollIntoView({block:'start'}));await page.screenshot({path:path.join(scratch,'ownership-voice-'+width+'.png'),fullPage:true});});


test('late debug row-style palette uses reader guidance without expanding its compact grid',async t=>{
  const page=await open(t);
  await page.evaluate(()=>{
    const palette=document.createElement('div');palette.dataset.role='conv-rowstyle-palette';
    for(const name of ['divider','zebra','cards']){const button=document.createElement('button');button.dataset.convRowstyleId=name;button.textContent=name;palette.append(button);}
    document.body.append(palette);
  });
  await page.waitForFunction(()=>[...document.querySelectorAll('[data-role="conv-rowstyle-palette"] button')].every(n=>n.dataset.settingsScope==='browser-workspace'));
  const result=await page.evaluate(()=>{
    const palette=document.querySelector('[data-role="conv-rowstyle-palette"]');
    return {children:palette.children.length, descriptions:[...palette.children].map(n=>{
      const description=document.getElementById(n.getAttribute('aria-describedby'));
      return {inReader:!!description?.closest('#settingsModalFontSlot'),text:description?.textContent};
    }),audit:cccSettingsScope.audit()};
  });
  assert.equal(result.children,3);
  for(const description of result.descriptions){assert.equal(description.inReader,true);assert.match(description.text,/This browser/);}
  assert.deepEqual(result.audit,[]);
  assert.deepEqual(await page.evaluate(()=>window.__forbidden),[]);
});

test('a large transcript render never searches for Settings controls',async t=>{
  // CCC-143: per-record subtree searches here froze the page ~10s per open.
  const page=await open(t);
  const result=await page.evaluate(async()=>{
    const view=document.createElement('div');view.className='conversations-view';document.body.append(view);
    await new Promise(r=>setTimeout(r));
    const real=Element.prototype.querySelector;let searches=0;
    Element.prototype.querySelector=function(...args){searches++;return real.apply(this,args);};
    for(let i=0;i<500;i++){const turn=document.createElement('div');view.append(turn);for(let j=0;j<4;j++)turn.append(document.createElement('p'));}
    await new Promise(r=>setTimeout(r));
    const during=searches;
    const late=document.createElement('button');late.id='fontPlus';document.body.append(late);
    await new Promise(r=>setTimeout(r));
    Element.prototype.querySelector=real;view.remove();
    return {during,annotated:late.hasAttribute('aria-describedby')};
  });
  assert.equal(result.during,0);
  assert.equal(result.annotated,true);
});
