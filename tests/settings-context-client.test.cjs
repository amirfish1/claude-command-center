'use strict';
// Real dashboard fragments, styles and native preference/repository handlers.
// All requests terminate at a disposable loopback fixture; writes are in memory.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/app.css'));
const moduleJS = fs.readFileSync(path.join(root, 'static/settings-context.js'));
const navigationJS = fs.readFileSync(path.join(root, 'static/sidebar-navigation.js'));
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'settings-context-'));
function between(start, end) {
  const i=app.indexOf(start); assert(i>=0,start); const j=app.indexOf(end,i+start.length); assert(j>=0,end);return app.slice(i,j);
}
const preference = between('    settingsPreferenceClick = function handleSettingsPreferenceClick(e)', "    $settingsModal.addEventListener('click', settingsPreferenceClick);");
const phoneActions = between('  function _wireSimpleActionsMenu()', '  // CCC-154: desktop pane-header');
const fonts = between('  // Font scale controls for conversations view', '  // The Sessions/Issues tab bar was removed.');
const chips = between("    const $chipsToggle = document.getElementById('chipsColorToggle');", "    _applyStatusRailLayout();");
const refresh = between('  function refreshAppearanceChecks()', '  // Live-update when');
const getters = between('  function getThemePref()', '  function applyVoiceMobileBtn(');
const applyView = between('  function applyViewGh(', '  // Segmented-control');
const avatars = between('  let repoAvatarRefreshSeq = 0;', '  let settingsSearchFocusTimer;');
const resets = between('  function settingsResetAppearance()', '  // ── Preview feature flags');
const mirror = between('  function _syncSidebarMoreFilters()', "  const $sidebarMoreMenu = document.getElementById('sidebarMoreMenu');");
const more = between("  const $sidebarMoreMenu = document.getElementById('sidebarMoreMenu');", '  if ($cpCloseBtn)');
const queueMenu = between("  const $queueMoreBtn = document.getElementById('queueMoreBtn');", '  // Ticket-filing history');
const mounts = between('  window.cccSettingsContext?.mount({', '  // Trigger check on load');
const deepLink = between('  (function settingsDeepLink()', '  // ── First-run engines screen');
const nativeSidebarTab = between('  function _activateSidebarTabFromMobileNav(', '  function _syncMobileBottomNav(');
const sharedQueue = between('  function _sharedQueueHostElement(', '  // The Queue panel has two possible homes');
const nativeRepoAction = between('      const branding = repoPath && !archiveObjectId', '      const objectRenameIcon');
const nativeInspector = between('  function renderFlowNodeInspector(', '  function flowInspectorSetStatus(');
const encoded = JSON.stringify(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')).replace(/</g,'\\u003c');
let browser, server, url;
const requests = [];
function fixture() {return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><style>
body{display:block;margin:0;overflow:auto}.fixture-header{display:flex;gap:12px;padding:8px;position:relative;height:48px}.fixture-grid{display:flex;align-items:flex-start}.fixture-sidebar{width:350px;flex:0 0 350px}.fixture-main{flex:1;min-width:0;padding:8px}.conv-pane{display:block!important}.fixture-reader{min-height:120px}.fixture-dormant{display:none}.sh-more-menu{z-index:12000}.fixture-sidebar #convList{overflow:visible}.conv-pane-header{display:flex!important}.conv-pane-title{display:block}.conv-pane-more-menu{z-index:100}.fixture-main #convInputContext{display:block}.fixture-main .spawn-cwd-row{display:flex;flex-wrap:wrap}.fixture-main #spawnCwdSummary{display:none}#settingsModal{z-index:1000}.fixture-main .status-rail{display:none}
@media(max-width:1200px){.fixture-grid{display:block}.fixture-sidebar{width:100%}.fixture-main{padding:12px}#convList .conv-archived-tools{display:flex!important}}
</style></head><body><header class="fixture-header"></header><div class="fixture-grid"><aside class="fixture-sidebar"><div id="convList"></div></aside><main class="fixture-main"><div id="convSplit"></div><div id="queueParking"></div></main></div><div class="fixture-dormant"></div>
<script src="/sidebar-navigation.js"></script><script>
const parsed=new DOMParser().parseFromString(${encoded},'text/html');
document.body.append(parsed.getElementById('settingsModal'));
const header=document.querySelector('.fixture-header');header.append(parsed.getElementById('settingsBtn'),parsed.getElementById('sidebarMoreBtn').closest('.sh-more-wrap'));
const pane=parsed.querySelector('.conv-pane');pane.classList.add('is-active','has-pane-title');
// Keep native reader header and conversation nodes, without unrelated services.
const p=document.createElement('section');p.className=pane.className;p.dataset.paneId='p1';p.append(pane.querySelector('.conv-pane-header'),pane.querySelector('.conversations-view'));document.getElementById('convSplit').append(p);
p.querySelector('.conversations-view').classList.add('fixture-reader');p.querySelector('.conversations-view').textContent='Example conversation: reader text size changes here.';
for(const id of ['convInputContext','queuePanel'])document.querySelector('.fixture-main').append(parsed.getElementById(id));
header.append(parsed.getElementById('convSimpleActionsBtn'),parsed.getElementById('convSimpleActionsMenu'));
const toolbar=parsed.getElementById('convToolbar');document.querySelector('.fixture-dormant').append(toolbar,parsed.getElementById('chipsColorToggle'));
</script><script src="/settings-context.js"></script><script>
const get=id=>document.getElementById(id), isImeKey=e=>e.isComposing;
const $settingsModal=get('settingsModal'),$settingsPane=get('settingsPane'),$settingsRail=get('settingsRail'),$settingsModalInner=$settingsModal.querySelector('.settings-modal');
let _settingsCurrentSection='appearance';
function closeSettingsModal(){$settingsModal.hidden=true;$settingsModal.classList.remove('open');}
function openSettingsModal(){$settingsModal.hidden=false;$settingsModal.classList.add('open');}
get('settingsBtn').addEventListener('click',openSettingsModal);
function setActiveSettingsRailSection(id){if(window.cccSettingsContext.redirect(id))return;document.body.dataset.fixtureSettingsSection=id;}
for(const section of $settingsPane.querySelectorAll('[data-section-id]'))section.dataset.destination=section.dataset.sectionId;
for(const tab of $settingsRail.querySelectorAll('[data-section-target]'))tab.addEventListener('click',()=>setActiveSettingsRailSection(tab.dataset.sectionTarget));
const _phoneMenusMQ=matchMedia('(max-width:1200px)'),isMobileListView=()=>document.documentElement.classList.contains('ccc-mobile-list-view');
${mirror}
const $sidebarMoreBtn=get('sidebarMoreBtn');
${more}
const $appearancePopover=null,debugModeEnabled=()=>false,_cccSoundsEnabled=()=>true,_syncUiModeBodyClass=()=>{};
${getters}
${chips}
${refresh}
let conversationsData=[],_uxqHealthCache={ts:1};
function renderSidebar(){renderArchiveList();}function _renderQueuePanel(){document.body.dataset.queueRenders=String(1+Number(document.body.dataset.queueRenders||0));}
function showSettingsSavedPulse(row){row.dataset.saved='true';}
${applyView}
${preference}
$settingsModal.addEventListener('click',settingsPreferenceClick);
const $conversationsView=document.querySelector('.conversations-view'),$convPanelView=null,_ttsCacheDebug=false;
${fonts}
function applyTheme(value){document.documentElement.dataset.theme=value;}function applyFont(value){document.documentElement.dataset.font=value;}function applyVoiceMobileBtn(){}
${resets}
get('settingsResetAppearance').addEventListener('click',settingsResetAppearance);get('settingsResetLayout').addEventListener('click',settingsResetLayout);
const repoAvatarHtml=(repo)=>'<span class="repo-avatar repo-avatar-preview" data-repo-avatar="'+repo+'"><img src="/api/repo/avatar?repo_path='+encodeURIComponent(repo)+'" alt=""></span>';
async function loadRepoList(){return (await fetch('/api/repo/list')).json();}
const showOpToast=(message,kind)=>{document.body.dataset.toast=message;document.body.dataset.toastKind=kind;};
${avatars}
function _syncSessionMenuItems(){}
${phoneActions}
${queueMenu}
let _sharedQueuePanelHost='rail';
${sharedQueue}
${nativeSidebarTab}
const escapeHtml=text=>String(text).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');const escapeAttr=escapeHtml;
let tab='coding';
function renderArchiveList(){
 const panel=get('queuePanel');if(panel) get('queueParking').append(panel);
 tab=window.CCCNavigation.getTab()||'coding';
 const repoPath='repo-alpha',archiveObjectId='',newSession='<button type="button" data-role="folder-new-session">+</button>',ship='';
 ${nativeRepoAction}
 get('convList').innerHTML=tab==='queues'?'<div id="sidebarQueueHost"></div>':'<div class="conv-archived-tools"><details class="conv-archived-overflow"><summary class="conv-archived-overflow-trigger" aria-label="View options">View options</summary><span class="conv-archived-overflow-menu"><button type="button" data-native-view>Native view option</button></span></details></div><div class="conv-folder-group"><div class="conv-folder-group-header" role="button" tabindex="0"><span>Alpha repository</span>'+repoActions+'</div></div>';
 if(tab==='queues'){_sharedQueuePanelHost='sidebar';_mountSharedQueuePanel();}
 get('convList').querySelector('[data-native-view]')?.addEventListener('click',()=>{document.body.dataset.nativeView='clicked';});
 get('convList').querySelector('.conv-folder-group-header')?.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();document.body.dataset.collapsed='true';}});
}
function mobileShowMain(){}function mobileShowConv(){}function mobileShowForCurrentMode(){}function cccSwitchCoreApp(){}
get('sidebarNewBtn')?.remove();const newButton=document.createElement('button');newButton.id='sidebarNewBtn';newButton.textContent='New session';document.querySelector('.fixture-header').append(newButton);
newButton.addEventListener('click',()=>document.querySelector('.spawn-cwd-row').scrollIntoView());
let _view=$conversationsView;
function getConvView(){return _view;}function flowInspectorRenderMarkdown(){}function wireFlowNodeInspector(){}
${nativeInspector}
window.__inspector=()=>{_view=document.createElement('div');document.querySelector('.fixture-main').append(_view);renderFlowNodeInspector({ok:true,kind:'repo',repo_path:'repo-beta',title:'Beta repository',content:'Example'});};
window.__rerender=renderArchiveList;
window.__originals=Object.fromEntries(['repoAvatarSelect','repoAvatarReset','repoAvatarUpload','repoAvatarFile','fontPlus','settingsGhIssuesToggle','settingsSeparateTabsToggle','settingsQueueRhsListToggle'].map(id=>[id,get(id)]));
renderArchiveList();
${mounts}
const _bootUrlParams=new URLSearchParams(location.search);
${deepLink}
</script></body></html>`;}
test.before(async()=>{
 server=http.createServer(async(req,res)=>{
  const u=new URL(req.url,'http://fixture');
  if(u.pathname==='/app.css'){res.setHeader('Content-Type','text/css');return res.end(css);}
  if(u.pathname==='/sidebar-navigation.js'){res.setHeader('Content-Type','text/javascript; charset=utf-8');return res.end(navigationJS);}
  if(u.pathname==='/settings-context.js'){res.setHeader('Content-Type','text/javascript; charset=utf-8');return res.end(moduleJS);}
  if(u.pathname==='/api/repo/avatar'&&req.method==='GET'){res.setHeader('Content-Type','image/svg+xml');return res.end('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" rx="6" fill="#58a6ff"/></svg>');}
  if(u.pathname.startsWith('/api/')){
   let body='';for await(const chunk of req)body+=chunk;requests.push({method:req.method,url:u.pathname,body:body?JSON.parse(body):null});res.setHeader('Content-Type','application/json');
   if(u.pathname==='/api/repo/list')return res.end(JSON.stringify({repos:[{path:'repo-alpha',label:'Alpha'},{path:'repo-beta',label:'Beta'}],suggested:[]}));
   if(u.pathname==='/api/repo/avatar/info')return res.end(JSON.stringify({mode:'initials'}));
   return res.end(JSON.stringify({ok:true}));
  }
  res.setHeader('Content-Type','text/html; charset=utf-8');res.end(fixture());
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));url='http://127.0.0.1:'+server.address().port;
 browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
});
test.after(async()=>{await browser?.close();await new Promise(resolve=>server.close(resolve));assert(requests.every(req=>req.method==='GET'||req.url==='/api/repo/avatar'));console.log('Context preference screenshots:',scratch);});
async function open(t,width=1440,query=''){
 const context=await browser.createBrowserContext(),page=await context.newPage();page.setDefaultTimeout(6000);await page.setViewport({width,height:900});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));t.after(async()=>{await context.close();assert.deepEqual(errors,[]);});
 await page.setRequestInterception(true);page.on('request',request=>request.url().startsWith(url)?request.continue():request.abort());
 await page.goto(url+'/'+query,{waitUntil:'load'});assert.deepEqual(errors,[]);await page.waitForSelector('#contextListPreferences');return page;
}
async function listMenu(page){await page.evaluate(()=>window.cccSettingsContext.redirect('list-presentation'));await page.waitForFunction(()=>document.getElementById('settingsGhIssuesToggle').getClientRects().length>0);}
for(const width of [1440,390]){
 test(`one canonical editor per preference, edited in place while Settings is open at ${width}px`,async t=>{
  const page=await open(t,width);
  assert.equal(await page.$$eval('#settingsPane #repoAvatarSelect,#settingsPane #fontPlus,#settingsPane [data-chips-mode],#settingsPane #settingsGhIssuesToggle,#settingsPane #settingsSeparateTabsToggle,#settingsPane #settingsQueueRhsListToggle',els=>els.length),0);
  assert.equal(await page.evaluate(()=>Object.entries(window.__originals).every(([id,node])=>node===document.getElementById(id))),true);
  for(const id of ['repoAvatarSelect','fontPlus','settingsGhIssuesToggle','settingsQueueRhsListToggle'])assert.equal(await page.$$eval('#'+id,els=>els.length),1);
  for(const id of ['settingsUiModeToggle','settingsDebugModeToggle','settingsTtsCacheDebugToggle'])assert.equal(await page.$eval('#'+id,el=>!!el.closest('#settingsPane')),true);
  // Settings never sends people elsewhere: opening it brings the repository
  // and text-size controls in. List and queue controls stay in their menus.
  const inPane='#settingsPane #repoAvatarSelect,#settingsPane #fontPlus';
  assert.equal(await page.$$eval('#settingsPane [data-settings-context]',els=>els.length),0);
  await page.evaluate(()=>openSettingsModal());
  await page.waitForFunction(sel=>document.querySelectorAll(sel).length===2,{},inPane);
  assert.equal(await page.$$eval('#settingsPane #settingsGhIssuesToggle,#settingsPane #settingsQueueRhsListToggle',els=>els.length),0);
  assert.equal(await page.evaluate(()=>Object.entries(window.__originals).every(([id,node])=>node===document.getElementById(id))),true);
  await page.evaluate(()=>{document.getElementById('settingsModal').hidden=true;});
  await page.waitForFunction(sel=>document.querySelectorAll(sel).length===0,{},inPane);
  for(const id of ['repoAvatarSelect','fontPlus','settingsGhIssuesToggle','settingsQueueRhsListToggle'])assert.equal(await page.$$eval('#'+id,els=>els.length),1);
 });
 test(`native list adapters survive rerenders and menu mirroring at ${width}px`,async t=>{
  const page=await open(t,width);await listMenu(page);
  await page.focus('#settingsGhIssuesToggle');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>localStorage.getItem('ccc-view-gh')==='hide');
  await listMenu(page);assert.equal(await page.$eval('#settingsGhIssuesToggle',el=>el.getAttribute('aria-checked')),'false');
  await page.evaluate(()=>{localStorage.setItem('ccc-sidebar-tab','coding');window.CCCNavigation.setTab('coding');});
  await page.focus('#settingsSeparateTabsToggle');await page.keyboard.press('Space');
  assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-separate-tabs')),'on');
  assert.equal(await page.evaluate(()=>window.CCCNavigation.getTab()),'inprogress');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('ccc-sidebar-tab')),'inprogress');
  assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-sidebar-tab')),'coding');
  await listMenu(page);await page.screenshot({path:path.join(scratch,`list-before-${width}.png`)});await page.click('[data-chips-mode="muted"]');
  assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-chips-mode')),'muted');assert.equal(await page.evaluate(()=>document.body.classList.contains('chips-muted')),true);
  await page.evaluate(()=>{window.__rerender();_syncSidebarMoreFilters();});await listMenu(page);
  assert.equal(await page.$eval('#settingsGhIssuesToggle',el=>el===window.__originals.settingsGhIssuesToggle),true);
  assert.equal(await page.$$eval('#settingsGhIssuesToggle',els=>els.length),1);
  assert.equal(await page.$eval('#settingsGhIssuesToggle',el=>!!el.closest(innerWidth<=1200?'#sidebarMoreFilters':'.conv-archived-overflow-menu')),true);
  await page.screenshot({path:path.join(scratch,`list-${width}.png`)});
  await page.click('#contextListPreferences > button.settings-action-btn');
  assert.deepEqual(await page.evaluate(()=>['ccc-view-gh','ccc-separate-tabs','ccc-chips-mode'].map(key=>localStorage.getItem(key))),[null,null,null]);
 });
 test(`reader text size and reset operate in reader actions at ${width}px`,async t=>{
  const page=await open(t,width,'?ccc_settings=text-size');await page.waitForFunction(()=>document.getElementById('fontPlus')===document.activeElement);
  assert.equal(await page.$eval('#settingsModal',el=>el.hidden),true);
  assert.equal(await page.$eval('#fontPlus',el=>!!el.closest(innerWidth<=1200?'#convSimpleActionsMenu':'.conv-pane-more-menu')),true);
  if(width===390)assert.equal(await page.$$eval('#settingsModalFontSlot .font-size-btn',els=>els.every(el=>{const r=el.getBoundingClientRect();return r.width>=44&&r.height>=44;})),true);
  await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-conv-font-scale')),'1.1');
  assert.equal(await page.$eval('.conversations-view',el=>el.style.zoom),'1.1');
  await page.click('#settingsModalFontSlot [aria-label="Reset conversation text size"]');
  assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-conv-font-scale')),null);
  await page.focus('#fontMinus');await page.keyboard.press('Space');assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-conv-font-scale')),'0.9');
  assert.equal(await page.$eval('[data-role="pane-more-menu"]',el=>el.scrollWidth<=el.clientWidth+1),true);
  await page.screenshot({path:path.join(scratch,`reader-${width}.png`)});
  await page.keyboard.press('Escape');assert.equal(await page.$eval('#fontPlus',el=>!!el.getClientRects().length),false);
 });
 test(`context menus retain keyboard navigation and native mirror actions at ${width}px`,async t=>{
  const page=await open(t,width);await listMenu(page);
  await page.focus('[data-chips-mode="color"]');await page.keyboard.press('ArrowRight');
  assert.equal(await page.evaluate(()=>document.activeElement.dataset.chipsMode),'level');
  assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-chips-mode')),'level');
  await page.focus('#settingsGhIssuesToggle');await page.keyboard.press('ArrowDown');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'settingsSeparateTabsToggle');
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(()=>innerWidth<=1200?document.activeElement.id==='sidebarMoreBtn':document.activeElement.matches('.conv-archived-overflow summary')),true);
  await listMenu(page);
  await page.click(width<=1200?'#sidebarMoreFilters [data-native-view]':'#convList [data-native-view]');
  assert.equal(await page.evaluate(()=>document.body.dataset.nativeView),'clicked');
 });
 test(`repository management retains upload/reset handlers and chooses its repository at ${width}px`,async t=>{
  const page=await open(t,width);await page.focus('[data-context-repo="repo-alpha"]');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.getElementById('repoAvatarSelect').value==='repo-alpha');
  assert.equal(await page.evaluate(()=>document.body.dataset.collapsed),undefined);
  assert.equal(await page.$eval('#contextRepoPreferences',el=>el.previousElementSibling.classList.contains('conv-folder-group-header')),true);
  await page.select('#repoAvatarSelect','repo-beta');await page.waitForFunction(()=>document.getElementById('repoAvatarStatus').textContent.includes('Initials'));
  const before=requests.length;await page.click('#repoAvatarReset');await page.waitForFunction(()=>document.body.dataset.toastKind==='success');
  assert.deepEqual(requests.slice(before).find(req=>req.method==='POST').body,{repo_path:'repo-beta',image:null});
  const [chooser]=await Promise.all([page.waitForFileChooser(),page.click('#repoAvatarUpload')]);
  const imageFile=path.join(scratch,'fake-logo.svg');fs.writeFileSync(imageFile,'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>');
  await chooser.accept([imageFile]);await page.waitForFunction(()=>document.body.dataset.toast==='Repo avatar saved.');
  assert.match(requests.filter(req=>req.method==='POST').at(-1).body.image,/^data:image\/svg\+xml;base64,/);
  await page.evaluate(()=>window.__inspector());await page.focus('.flow-inspector [data-context-repo]');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('.flow-inspector #repoAvatarSelect')?.value==='repo-beta');
  assert.equal(await page.$$eval('#repoAvatarSelect',els=>els.length),1);
  await page.screenshot({path:path.join(scratch,`repo-${width}.png`)});
 });
 test(`queue menu uses the native on/off adapter and visible destination at ${width}px`,async t=>{
  const page=await open(t,width,'?ccc_settings=queue-view');await page.waitForFunction(()=>document.activeElement.id==='settingsQueueRhsListToggle');
  assert.equal(await page.$eval('#settingsQueueRhsListToggle',el=>!!el.closest('#queueMoreMenu.open')),true);
  await page.click('#settingsQueueRhsListToggle');assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-queue-rhs-list')),'on');
  await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>localStorage.getItem('ccc-queue-rhs-list')),'off');
  assert(Number(await page.evaluate(()=>document.body.dataset.queueRenders))>=2);
  assert.equal(await page.$eval('#queueMoreMenu',el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1}),true);
  await page.screenshot({path:path.join(scratch,`queue-${width}.png`)});
 });
}
test('resize, cloned reader chrome, hashes and old Settings routes retain access',async t=>{
 const page=await open(t);await listMenu(page);
 for(const width of [390,1440,390]){await page.setViewport({width,height:900});await listMenu(page);assert.equal(await page.$$eval('#settingsGhIssuesToggle',els=>els.length),1);}
 await page.evaluate(()=>{location.hash='repoAvatarSelect';});await page.waitForFunction(()=>document.getElementById('contextRepoPreferences').open);
 await page.evaluate(()=>{location.hash='fontPlus';});await page.waitForFunction(()=>document.activeElement.id==='fontPlus');await page.keyboard.press('Escape');
 await page.evaluate(()=>{const pane=document.querySelector('.conv-pane'),clone=pane.cloneNode(true);clone.querySelectorAll('[data-context-singleton]').forEach(el=>el.remove());clone.querySelectorAll('[id]').forEach(el=>el.removeAttribute('id'));clone.classList.remove('is-active');clone.dataset.paneId='p2';document.getElementById('convSplit').append(clone);});
 await page.setViewport({width:1440,height:900});
 await page.click('[data-pane-id="p2"] [data-role="pane-more"]');
 assert.equal(await page.$eval('#fontPlus',el=>el.closest('.conv-pane').dataset.paneId),'p2');assert.equal(await page.$$eval('#fontPlus',els=>els.length),1);
 await page.evaluate(()=>window.cccSettingsContext.redirect('appearance'));assert.equal(await page.evaluate(()=>window.cccSettingsContext.redirect('appearance')),false);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
});
test('global resets no longer modify contextual choices; reload preserves adapters',async t=>{
 const page=await open(t);await page.evaluate(()=>{localStorage.setItem('ccc-view-gh','hide');localStorage.setItem('ccc-conv-font-scale','1.3');settingsResetAppearance();settingsResetLayout();});
 assert.deepEqual(await page.evaluate(()=>['ccc-view-gh','ccc-conv-font-scale'].map(key=>localStorage.getItem(key))),['hide','1.3']);
 await page.reload({waitUntil:'load'});await listMenu(page);assert.equal(await page.$eval('#settingsGhIssuesToggle',el=>el.getAttribute('aria-checked')),'false');
 await page.evaluate(()=>window.cccSettingsContext.redirect('text-size'));assert.equal(await page.$eval('.conversations-view',el=>el.style.zoom),'1.3');
 assert.equal(await page.$$eval('[data-segmented-group="theme"]',els=>els.length),1);
});

test('contextual redirects survive outside-click handlers and escape collapsed folder picker',async t=>{
 const page=await open(t,390);
 await page.evaluate(()=>{
   document.addEventListener('click',event=>{if(event.target.closest('[data-settings-context]'))document.getElementById('sidebarMoreMenu').hidden=true;});
   openSettingsModal();
 });
 await page.evaluate(()=>window.cccSettingsContext.redirect('list-presentation'));
 assert.equal(await page.$eval('#settingsGhIssuesToggle',e=>!!e.getClientRects().length),true);
 await page.evaluate(()=>{
   const summary=document.getElementById('spawnCwdSummary');summary.innerHTML='<button class="spawn-cwd-change">Change</button>';
   document.body.classList.add('ccc-spawn-cwd-collapsed');
   summary.querySelector('button').addEventListener('click',()=>document.body.classList.remove('ccc-spawn-cwd-collapsed'));
   window.cccSettingsContext.redirect('repository-branding');
 });
 await page.waitForSelector('#repoAvatarSelect',{visible:true});
 assert.equal(await page.$eval('#contextRepoPreferences',e=>!!e.closest('.spawn-cwd-row')),false);
});

test('repository appearance stays out of the composer until requested and after closing',async t=>{
 const page=await open(t);
 assert.equal(await page.$eval('#contextRepoPreferences',e=>!!e.closest('#contextPreferenceParking')),true);
 assert.equal(await page.$eval('#contextRepoPreferences',e=>!!e.getClientRects().length),false);
 await page.evaluate(()=>window.cccSettingsContext.openBranding('repo-alpha'));
 await page.waitForSelector('#repoAvatarSelect',{visible:true});
 await page.click('#contextRepoPreferences > summary');
 await page.waitForFunction(()=>!!document.querySelector('#contextPreferenceParking #contextRepoPreferences'));
 assert.equal(await page.$eval('#contextRepoPreferences',e=>!!e.getClientRects().length),false);
 await page.evaluate(()=>window.cccSettingsContext.openBranding('repo-beta'));
 await page.waitForSelector('#repoAvatarSelect',{visible:true});
 assert.equal(await page.$eval('#repoAvatarSelect',e=>e.value),'repo-beta');
});

test('known repository remains editable while discovery is pending',async t=>{
 const page=await open(t);
 await page.evaluate(()=>{
   loadRepoList=()=>new Promise(()=>{});
   window.cccSettingsContext.openBranding('repo-known');
 });
 await page.waitForSelector('#repoAvatarSelect',{visible:true});
 assert.equal(await page.$eval('#repoAvatarSelect',e=>e.value),'repo-known');
 assert.equal(await page.evaluate(()=>document.activeElement.id),'repoAvatarSelect');
 await page.click('#repoAvatarReset');
 await page.waitForFunction(()=>document.body.dataset.toast==='Automatic repo avatar restored.');
});

test('repository editor spans the collapsed phone composer grid',async t=>{
 const page=await open(t,390);
 await page.evaluate(()=>{
   const host=document.querySelector('.spawn-cwd-row').parentElement;
   host.classList.add('visible','is-new-session');document.body.classList.add('ccc-spawn-cwd-collapsed');host.style.cssText='display:grid;width:340px;max-width:100%;';
   document.querySelector('.spawn-cwd-row').style.gridArea='cwd';
   window.cccSettingsContext.redirect('repository-branding');
 });
 await page.waitForSelector('#repoAvatarSelect',{visible:true});
 assert.equal(await page.$eval('#contextRepoPreferences',e=>e.clientWidth>=280&&e.scrollWidth<=e.clientWidth),true);
 assert.equal(await page.$eval('#repoAvatarSelect',e=>e.getBoundingClientRect().width>=250),true);
 for(const id of ['repoAvatarUpload','repoAvatarReset'])assert.equal(await page.$eval('#'+id,e=>e.getBoundingClientRect().right<=e.closest('#contextRepoPreferences').getBoundingClientRect().right),true);
});


test('phone Settings hosts the moved controls without horizontal overflow',async t=>{
 const page=await open(t,390);
 await page.evaluate(()=>openSettingsModal());
 await page.waitForFunction(()=>document.querySelectorAll('#settingsPane [data-context-home] > [data-context-preferences]').length>=2);
 const result=await page.evaluate(()=>{
   const pane=document.getElementById('settingsPane');
   pane.style.width='350px';pane.style.maxWidth='100%';
   return [...pane.querySelectorAll('[data-context-home]')].map(row=>{
     for(let node=row;node&&node!==pane;node=node.parentElement){node.hidden=false;if(node.tagName==='DETAILS')node.open=true;}
     const block=row.firstElementChild;
     return {fits:block.scrollWidth<=row.clientWidth+1};
   });
 });
 assert(result.length>=2);
 for(const item of result)assert(item.fits);
});
