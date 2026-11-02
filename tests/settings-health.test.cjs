'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {spawn}=require('node:child_process'),{once}=require('node:events'),readline=require('node:readline');
const puppeteer=require('../require-puppeteer.js'),{findChromePath}=require('../puppeteer-browser-config.js');
const root=path.resolve(__dirname,'..'),scratch=fs.mkdtempSync(path.join(os.tmpdir(),'settings-health-'));
const app=fs.readFileSync(path.join(root,'static/app.js'),'utf8'),html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
function between(a,b){const i=app.indexOf(a),j=app.indexOf(b,i+a.length);assert(i>=0&&j>i,a);return app.slice(i,j);}
const nativeSystem=between('  function _sysFocusables() {','  // ── Restart controls');
const nativeResource=between('  let _sysHealthPollPromise = null;','  function _shBar(');
const integration=between('  window.cccSettingsHealth?.mount({','  // Trigger check on load');
const encoded=JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
const fixture=`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/settings-health.css"><link rel="stylesheet" href="/static/engine-connection.css"><link rel="stylesheet" href="/static/service-connections.css"><style>body{display:block}#sysModal.open{display:flex!important}#settingsModal .settings-modal{width:min(1100px,96vw)}#settingsPane>.settings-section{display:none!important}#settingsPane>.settings-section.is-active-section{display:block!important}#sysDialog [hidden]{display:none!important}#sysDialog{padding:20px}</style><button id="opener" style="min-height:44px">System</button><button id="sidebarMoreBtn">More</button><script src="/static/settings-health.js"></script><script src="/static/engine-connection.js"></script><script src="/static/service-connections.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');for(const id of ['sysModal','settingsModal'])document.body.append(parsed.getElementById(id));
const $sysModal=document.getElementById('sysModal'),$sysDialog=document.getElementById('sysDialog'),$sysChip=document.getElementById('opener'),$sysBackdrop=document.getElementById('sysBackdrop'),$settingsModal=document.getElementById('settingsModal');
let _sysReturnFocus=null,_sysLastRollupLevel='',_sysRestartInFlight=false,_sysPollTimer=null,_sysSpawnedTickTimer=null,_sysHealthTimer=null,_sysProcessesTimer=null,_sysJobsTimer=null,settingsSearchFocusTimer=null;
window.__calls=[];const isImeKey=()=>false;function showOpToast(){}function sysRefresh(){window.__calls.push('services');return Promise.resolve({});}function renderSysSpawnedList(){}function _pollSystemProcesses(){}function refreshControlPlaneStatus(){window.__calls.push('worker');}function _wtQueueRefresh(){window.__calls.push('queue');}
function _renderSystemHealth(d){document.getElementById('sysHealthBody').textContent='Inert resource snapshot';}
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
document.getElementById('opener').onclick=()=>sysOpenModal(document.getElementById('opener'));
document.getElementById('settingsModalClose').onclick=()=>{closeSettingsModal();document.getElementById('opener').focus();};
window.__legacy=()=>{navigate('maintenance');document.getElementById('settingsHealthOpen').click();};
</script>`;
let browser,child,url;
test.before(async()=>{
 fs.writeFileSync(path.join(scratch,'fixture.html'),fixture);fs.writeFileSync(path.join(scratch,'spawn-defaults.json'),JSON.stringify({engine:'codex',reasoning_effort:'max',future_policy:{keep:true}}));
 child=spawn('python3',['-u','tests/fixtures/settings_health_server.py',scratch],{cwd:root,env:{...process.env,PYTHONPATH:root,CCC_EPHEMERAL:'1'}});let stderr='';child.stderr.on('data',d=>stderr+=d);const lines=readline.createInterface({input:child.stdout});url=(await Promise.race([once(lines,'line').then(([line])=>JSON.parse(line)),once(child,'exit').then(()=>{throw Error(stderr);})])).url;
 browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();if(child){child.kill();await once(child,'exit');}console.log('Health screenshots:',scratch);});
async function open(t,width=1440,{fail=false,stale=false}={}){
 for(const [name,on]of [['fail',fail],['stale',stale]]){const p=path.join(scratch,name);if(on)fs.writeFileSync(p,'1');else fs.rmSync(p,{force:true});}
 const context=await browser.createBrowserContext(),page=await context.newPage();t.after(()=>context.close());await page.setViewport({width,height:900});const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));await page.setRequestInterception(true);page.on('request',r=>{if(new URL(r.url()).origin!==url){r.abort();return;}requests.push({method:r.method(),url:new URL(r.url()).pathname});r.continue();});
 await page.goto(url);await page.click('#opener');await page.waitForFunction(()=>document.getElementById('settingsHealthStatus')?.textContent.includes('cached observations')||document.getElementById('settingsHealthStatus')?.textContent.includes('unavailable'));
 assert.deepEqual(errors,[]);return{page,requests,errors};
}
for(const width of [1440,390])test('default health entry is read-only, actionable and fits '+width,async t=>{
 const before=fs.readFileSync(path.join(scratch,'spawn-defaults.json'),'utf8'),{page,requests}=await open(t,width);
 assert.equal(await page.$eval('#sysTabContentHealth',n=>getComputedStyle(n).display),'block');
 assert.deepEqual(await page.evaluate(()=>window.__calls),[]);
 assert.deepEqual(requests.filter(r=>r.url.startsWith('/api/')).map(r=>r.url),['/api/settings/health']);
 assert.match(await page.$eval('[data-health-check="engine-codex"]',n=>n.textContent),/Needs attention.*CLI not detected/s);
 assert.match(await page.$eval('[data-health-check="connections"]',n=>n.textContent),/Unchecked.*does not establish validity/s);
 assert.match(await page.$eval('[data-health-check="policy-reasoning_effort"]',n=>n.textContent),/Needs attention/);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(await page.$$eval('#sysDialog .sys-tab',ns=>ns.filter(n=>{const r=n.getBoundingClientRect(),p=n.parentElement.getBoundingClientRect();return r.right>p.right+1||r.left<p.left-1;}).map(n=>n.id)),[]);
 if(width===390)assert.deepEqual(await page.$$eval('#settingsHealth button',ns=>ns.filter(n=>n.getBoundingClientRect().height<44).map(n=>n.textContent)),[]);
 await page.screenshot({path:path.join(scratch,'health-'+width+'.png'),fullPage:true});
 assert.equal(fs.readFileSync(path.join(scratch,'spawn-defaults.json'),'utf8'),before);
});
test('keyboard engine repair opens selected canonical flow and returns to its issue',async t=>{
 const{page,requests}=await open(t,390);await page.focus('#health-repair-engine-codex');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.activeElement.id==='engineConnectionSelect');
 assert.equal(await page.$eval('#engineConnectionSelect',n=>n.value),'codex');assert.equal(await page.$eval('#sysModal',n=>n.classList.contains('open')),false);
 await page.focus('#settingsHealthReturn');await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>document.activeElement.id),'health-repair-engine-codex');
 assert.equal(requests.some(r=>r.method!=='GET'),false);
});
test('policy repair passes no preset and opens a staged destination without saving',async t=>{
 const{page,requests}=await open(t);await page.click('#health-repair-policy-reasoning_effort');assert.deepEqual(await page.evaluate(()=>window.__calls),[['policy','defaults']]);assert.equal(await page.evaluate(()=>document.activeElement.id),'routingPolicyContext');assert.equal(requests.some(r=>r.method!=='GET'),false);
});
test('connections repair opens canonical directory with no credential read',async t=>{
 const{page,requests}=await open(t,390);await page.click('#settingsHealthChecks summary');await page.click('#health-repair-connections');assert.equal(await page.$eval('#settingsSection-vault',n=>n.classList.contains('is-active-section')),true);assert.equal(await page.$eval('#serviceConnections',n=>!!n.getClientRects().length),true);assert.deepEqual(requests.filter(r=>r.url.startsWith('/api/')).map(r=>r.url),['/api/settings/health']);
});
test('explicit service repair uses native Services tab and focuses it',async t=>{
 const{page}=await open(t,390);await page.focus('#health-repair-worker');await page.keyboard.press('Enter');assert.deepEqual(await page.evaluate(()=>window.__calls),['services']);assert.equal(await page.evaluate(()=>document.activeElement.id),'sysRowWorker');
});
test('resources remain available only after explicit load',async t=>{
 const{page,requests}=await open(t);assert.equal(requests.some(r=>r.url==='/api/system-health'),false);await page.click('#sysTabContentHealth>details>summary');await page.click('#sysTabContentHealth>details>button');await page.waitForFunction(()=>document.getElementById('sysHealthBody').textContent==='Inert resource snapshot');assert.equal(requests.filter(r=>r.url==='/api/system-health').length,1);
});
test('failed health read is unavailable, retry recovers without writes',async t=>{
 const{page,requests}=await open(t,390,{fail:true});assert.equal(await page.$$eval('#settingsHealthChecks li',n=>n.length),0);assert.match(await page.$eval('#settingsHealthStatus',n=>n.textContent),/unavailable.*Retry/);fs.rmSync(path.join(scratch,'fail'));await page.focus('#settingsHealthRefresh');await page.keyboard.press('Enter');await page.waitForSelector('#health-repair-worker');assert.equal(await page.evaluate(()=>document.activeElement.id),'settingsHealthRefresh');assert.equal(requests.some(r=>r.method!=='GET'),false);
});
test('refresh failure marks prior evidence stale; no all-healthy wall',async t=>{
 const{page}=await open(t);fs.writeFileSync(path.join(scratch,'fail'),'1');await page.click('#settingsHealthRefresh');await page.waitForFunction(()=>document.getElementById('settingsHealthStatus').textContent.includes('unavailable'));assert.match(await page.$eval('[data-health-check="engine-codex"]',n=>n.textContent),/Stale.*expired/s);await page.screenshot({path:path.join(scratch,'unavailable.png'),fullPage:true});
});
test('expired native cache remains stale instead of a current failure',async t=>{
 const{page}=await open(t,390,{stale:true});assert.match(await page.$eval('[data-health-check="engine-codex"]',n=>n.textContent),/Stale/);assert.match(await page.$eval('[data-health-check="watchtower"]',n=>n.textContent),/Stale/);
});
test('legacy maintenance entry preserves preferences and leads to health, Escape returns',async t=>{
 const{page}=await open(t,390);await page.click('#sysCloseBtn');await page.evaluate(()=>window.__legacy());await page.waitForSelector('#settingsHealthRefresh');assert.equal(await page.$eval('#settingsModal',n=>n.hidden),true);await page.keyboard.press('Escape');assert.equal(await page.$eval('#sysModal',n=>n.classList.contains('open')),false);
});

test('native System Tab trap works on phone and Escape restores its visible opener',async t=>{
 const{page}=await open(t,390);
 await page.evaluate(()=>{window.__first=document.getElementById('sysCloseBtn');window.__last=document.querySelector('#sysTabContentHealth>details>summary');window.__last.focus();});
 await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>document.activeElement===window.__first),true);
 await page.keyboard.down('Shift');await page.keyboard.press('Tab');await page.keyboard.up('Shift');assert.equal(await page.evaluate(()=>document.activeElement===window.__last),true);
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'opener');
});
test('System close skips an occluded opener and returns to mobile More',async t=>{
 const{page}=await open(t,390);await page.evaluate(()=>{const opener=document.getElementById('opener');opener.style.cssText='position:fixed;left:10px;top:10px;width:80px;height:44px;z-index:1';const more=document.createElement('button');more.textContent='More';more.dataset.mobileNavMenu='';more.style.cssText='position:fixed;left:10px;top:10px;width:80px;height:44px;z-index:2';document.body.append(more);});await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.matches('[data-mobile-nav-menu]')),true);
});
test('clock expiry marks evidence stale without another request and preserves repair focus',async t=>{
 const{page,requests}=await open(t);await page.focus('#health-repair-worker');const count=requests.length;
 await page.evaluate(()=>{const now=Date.now;Date.now=()=>now()+31000;document.dispatchEvent(new Event('visibilitychange'));});
 assert.equal(await page.evaluate(()=>document.activeElement.id),'health-repair-worker');assert.match(await page.$eval('[data-health-check="worker"]',n=>n.textContent),/Stale/);assert.equal(requests.length,count);
});
test('malformed read never becomes a passing result and can be retried',async t=>{
 const{page}=await open(t);await page.evaluate(()=>{const original=fetch;window.fetch=(...args)=>args[0]==='/api/settings/health'?Promise.resolve({ok:true,json:async()=>({checks:[]})}):original(...args);});await page.click('#settingsHealthRefresh');await page.waitForFunction(()=>document.getElementById('settingsHealthStatus').textContent.includes('unavailable'));assert.match(await page.$eval('#settingsHealthChecks',n=>n.textContent),/Stale/);
});
test('phone supporting evidence and resource entry remain readable with native sizing',async t=>{
 const{page}=await open(t,390);await page.click('#settingsHealthChecks summary');await page.$eval('#health-repair-connections',n=>n.scrollIntoView({block:'center'}));await page.screenshot({path:path.join(scratch,'health-phone-details.png'),fullPage:true});assert.equal(await page.$eval('#sysCloseBtn',n=>n.getBoundingClientRect().height>=44),true);
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
