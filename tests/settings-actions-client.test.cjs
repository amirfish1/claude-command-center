'use strict';
// Real index.html fragments and CSS; selected native app.js handlers execute in
// a disposable browser. Only this fixture's loopback HTTP server is contacted.
// Daemon probes and tour rendering are fixture hooks; no real services run.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const ROOT = path.resolve(__dirname, '..');
const HTML = fs.readFileSync(path.join(ROOT, 'static/index.html'), 'utf8');
const APP = fs.readFileSync(path.join(ROOT, 'static/app.js'), 'utf8');
const MODULE = fs.readFileSync(path.join(ROOT, 'static/settings-actions.js'));
const CSS = fs.readFileSync(path.join(ROOT, 'static/app.css'));
function between(source, start, end) {
  const first = source.indexOf(start); assert(first >= 0, start);
  const last = source.indexOf(end, first + start.length); assert(last >= 0, end);
  return source.slice(first, last);
}
const ORGANIZE = between(APP, '  function organizeSettings()', '  function settingsModalFocusables()');
const SELECT = between(APP, '  function setActiveSettingsRailSection(', '  function ensureSettingsRowCrumb(');
const USAGE = between(APP, '  async function refreshSettingsUsage()', '  async function refreshSettingsQueues()');
const STATS_START = APP.indexOf("  (function() {\n    const $modal = document.getElementById('statsModal');");
assert(STATS_START >= 0);
const STATS = APP.slice(STATS_START, APP.indexOf('  })();', STATS_START) + '  })();'.length);
const MOBILE_MORE = between(APP, '  const _mobileMoreMenuIds =', '  const $cccTopbarMoreBtn =');
const MORE = between(APP, "  const $sidebarMoreMenu = document.getElementById('sidebarMoreMenu');", '  if ($cpCloseBtn)');
const SEARCH = between(APP, '  function openCmdk()', '  function closeCmdk()');
const SEARCH_BIND = between(APP, "  const $settingsCmdkBtn = document.getElementById('settingsCmdkBtn');", '  // Init');
const FLEET = between(APP, "  const $fleetBtn = document.getElementById('fleetViewBtn');", '  // If we just finished restarting');
const TOUR = between(APP, "  const $runOnboardingBtn = document.getElementById('runOnboardingBtn');", '  // ── Car Mode');
const TOUR_ALIAS = between(APP, "  const $takeTourBtn = document.getElementById('takeTourBtn');", '  if (!CONV_POPOUT_MODE)');
const TERMINAL_AT = HTML.indexOf("  const $panel = document.getElementById('termPanel');");
const TERMINAL = HTML.slice(HTML.lastIndexOf('<script>', TERMINAL_AT) + 8, HTML.indexOf('</script>', TERMINAL_AT));
const SYS_OPEN = between(APP, '  function sysOpenModal(', '  function sysCloseModal(');
const SYS_CLOSE = between(APP, '  function sysCloseModal(', '  // Never trap the user');
const MAINTENANCE = between(APP, '  function openMaintenanceSettings()', "  // endpoint '/api/restart'");
const DEEP_LINK = between(APP, '  (function settingsDeepLink()', '  // ── First-run engines screen');
const INERT = JSON.stringify(HTML.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')).replace(/</g, '\\u003c');
const screenshots = fs.mkdtempSync(path.join(os.tmpdir(), 'settings-actions-'));
let browser, server, url;
const requests = [];

function fixture() {
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><style>
body{margin:0}.fixture-header{display:flex;gap:12px;padding:8px;height:48px;flex:0 0 48px;align-items:center;box-sizing:border-box;position:relative;z-index:100}.fixture-launchers{display:none}.fixture-header #settingsBtn{display:block!important}.sh-more-menu{z-index:11000}#settingsModal{z-index:1000}#statsModal,#sysModal{z-index:2000}.settings-actions-tools #annotationNotesBtn{display:flex!important}
</style></head><body><header class="fixture-header"></header><div class="fixture-launchers"></div>
<script>
const parsed=new DOMParser().parseFromString(${INERT},'text/html');
for(const style of parsed.querySelectorAll('style'))document.head.append(style.cloneNode(true));
for(const id of ['settingsModal','sysModal','statsModal','cmdkModal','worktreesModal','termPanel','todayTray','drainPausedBanner'])document.body.append(parsed.getElementById(id));
const header=document.querySelector('.fixture-header');header.append(parsed.getElementById('settingsBtn'),parsed.getElementById('sidebarMoreBtn').closest('.sh-more-wrap'));
for(const id of ['termToggleBtn','historyStatusPill','kptWorktreesBtn','statsBtn','todayToggleBtn','annotationNotesBtn'])document.querySelector('.fixture-launchers').append(parsed.getElementById(id));
document.getElementById('historyStatusPill').style.display='';
</script><script src="/settings-actions.js"></script><script>
const get=id=>document.getElementById(id), isImeKey=e=>e.isComposing;
const $settingsRail=get('settingsRail'),$settingsPane=get('settingsPane'),$settingsModal=get('settingsModal'),$settingsModalInner=$settingsModal.querySelector('.settings-modal');
let _settingsSearchIndex=null,_settingsCurrentSection='appearance';
function closeSettingsModal(){$settingsModal.hidden=true;$settingsModal.classList.remove('open');}
function openSettingsModal(){$settingsModal.hidden=false;$settingsModal.classList.add('open');setActiveSettingsRailSection('appearance');}
function refreshSettingsQueues(){}function startEnginesHub(){}function _syncSidebarMoreFilters(){}
const escapeHtml=text=>String(text).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
async function settingsJSON(target){return (await fetch(target)).json();}
${USAGE}
${ORGANIZE}
${SELECT}
get('settingsBtn').addEventListener('click',openSettingsModal);
get('settingsModalClose').addEventListener('click',closeSettingsModal);
$settingsRail.addEventListener('click',event=>{const button=event.target.closest('.settings-rail-item');if(button)setActiveSettingsRailSection(button.dataset.sectionTarget);});
const $sidebarMoreBtn=get('sidebarMoreBtn');
${MORE}
// Native modal opening/closing functions, with daemon reads replaced by inert
// fixture probes. No service implementation is imported or contacted.
const $sysModal=get('sysModal'),$sysDialog=get('sysDialog'),$sysChip=get('systemStatusBtn');
let _sysReturnFocus=null,_sysLastRollupLevel='',_sysPollTimer=null,_sysSpawnedTickTimer=null,_sysHealthTimer=null,_sysProcessesTimer=null,_sysJobsTimer=null,_sysSpawnedList=[];
function sysSelectTab(){}function sysRefresh(){}function renderSysSpawnedList(){}function _sysTrapTab(){}
${SYS_OPEN}
${SYS_CLOSE}
${MAINTENANCE}
get('drainPausedDetailsBtn').addEventListener('click',openMaintenanceSettings);
get('systemStatusBtn').addEventListener('click',()=>sysOpenModal(get('sidebarMoreBtn')));
get('sysCloseBtn').addEventListener('click',sysCloseModal);
get('sysDialog').addEventListener('keydown',event=>{if(event.key==='Escape')sysCloseModal()});
const $cmdkModal=get('cmdkModal'),$cmdkInput=get('cmdkInput');
function renderCmdkList(){get('cmdkList').textContent='Example session';}
${SEARCH}
${SEARCH_BIND}
${STATS}
${FLEET}
function loadFirstFlightTour(){document.body.dataset.fixtureTour='open';}
${TOUR}
${TOUR_ALIAS}
window.__opens=[];window.open=(destination,target)=>{window.__opens.push({destination,target});return null;};
window.__legacyDestructive=0;
for(const id of ['restartServerBtn','controlPlaneRestartBtn','watchtowerServiceActionBtn']){get(id).disabled=false;get(id).addEventListener('click',()=>window.__legacyDestructive++);}
for(const id of ['sysRestartDashBtn','sysRestartWorkerBtn','sysRestartWtBtn'])get(id).disabled=false;
get('controlPlaneDrainBtn').disabled=false;get('controlPlaneDrainBtn').addEventListener('click',event=>event.currentTarget.textContent='Resume dispatch');
get('controlPlaneReconcileBtn').hidden=false;get('controlPlaneReconcileBtn').addEventListener('click',event=>event.currentTarget.textContent='Reconciled in fixture');
get('watchtowerServiceOpenBtn').disabled=false;get('watchtowerServiceOpenBtn').addEventListener('click',()=>window.open('/queue-fixture','_blank'));
get('kptWorktreesBtn').addEventListener('click',()=>get('worktreesModal').classList.add('open'));
get('todayToggleBtn').addEventListener('click',()=>{get('todayTray').hidden=!get('todayTray').hidden;});
get('annotationNotesBtn').addEventListener('click',()=>{document.body.dataset.fixtureNotes='open';});
get('momentZeroBtn').addEventListener('click',()=>{document.body.dataset.fixtureWelcome='open';});
get('historyStatusPill').addEventListener('click',()=>{document.body.dataset.fixtureHistory='open';});
window._cccOpenFleetPulse=()=>{document.body.dataset.fixturePulse='open';};
window.__originals=Object.fromEntries(['termToggleBtn','settingsCmdkBtn','runOnboardingBtn','statsBtn','controlPlaneDrainBtn','watchtowerServiceOpenBtn'].map(id=>[id,get(id)]));
window.__dialogCount=document.querySelectorAll('[role=dialog]').length;
// Match app.js's permanent boot-time tool move, before the new relocation.
for(const id of ['termToggleBtn','historyStatusPill','kptWorktreesBtn','statsBtn','todayToggleBtn','annotationNotesBtn'])get('settingsModalToolsSlot').append(get(id));
window.cccSettingsActions.mount({closeSettings:closeSettingsModal,openSystem:opener=>sysOpenModal(opener,'status'),refreshUsage:refreshSettingsUsage});
${MOBILE_MORE}
let fixtureMobile=false;
function isMobileChromeActive(){return fixtureMobile;}
window.__syncResponsiveActions=mobile=>{fixtureMobile=mobile;_syncMobileMoreMenu();};
${TERMINAL}
const _bootUrlParams=new URLSearchParams(location.search);
${DEEP_LINK}
</script></body></html>`;
}

test.before(async () => {
  server=http.createServer((req,res)=>{
    const parsed=new URL(req.url,'http://fixture');
    if(parsed.pathname==='/app.css'){res.setHeader('Content-Type','text/css');return res.end(CSS);}
    if(parsed.pathname==='/settings-actions.js'){res.setHeader('Content-Type','text/javascript; charset=utf-8');return res.end(MODULE);}
    if(parsed.pathname.startsWith('/api/')){
      requests.push({method:req.method,url:parsed.pathname});res.setHeader('Content-Type','application/json');
      if(parsed.pathname==='/api/settings/model-usage')return res.end(JSON.stringify({total:3,models:[{model:'Example model',engine:'example',count:3}]}));
      if(parsed.pathname==='/api/stats')return res.end(JSON.stringify({sessions:7,messages:30,models:[{label:'Example model',messages:30}]}));
      return res.end(JSON.stringify({ok:true,cwd:'example'}));
    }
    res.setHeader('Content-Type','text/html');res.end(fixture());
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));url='http://127.0.0.1:'+server.address().port;
  browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();await new Promise(resolve=>server.close(resolve));assert(requests.every(request=>request.method==='GET'||request.url==='/api/onboarding/reset'));console.log('Settings action screenshots:',screenshots);});
async function open(t,width=1440,query=''){
  const context=await browser.createBrowserContext();const page=await context.newPage();page.setDefaultTimeout(7000);await page.setViewport({width,height:width<700?844:1000});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  t.after(async()=>{await context.close();assert.deepEqual(errors,[]);});
  await page.setRequestInterception(true);page.on('request',request=>{if(request.url().startsWith(url))request.continue();else request.abort();});
  await page.goto(url+'/'+query,{waitUntil:'load'});await page.waitForSelector('#settingsActionsTools');return page;
}
async function menu(page){if(await page.$eval('#sidebarMoreMenu',element=>element.hidden))await page.click('#sidebarMoreBtn');}

test('Settings retains preference controls and retires tools/help/actions without cloning nodes',async t=>{
  const page=await open(t);
  assert.equal(await page.$$eval('#settingsPane #settingsSection-tools,#settingsPane #settingsSection-help,#settingsPane #settingsModelUsage',nodes=>nodes.length),0);
  assert.equal(await page.$$eval('#settingsPane #restartServerBtn,#settingsPane #controlPlaneDrainBtn,#settingsPane #systemStatusBtn,#settingsPane #fleetViewBtn,#settingsPane #runOnboardingBtn',nodes=>nodes.length),0);
  for(const id of ['configConsentBtn','settingsResetAppearance','spawnDefaultsEngine','settingsMonthlyClaudePlanInput'])assert.equal(await page.$eval('#'+id,element=>!!element.closest('#settingsPane')),true);
  assert.equal(await page.$eval('[data-settings-destination="maintenance"]',element=>element.textContent),'System');
  assert.equal(await page.evaluate(()=>Object.entries(window.__originals).every(([id,element])=>element===document.getElementById(id))),true);
  assert.equal(await page.evaluate(()=>window.__dialogCount===document.querySelectorAll('[role=dialog]').length),true);
  assert.equal(await page.$$eval('#takeTourBtn',nodes=>nodes.length),1);
  assert.equal(await page.$eval('#takeTourBtn',element=>!!element.closest('[hidden]')),true);
});

for(const width of [1440,390]){
  test(`tools are usable from More by mouse and keyboard at ${width}px`,async t=>{
    const page=await open(t,width);await menu(page);
    assert.equal(await page.$eval('#termToggleBtn',element=>!!element.offsetParent),true);
    await page.click('#termToggleBtn');await page.waitForSelector('#termPanel.open');
    assert.equal(await page.$eval('#sidebarMoreMenu',element=>element.hidden),true);
    await page.click('#termCloseBtn');await menu(page);await page.focus('#settingsCmdkBtn');await page.keyboard.press('Enter');
    await page.waitForSelector('#cmdkModal.open');await page.waitForFunction(()=>document.activeElement.id==='cmdkInput');
    await page.evaluate(()=>document.getElementById('cmdkModal').classList.remove('open'));
    await menu(page);await page.click('#settingsActionsWorkspace > summary');await page.click('#kptWorktreesBtn');await page.waitForSelector('#worktreesModal.open');
    await page.evaluate(()=>document.getElementById('worktreesModal').classList.remove('open'));
    await menu(page);await page.click('#todayToggleBtn');assert.equal(await page.$eval('#todayTray',element=>element.hidden),false);
    await menu(page);await page.click('#fleetViewBtn');assert.deepEqual(await page.evaluate(()=>window.__opens.at(-1)),{destination:'/fleet.html',target:'_blank'});
    await menu(page);await page.click('#settingsFleetPulseBtn');assert.equal(await page.evaluate(()=>document.body.dataset.fixturePulse),'open');
    await menu(page);await page.click('#historyStatusPill');assert.equal(await page.evaluate(()=>document.body.dataset.fixtureHistory),'open');
    await menu(page);await page.click('#annotationNotesBtn');assert.equal(await page.evaluate(()=>document.body.dataset.fixtureNotes),'open');
    await menu(page);await page.keyboard.press('ArrowDown');assert.equal(await page.evaluate(()=>document.activeElement.id),'termToggleBtn');
    await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'sidebarMoreBtn');
    assert.equal(await page.$eval('#sidebarMoreMenu',element=>element.hidden),true);
  });
  test(`Help routes retain documentation and onboarding at ${width}px`,async t=>{
    const page=await open(t,width,'?ccc_settings=help');
    assert.equal(await page.$eval('#settingsModal',element=>element.hidden),true);
    assert.equal(await page.$eval('#settingsActionsHelp',element=>element.open),true);
    const href=await page.$eval('#settingsActionsHelp a',element=>element.href);
    assert.equal(href,'https://github.com/amirfish1/claude-command-center#readme');
    assert.equal(await page.$eval('#settingsActionsHelp a',element=>element.rel),'noopener');
    await page.focus('#runOnboardingBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.body.dataset.fixtureTour==='open');
    await menu(page);await page.click('#momentZeroBtn');assert.equal(await page.evaluate(()=>document.body.dataset.fixtureWelcome),'open');
    await page.evaluate(()=>{delete document.body.dataset.fixtureTour;document.getElementById('takeTourBtn').click();});
    assert.equal(await page.evaluate(()=>document.body.dataset.fixtureTour),'open');
  });
  test(`native Stats contains the report and survives native tab/range rerenders at ${width}px`,async t=>{
    const page=await open(t,width);await menu(page);await page.click('#statsBtn');await page.waitForSelector('#statsModal.open .stats-cards');
    await page.click('#settingsActionsUsage > summary');await page.waitForFunction(()=>document.getElementById('settingsModelUsage').textContent.includes('Example model'));
    assert.match(await page.$eval('#settingsModelUsage',element=>element.textContent),/3.*recorded sessions/);
    assert.equal(await page.$eval('.settings-usage-list',element=>element.scrollWidth<=element.clientWidth),true);
    assert.equal(await page.$eval('.settings-usage-row b',element=>element.getBoundingClientRect().right<=element.closest('.settings-usage-list').getBoundingClientRect().right),true);
    await page.click('#statsTabModels');await page.waitForSelector('#statsBody .stats-models-row');
    assert.equal(await page.$eval('#settingsModelUsage',element=>!!element.closest('#statsDialog')),true);
    await page.click('[data-stats-range="7d"]');await page.waitForSelector('#statsBody .stats-models-row');
    await page.focus('#statsCloseBtn');await page.keyboard.press('Enter');await page.waitForFunction(()=>document.activeElement.id==='sidebarMoreBtn');
  });
}

for (const width of [1440,390]) test(`service controls move intact and legacy aliases navigate to canonical controls at ${width}px`,async t=>{
  const page=await open(t,width);await menu(page);await page.click('#systemStatusBtn');await page.waitForSelector('#sysModal.open');
  assert.equal(await page.$eval('#controlPlaneDrainBtn',element=>!!element.closest('#sysRowWorker')),true);
  await page.focus('#controlPlaneDrainBtn');await page.keyboard.press('Enter');assert.equal(await page.$eval('#controlPlaneDrainBtn',element=>element.textContent),'Resume dispatch');
  await page.focus('#watchtowerServiceOpenBtn');await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>window.__opens.at(-1).destination),'/queue-fixture');
  await page.screenshot({path:path.join(screenshots,`system-${width}.png`)});
  assert.deepEqual(await page.$$eval('#sysDialog, #sysDialog *',elements=>elements.filter(element=>element.scrollWidth>element.clientWidth+1 && getComputedStyle(element).display!=='inline').map(element=>({id:element.id,class:element.className,width:element.clientWidth,scroll:element.scrollWidth}))),[]);
  await page.click('#sysCloseBtn');await page.waitForFunction(()=>document.activeElement.id==='sidebarMoreBtn');
  for(const [legacy,canonical] of [['restartServerBtn','sysRestartDashBtn'],['controlPlaneRestartBtn','sysRestartWorkerBtn'],['watchtowerServiceActionBtn','sysRestartWtBtn']]){
    await page.evaluate(id=>document.getElementById(id).click(),legacy);assert.equal(await page.$eval('#sysModal',element=>element.classList.contains('open')),true);
    assert.equal(await page.evaluate(()=>document.activeElement.id),canonical);
    await page.click('#sysCloseBtn');
  }
  assert.equal(await page.evaluate(()=>window.__legacyDestructive),0);
  await page.evaluate(()=>document.getElementById('drainPausedDetailsBtn').click());
  assert.equal(await page.$eval('#sysModal',element=>element.classList.contains('open')),true);
  assert.equal(await page.$eval('#settingsModal',element=>element.hidden),true);
});

test('retired section deep links and adapters redirect to existing destinations',async t=>{
  const page=await open(t,1440,'?ccc_settings=tools');assert.equal(await page.$eval('#sidebarMoreMenu',element=>element.hidden),false);
  await page.evaluate(()=>document.getElementById('settingsRailTab-help').click());assert.equal(await page.$eval('#settingsActionsHelp',element=>element.open),true);
  await page.evaluate(()=>window.cccSettingsActions.redirect('model-usage'));await page.waitForSelector('#statsModal.open');
  assert.equal(await page.$eval('#settingsActionsUsage',element=>element.open),true);
  await page.keyboard.press('Escape');
  await page.evaluate(()=>{location.hash='settingsSection-help';});await page.waitForFunction(()=>!document.getElementById('sidebarMoreMenu').hidden);
  assert.equal(await page.$eval('#settingsActionsHelp',element=>element.open),true);
  assert.equal(await page.evaluate(()=>window.cccSettingsActions.redirect('appearance')),false);
});

test('More and destinations fit desktop/mobile and reveal focused items without horizontal overflow',async t=>{
  const page=await open(t);await menu(page);await page.screenshot({path:path.join(screenshots,'desktop-more.png')});
  await page.setViewport({width:390,height:844});await menu(page);await page.screenshot({path:path.join(screenshots,'phone-more.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await page.$eval('#sidebarMoreMenu',element=>element.getBoundingClientRect().right<=innerWidth+1),true);
  await page.evaluate(()=>window.cccSettingsActions.redirect('help'));await page.focus('#momentZeroBtn');await page.screenshot({path:path.join(screenshots,'phone-help.png')});
  assert.equal(await page.$eval('#momentZeroBtn',element=>{const box=element.getBoundingClientRect();return box.top>=0&&box.bottom<=innerHeight;}),true);
  await page.focus('#settingsActionsHelp a');
  assert.equal(await page.$eval('#settingsActionsHelp a',element=>{const box=element.getBoundingClientRect();return box.top>=0&&box.bottom<=innerHeight;}),true);
  await page.evaluate(()=>window.cccSettingsActions.redirect('model-usage'));await page.waitForSelector('#statsModal.open');await page.screenshot({path:path.join(screenshots,'phone-usage.png')});
  assert.equal(await page.$eval('#statsDialog',element=>element.scrollWidth<=element.clientWidth+1),true);
});

test('responsive topbar updates preserve permanent workspace action homes',async t=>{
  const page=await open(t);await menu(page);await page.click('#settingsActionsWorkspace > summary');
  for(const mobile of [false,true,false]){
    await page.evaluate(value=>window.__syncResponsiveActions(value),mobile);
    for(const id of ['statsBtn','termToggleBtn','kptWorktreesBtn','annotationNotesBtn']){
      assert.equal(await page.$eval('#'+id,node=>!!node.closest('#settingsActionsTools')),true);
      assert.equal(await page.$eval('#'+id,node=>node.checkVisibility()),true);
    }
  }
});

 test('collapsed workspace is skipped by keyboard until expanded', async t => {
  const page=await open(t); await menu(page);
  await page.focus('#statsBtn'); await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(()=>document.activeElement.parentElement.id),'settingsActionsWorkspace');
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(()=>!!document.activeElement.closest('#settingsActionsWorkspace')),false);
  await page.focus('#settingsActionsWorkspace > summary'); await page.keyboard.press('Enter');
  await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'historyStatusPill');
});
