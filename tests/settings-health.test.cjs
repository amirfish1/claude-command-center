'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'settings-health-'));
const app=require('./legacy-dashboard-source.cjs'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
function between(a,b){const i=app.indexOf(a),j=app.indexOf(b,i+a.length);assert(i>=0&&j>i,a);return app.slice(i,j);}
const nativeSystem=between('  function _sysFocusables() {','  // ── Restart controls');
const nativeResource=between('  let _sysHealthPollPromise = null;','  // ── System Processes');
const nativeFormat=between('  function _shFmtMB(', '  function _ensureSysHealthModal(');
const integration=between('  window.cccSettingsHealth?.mount({','  // Trigger check on load');
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/settings-health.css"><link rel="stylesheet" href="/static/engine-connection.css"><link rel="stylesheet" href="/static/service-connections.css"><style>body{display:block}#sysModal.open{display:flex!important}#settingsModal .settings-modal{width:min(1100px,96vw)}#settingsPane>.settings-section{display:none!important}#settingsPane>.settings-section.is-active-section{display:block!important}#sysDialog [hidden]{display:none!important}#sysDialog{padding:20px}</style><button id="opener" style="min-height:44px">System</button><button id="sidebarMoreBtn">More</button><script src="/static/settings-health.js"></script><script src="/static/engine-connection.js"></script><script src="/static/service-connections.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');for(const id of ['sysModal','settingsModal'])document.body.append(parsed.getElementById(id));
const $sysModal=document.getElementById('sysModal'),$sysDialog=document.getElementById('sysDialog'),$sysChip=null,$sysBackdrop=document.getElementById('sysBackdrop'),$settingsModal=document.getElementById('settingsModal');
let _sysReturnFocus=null,_sysLastRollupLevel='',_sysRestartInFlight=false,_sysPollTimer=null,_sysSpawnedTickTimer=null,_sysHealthTimer=null,_sysProcessesTimer=null,_sysJobsTimer=null,settingsSearchFocusTimer=null;
const nativeFetch=fetch;window.fetch=(url,options)=>{if(url==='/api/system-health')window.__resourcePriority=options?.cccPriority;return nativeFetch(url,options);};
window.__calls=[];const isImeKey=()=>false;function showOpToast(){}function sysRefresh(){window.__calls.push('services');return Promise.resolve({});}function renderSysSpawnedList(){}function _pollSystemProcesses(){}function refreshControlPlaneStatus(){window.__calls.push('worker');}function _wtQueueRefresh(){window.__calls.push('queue');}
${nativeFormat}
${nativeResource}
${nativeSystem}
function openSettingsModal(){$settingsModal.hidden=false;$settingsModal.classList.add("open");}function closeSettingsModal(){$settingsModal.hidden=true;$settingsModal.classList.remove("open");}function clearSettingsSearch(){}
function setActiveSettingsRailSection(section){document.querySelectorAll('#settingsPane>.settings-section').forEach(n=>n.classList.toggle('is-active-section',n.dataset.sectionId===section));document.querySelector('#settingsSection-'+section+' details')?.setAttribute('open','');}
const navigate=section=>{openSettingsModal();setActiveSettingsRailSection(section);};
window.cccEngineConnection.mount({navigate:()=>navigate('engines'),sessionEngines:['claude','codex'],workerEngines:['claude','codex'],checking:()=>false,loginState:()=>null,terminalAvailable:()=>false,refresh:()=>window.__calls.push('engine-explicit')});
window.cccEngineConnection.render(['claude','codex'].map(engine=>({engine,meta:{label:engine,docs:'https://example.test/setup'},installed:engine==='claude',auth:null,disabled:false,limited:false,isDefault:engine==='claude',byokReady:false})),{});
window.cccServiceConnections.mount({navigate:()=>navigate('vault'),newCredential(){},refresh(){}});
// Native staged-policy implementation is verified by its dedicated actual API
// suite; this fixture checks navigation remains staged and receives no preset.
window.cccRoutingPolicy={open:async(...args)=>{window.__calls.push(['policy',...args]);navigate('routing');document.getElementById('routingPolicyContext').focus();}};
${integration}
document.getElementById('opener').onclick=()=>sysOpenModal(document.getElementById('opener'),'health');
document.getElementById('settingsModalClose').onclick=()=>{closeSettingsModal();document.getElementById('opener').focus();};
window.__settings=()=>{sysCloseModal();navigate('maintenance');};
</script>`;
let browser,child,url;
test.before(async()=>{
 fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);fs.writeFileSync(path.join(scratch,'spawn-defaults.json'),JSON.stringify({engine:'codex',reasoning_effort:'max',future_policy:{keep:true}}));
 child=spawn('python3',['-u','tests/fixtures/settings_health_server.py',scratch],{cwd:root,env:{...process.env,PYTHONPATH:root,CCC_EPHEMERAL:'1'}});let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});url=(await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw Error(stderr);})])).url;
 browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Health screenshots:',scratch);});
async function open(t,width=1440,{fail=false,stale=false,resourceFail=false}={}){
 for(const [name,on]of [['fail',fail],['stale',stale],['resource-fail',resourceFail]]){const p=path.join(scratch,name);if(on)fs.writeFileSync(p,'1');else fs.rmSync(p,{force:true});}
 const context=await browser.createBrowserContext(),page=await context.newPage();t.after(()=>context.close());await page.setViewport({width,height:900});const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);page.on('request',r=>{if(new URL(r.url()).origin!==url){r.abort();return;}requests.push({method:r.method(),url:new URL(r.url()).pathname});r.continue();});
 await page.goto(url);await page.click('#opener');if(errors.length)throw Error(JSON.stringify(errors));await page.waitForFunction(()=>document.getElementById('sysHealthBody')?.textContent.includes('Memory')||document.getElementById('sysHealthBody')?.textContent.includes('unavailable'));
 assert.deepEqual(errors,[]);return{page,requests,errors};
}

for(const width of [1440,390])test('Health shows measured resources directly without setup, prose, or destination buttons at '+width,async t=>{
 const before=fs.readFileSync(path.join(scratch,'spawn-defaults.json'),'utf8'),{page,requests}=await open(t,width);
 assert.equal(await page.$eval('#sysTabContentHealth',n=>getComputedStyle(n).display),'block');
 assert.deepEqual(await page.evaluate(()=>window.__calls),[]);
 assert.equal(await page.evaluate(()=>window.__resourcePriority),'user');
 assert.deepEqual(requests.filter(r=>r.url.startsWith('/api/')).map(r=>r.url),['/api/system-health']);
 assert.match(await page.$eval('#sysHealthBody',n=>n.textContent),/4.0 GB \/ 8.0 GB/);
 assert.match(await page.$eval('#sysHealthBody',n=>n.textContent),/1.25 \/ 1.50 \/ 1.75/);
 assert.match(await page.$eval('#sysHealthBody',n=>n.textContent),/PID 104/);
 assert.match(await page.$eval('#sysHealthBody',n=>n.textContent),/Example app/);
 assert.equal(await page.$$eval('#sysTabContentHealth [data-health-repair],#sysTabContentHealth [data-open],#sysHealthBody button,#sysHealthBody a,#sysHealthBody .sh-def',ns=>ns.length),0);
 assert.deepEqual(await page.$$eval('#sysTabContentHealth button',ns=>ns.map(n=>n.textContent)),['Refresh']);
 assert.doesNotMatch(await page.$eval('#sysTabContentHealth',n=>n.textContent),/Other evidence|CLI detected|Open engine setup|Health checks|shared app-server processes|Reaping SIGTERM/);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(await page.$$eval('#sysHealthBody .sh-sesscol',ns=>ns.filter(n=>n.scrollWidth>n.clientWidth+1).map(n=>n.textContent)),[]);
 assert.deepEqual(await page.$$eval('#sysDialog .sys-tab',ns=>ns.filter(n=>{const r=n.getBoundingClientRect(),p=n.parentElement.getBoundingClientRect();return r.right>p.right+1||r.left<p.left-1;}).map(n=>n.id)),[]);
 await page.screenshot({path:path.join(scratch,'health-'+width+'.png'),fullPage:true});
 assert.equal(fs.readFileSync(path.join(scratch,'spawn-defaults.json'),'utf8'),before);
});
async function settings(page){
 await page.evaluate(()=>window.__settings());
 await page.waitForFunction(()=>!document.getElementById('settingsHealthRefresh').disabled&&document.getElementById('settingsHealthStatus').textContent!=='Not checked');
}
test('Settings retains its configuration summary and canonical fix destinations without writes',async t=>{
 const{page,requests}=await open(t,390);await settings(page);
 assert.equal(await page.$eval('#sysModal',n=>n.classList.contains('open')),false);
 assert.ok(await page.$('#settingsSection-maintenance #settingsHealthInline'));
 assert.match(await page.$eval('[data-health-inline="engine-codex"]',n=>n.textContent),/CLI not detected/);
 assert.match(await page.$eval('[data-health-inline="policy-reasoning_effort"]',n=>n.textContent),/Saved effort/);
 assert.equal(await page.$$eval('[data-health-inline="connections"],[data-health-inline="engine-claude"]',ns=>ns.length),0);
 await page.focus('#health-fix-engine-codex');await page.keyboard.press('Enter');
 assert.equal(await page.$eval('#settingsSection-engines',n=>n.classList.contains('is-active-section')),true);
 assert.equal(requests.some(r=>r.method!=='GET'),false);
 assert.deepEqual(requests.filter(r=>r.url.startsWith('/api/')).map(r=>r.url),['/api/system-health','/api/settings/health']);
});
test('configuration checks refresh failure and expiry never become a passing observation',async t=>{
 const{page,requests}=await open(t,390);await settings(page);
 fs.writeFileSync(path.join(scratch,'fail'),'1');await page.click('#settingsHealthRefresh');
 await page.waitForFunction(()=>document.getElementById('settingsHealthStatus').textContent==='Unavailable');
 assert.equal(await page.$$eval('[data-health-inline]',ns=>ns.length),0);
 fs.rmSync(path.join(scratch,'fail'));await page.focus('#settingsHealthRefresh');await page.keyboard.press('Enter');
 await page.waitForSelector('#health-fix-engine-codex');
 assert.equal(await page.evaluate(()=>document.activeElement.id),'settingsHealthRefresh');
 const count=requests.length;
 await page.evaluate(()=>{const now=Date.now;Date.now=()=>now()+31000;document.dispatchEvent(new Event('visibilitychange'));});
 assert.equal(await page.$eval('#settingsHealthStatus',n=>n.textContent),'Stale');
 assert.equal(await page.$$eval('[data-health-inline]',ns=>ns.length),0);
 assert.equal(requests.length,count);
 assert.equal(requests.some(r=>r.method!=='GET'),false);
});
test('unknown or malformed configuration reads stay unavailable and can be retried',async t=>{
 const{page}=await open(t,1440,{fail:true});await settings(page);
 assert.equal(await page.$eval('#settingsHealthStatus',n=>n.textContent),'Unavailable');
 fs.rmSync(path.join(scratch,'fail'));await page.click('#settingsHealthRefresh');await page.waitForSelector('#health-fix-engine-codex');
 await page.evaluate(()=>{const original=fetch;window.fetch=(...args)=>args[0]==='/api/settings/health'?Promise.resolve({ok:true,json:async()=>({checks:[]})}):original(...args);});
 await page.click('#settingsHealthRefresh');await page.waitForFunction(()=>document.getElementById('settingsHealthStatus').textContent==='Unavailable');
 assert.equal(await page.$$eval('[data-health-inline]',ns=>ns.length),0);
});
test('unavailable resource read clears values and the local Refresh action recovers without writes',async t=>{
 const{page,requests}=await open(t,390,{resourceFail:true});
 assert.equal(await page.$eval('#sysHealthBody',n=>n.textContent),'Resource snapshot unavailable.');
 fs.rmSync(path.join(scratch,'resource-fail'));await page.click('#sysHealthRefreshBtn');
 await page.waitForFunction(()=>document.getElementById('sysHealthBody').textContent.includes('Memory'));
 assert.equal(requests.filter(r=>r.url==='/api/system-health').length,2);
 assert.equal(requests.some(r=>r.method!=='GET'),false);
});
test('native System keyboard trap and Escape still return to the visible opener',async t=>{
 const{page}=await open(t,390);
 await page.focus('#sysHealthRefreshBtn');await page.keyboard.press('Tab');
 assert.equal(await page.evaluate(()=>document.activeElement.id),'sysCloseBtn');
 await page.keyboard.down('Shift');await page.keyboard.press('Tab');await page.keyboard.up('Shift');
 assert.equal(await page.evaluate(()=>document.activeElement.id),'sysHealthRefreshBtn');
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'opener');
});
test('System close skips an occluded opener and returns to mobile More',async t=>{
 const{page}=await open(t,390);await page.evaluate(()=>{const opener=document.getElementById('opener');opener.style.cssText='position:fixed;left:10px;top:10px;width:80px;height:44px;z-index:1';const more=document.createElement('button');more.textContent='More';more.dataset.mobileNavMenu='';more.style.cssText='position:fixed;left:10px;top:10px;width:80px;height:44px;z-index:2';document.body.append(more);});await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.matches('[data-mobile-nav-menu]')),true);
});
test('native Settings defaults refresh uses the nonmigrating policy GET',async t=>{
 const{page}=await open(t);
 const load=between('  async function loadSpawnDefaults(options) {','  let _emnInstalled');
 const refresh=between('  function refreshSpawnEngineValue() {','  function refreshMonthlyClaudePlanInput() {');
 const urls=await page.evaluate(async({load,refresh})=>{
  return new Function('return (async()=>{let _spawnDefaultsLoaded=false;const urls=[];const fetch=async url=>{urls.push(url);return {ok:true,json:async()=>({ok:true})};};function mergeSpawnDefaults(){}function syncSpawnEngineDependentUi(){}function renderSpawnDefaultsInline(){}'+load+refresh+'refreshSpawnEngineValue();await Promise.resolve();await Promise.resolve();await Promise.resolve();return urls;})()')();
 },{load,refresh});
 assert.deepEqual(urls,['/api/spawn-defaults?policy=1']);
});

test('a stalled resource request aborts and a later Refresh recovers',async t=>{
 const{page}=await open(t);
 await page.evaluate(()=>{
   const originalFetch=fetch,originalTimer=setTimeout;
   window.__restoreResource=()=>{window.fetch=originalFetch;window.setTimeout=originalTimer;};
   window.fetch=(url,options)=>url==='/api/system-health'?new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true})):originalFetch(url,options);
   window.setTimeout=(fn,ms,...args)=>originalTimer(fn,ms===10000?5:ms,...args);
   document.getElementById('sysHealthRefreshBtn').click();
 });
 await page.waitForFunction(()=>document.getElementById('sysHealthBody').textContent==='Resource snapshot unavailable.');
 await page.evaluate(()=>window.__restoreResource());await page.click('#sysHealthRefreshBtn');
 await page.waitForFunction(()=>document.getElementById('sysHealthBody').textContent.includes('Memory'));
});
