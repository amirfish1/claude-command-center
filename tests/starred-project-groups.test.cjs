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
const exposed = app.replace(marker, `
  window.projectGroupTest = {seed(grouping='project', starred=true) {
    // API fixtures are empty; keep their background refresh from replacing
    // the explicit rows under test while exercising the real list renderer.
    renderArchiveList=()=>{};
    window.__CCC_FLAGS__={idea_tickets:true};
    window.CCCNavigation.setTab('alltickets');
    localStorage.setItem('ccc-archived-grouping',grouping);
    localStorage.setItem('ccc-archive-window','all');
    const now=Date.now()/1000;
    const row=(id,n,extra={})=>({id,session_id:id,source:'interactive',engine:'claude',
      display_name:'Topic '+n,first_message:'Investigate topic '+n,modified:now-n,
      cwd:'/workspace/project',folder_path:'/workspace/project',folder_label_chip:'Example Project',
      is_live:true,idea_ticket:{ref:'IDEAS-'+n,queue:'IDEAS'},...extra});
    const rows=[row('starred-topic',1,{modified:now-500}),row('regular-topic',2),
      row('other-topic',3,{cwd:'/workspace/other',folder_path:'/workspace/other',folder_label_chip:'Other Project'}),
      row('child-lane',4,{parent_session_id:'regular-topic',idea_ticket:{ref:'IDEAS-2',queue:'IDEAS'}})];
    _todayStars=starred ? {'starred-topic':now} : {};
    conversationsData=rows; archiveData=rows; archiveLoaded=true; archiveDataWindow="all";
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
    const active='[data-session-section="active"]';
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
  } finally {await browser.close();}
});
