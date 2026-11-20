const respondConversationAsset = require('./conversation-assets.cjs');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
assert.ok(app.includes(marker));
const exposed = app.replace('const _renderRow = (c, opts = {}) => {',
  'const _renderRow = (c, opts = {}) => { window.projectGroupRowsBuilt++;').replace(marker, `
  window.projectGroupTest = {seed(grouping='project', starred=true, tab='alltickets', withArchive=false, search='') {
    // API fixtures are empty; keep their background refresh from replacing
    // the explicit rows under test while exercising the real list renderer.
    renderArchiveList=()=>renderConversationList(conversationsData);
    window.__CCC_FLAGS__={idea_tickets:true};
    window.CCCNavigation.setTab(tab);
    localStorage.setItem('ccc-archived-grouping',grouping);
    localStorage.setItem('ccc-archive-window','all');
    document.getElementById('convSearch').value=search;
    const now=Date.now()/1000;
    const row=(id,n,extra={})=>({id,session_id:id,source:'interactive',engine:'claude',
      display_name:'Topic '+n,first_message:'Investigate topic '+n,modified:now-n,
      cwd:'/workspace/project',folder_path:'/workspace/project',folder_label_chip:'Example Project',
      is_live:true,idea_ticket:{ref:'IDEAS-'+n,queue:'IDEAS'},...extra});
    const rows=[row('starred-topic',1,{modified:now-500}),row('regular-topic',2),
      row('other-topic',3,{cwd:'/workspace/other',folder_path:'/workspace/other',folder_label_chip:'Other Project'}),
      row('child-lane',4,{parent_session_id:'regular-topic',idea_ticket:{ref:'IDEAS-2',queue:'IDEAS'}})];
    if(withArchive)Object.assign(rows[2],{archived:true,explicitly_archived:true});
    _todayStars=starred ? {'starred-topic':now} : {};
    conversationsData=rows; archiveData=rows; archiveLoaded=true; archiveDataWindow="all";
    window.projectGroupRowsBuilt=0;
    renderConversationList(rows);

  }};
` + marker);

test('Today stars keep a project together while chronological mode retains its Today group', async () => {
  const browser=await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page=await browser.newPage();
    await page.setViewport({width:1440,height:1000});
    await page.evaluateOnNewDocument(()=>{
      localStorage.setItem('ccc-conv-list-collapsed','0');
      localStorage.setItem('ccc-ui-mode','advanced');
      localStorage.setItem('ccc-theme','light');
    });
    await page.setRequestInterception(true);
    page.on('request',r=>{
      const url=new URL(r.url());
      if(r.isNavigationRequest())return r.respond({status:200,contentType:'text/html',body:fs.readFileSync(require.resolve('../static/index.html'),'utf8')});
      if (respondConversationAsset(r)) return;
      if(url.pathname==='/static/app.js')return r.respond({status:200,contentType:'application/javascript',body:exposed});
      if(url.pathname.startsWith('/static/')&&(url.pathname.endsWith('.css')||url.pathname==='/static/sidebar-navigation.js')) {
        const file=path.join(__dirname,'..',url.pathname);
        if(fs.existsSync(file))return r.respond({status:200,contentType:url.pathname.endsWith('.css')?'text/css':'application/javascript',body:fs.readFileSync(file,'utf8')});
      }
      return r.respond({status:200,contentType:'application/json',body:'[]'});
    });
    await page.goto('http://ccc-projects.test/',{waitUntil:'load'});
    await page.waitForFunction(()=>!!window.projectGroupTest);
    await page.waitForNetworkIdle({idleTime:200});
    await page.evaluate(()=>projectGroupTest.seed());
    assert.ok(await page.evaluate(()=>window.projectGroupRowsBuilt <= 6),
      'four visible rows and their family routing must not build a discarded duplicate tree');
    const active='[data-session-section="active"]';
    const lanes=active+' .conv-subagent-cluster-toggle[data-subagent-parent-sid="regular-topic"]';
    assert.equal(await page.$(active+' [data-session-id="child-lane"]'),null,
      'collapsed lanes keep their data without constructing hidden rows');
    if(process.env.CCC_PROJECT_GROUP_SCREENSHOT)await page.screenshot({path:process.env.CCC_PROJECT_GROUP_SCREENSHOT});
    await page.click(lanes);
    assert.equal(await page.$eval(lanes,el=>el.getAttribute('aria-expanded')),'true');
    assert.equal(await page.$eval(active+' [data-session-id="child-lane"]',el=>el.checkVisibility()),true);
    assert.equal(await page.$eval(lanes,el=>el===document.activeElement),true,
      'opening a deferred group retains keyboard focus');
    await page.keyboard.press('Enter');
    assert.equal(await page.$eval(lanes,el=>el.getAttribute('aria-expanded')),'false');
    await page.keyboard.press('Enter');
    assert.equal(await page.$eval(lanes,el=>el.getAttribute('aria-expanded')),'true');
    await page.evaluate(()=>projectGroupTest.seed());
    assert.equal(await page.$eval(active+' [data-session-id="child-lane"]',el=>el.checkVisibility()),true,
      'saved expansion survives ordinary sidebar rendering');
    const groups=await page.$$eval(active+' .conv-folder-group',els=>els.map(el=>({
      name:el.querySelector('.conv-folder-group-chip').textContent.trim(),
      count:el.querySelector('.conv-folder-group-count').textContent.trim(),
      roots:[...el.querySelectorAll('.conv-item[data-session-id]')].map(row=>row.dataset.sessionId),
    })));
    if(process.env.CCC_PROJECT_GROUP_SCREENSHOT)await page.screenshot({path:process.env.CCC_PROJECT_GROUP_SCREENSHOT});
    assert.equal(groups.length,2,JSON.stringify(groups));
    assert.equal(groups[0].name.endsWith('Example Project'),true);
    assert.equal(groups[0].count,'2','count both topics once, keeping the lane with its parent');
    assert.deepEqual(groups[0].roots,['starred-topic','regular-topic','child-lane']);
    assert.equal(groups[1].name.endsWith('Other Project'),true);
    assert.equal(await page.$(active+' .today-star-group'),null,'Today must not split project folders');
    const toggle=active+' .conv-folder-group-header';
    await page.hover(toggle);
    await page.waitForFunction(selector => {
      const button=document.querySelector(selector+' .conv-folder-new-session-btn');
      return button && getComputedStyle(button).opacity === '1';
    }, {}, toggle);
    assert.equal(await page.$eval(toggle, header =>
      [...header.querySelectorAll('.conv-folder-new-session-btn')].every(button => {
        const rect=button.getBoundingClientRect();
        const target=document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);
        return target === button || button.contains(target);
      })), true, 'decorative header hairlines must not intercept repository action clicks');
    await page.mouse.move(1200, 10);
    const sidebarStyle=await page.$eval('.sidebar', sidebar => sidebar.getAttribute('style'));
    const statusText=await page.$eval(toggle, header => {
      const status=header.querySelector('.conv-folder-ship-status');
      const previous=status.textContent;
      status.textContent='dirty';
      const sidebar=header.closest('.sidebar');
      Object.assign(sidebar.style, {width:'280px', minWidth:'280px', maxWidth:'280px'});
      sidebar.classList.add('is-narrow');
      return previous;
    });
    assert.equal(await page.$eval(toggle, header => {
      const count=header.querySelector('.conv-folder-group-count').getBoundingClientRect();
      const status=header.querySelector('.conv-folder-ship-status').getBoundingClientRect();
      return count.right <= status.left;
    }), true, 'visible ship status must not overlap the title or count in a narrow sidebar');
    await page.$eval('.sidebar', (sidebar, style) => {
      if (style === null) sidebar.removeAttribute('style');
      else sidebar.setAttribute('style', style);
      sidebar.classList.remove('is-narrow');
    }, sidebarStyle);
    await page.$eval(toggle, (header, text) => {
      header.querySelector('.conv-folder-ship-status').textContent=text;
    }, statusText);
    await page.click(toggle);
    assert.equal(await page.$eval(toggle,el=>el.getAttribute('aria-expanded')),'false');
    assert.equal(await page.$eval(active+' [data-session-id="regular-topic"]',el=>el.checkVisibility()),false);
    await page.focus(toggle);
    await page.keyboard.press('Enter');
    assert.equal(await page.$eval(toggle,el=>el.getAttribute('aria-expanded')),'true');
    await page.evaluate(()=>projectGroupTest.seed('time'));
    assert.equal(await page.$eval(active+' .today-star-group [data-session-id="starred-topic"]',el=>!!el),true);
    assert.equal(await page.$(active+' .today-star-group [data-session-id="regular-topic"]'),null);
    await page.evaluate(()=>projectGroupTest.seed('project',false));
    assert.equal(await page.$$eval(active+' .conv-folder-group',els=>els.length),2,'unstarred project grouping also stays intact');
    for (const tab of ['decisions', 'jobs', 'queues', 'issues']) {
      await page.evaluate(tab=>projectGroupTest.seed('project',true,tab),tab);
      assert.equal(await page.evaluate(()=>window.projectGroupRowsBuilt),0,
        tab+' must not construct invisible Active session rows');
      assert.equal(await page.$$eval('#convList .conv-item[data-session-id]',els=>els.length),0);
    }
    await page.evaluate(()=>projectGroupTest.seed());
    assert.deepEqual(await page.$$eval(active+' .conv-item[data-session-id]',els=>els.map(el=>el.dataset.sessionId)),
      ['starred-topic','regular-topic','child-lane','other-topic'],
      'returning to All restores the full visible hierarchy');
    await page.setViewport({width:390,height:844});
    await page.evaluate(()=>window.cccMobileNav.back());
    const phoneLanes=active+' .wa-lanes[data-subagent-parent-sid="regular-topic"]';
    await page.click(phoneLanes);
    await page.evaluate(()=>projectGroupTest.seed());
    assert.equal(await page.$(active+' [data-session-id="child-lane"]'),null);
    await page.click(phoneLanes);
    assert.equal(await page.$eval(phoneLanes,el=>el===document.activeElement),true,
      'phone expansion focuses the visible phone control');
    assert.equal(await page.$eval(active+' [data-session-id="child-lane"]',el=>el.checkVisibility()),true);
    await page.evaluate(()=>projectGroupTest.seed('time',false,'alltickets',true));
    const archived='[data-session-section="archived"]';
    const archiveToggle=archived+' [data-role="session-section-toggle"]';
    assert.equal(await page.$(archived+' [data-session-id="other-topic"]'),null,
      'a collapsed archive retains data without building hidden rows');
    assert.equal(await page.$eval(archived+' .session-lifecycle-count',el=>el.textContent),'1');
    await page.click(archiveToggle);
    assert.equal(await page.$eval(archived+' [data-session-id="other-topic"]',el=>el.checkVisibility()),true);
    assert.equal(await page.$eval(archiveToggle,el=>el===document.activeElement),true);
    await page.keyboard.press('Enter');
    await page.keyboard.press('Enter');
    assert.equal(await page.$eval(archived+' [data-session-id="other-topic"]',el=>el.checkVisibility()),true);
    await page.evaluate(()=>projectGroupTest.seed('time',false,'alltickets',true));
    assert.equal(await page.$eval(archived+' [data-session-id="other-topic"]',el=>el.checkVisibility()),true,
      'saved archive expansion survives ordinary refresh');
    await page.click(archiveToggle);
    await page.evaluate(()=>projectGroupTest.seed('time',false,'alltickets',true,'topic'));
    assert.equal(await page.$('[data-deferred-row-ids]'),null,'search does not defer matching sections');
  } finally {await browser.close();}
});
