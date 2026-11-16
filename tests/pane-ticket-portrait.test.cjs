const respondConversationAsset = require('./conversation-assets.cjs');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
assert.ok(app.includes(marker));
const exposed = app.replace(marker, `
  window.headerTest = {open:selectConversation,applyTheme,
    seed(ref = 'EXAMPLE-42', overrides = {}, opts = {}) {
      const row = {id:'session-one',session_id:'session-one',engine:'codex',source:'interactive',
        cwd:'/workspace/project',folder_label:'Example Project',is_watchtower_worker:true,
        _worker_id:'worker-one',display_name:'EXAMPLE#1: An old task',...overrides};
      _setUxFixesQueueMeta(ref ? [{ref,seq:Number(ref.split('-').pop()),project:'EXAMPLE',
        title:'A long task name '.repeat(15),status:'in_progress',claimed_by:'worker-one',
        claimed_session_id:'session-one',claimed_at:new Date().toISOString()}] : []);
      conversationsData=[row];
      updatePaneHeader(activePaneId(),row,opts);
      document.querySelector('.conv-pane').classList.add('has-last-user-message');
      _uxqOpenItemDetail = ref => {window.openedTicket=ref;};
    }
  };
` + marker);

test('reader ticket identity stays visible and repo portraits are sharp with an offline fallback', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:1440,height:900});
    await page.evaluateOnNewDocument(() => localStorage.setItem('ccc-conv-list-collapsed','0'));
    await page.setRequestInterception(true);
    page.on('request', r => {
      const url = new URL(r.url());
      if (r.isNavigationRequest()) return r.respond({status:200,contentType:'text/html',body:fs.readFileSync(require.resolve('../static/index.html'),'utf8')});
      if (respondConversationAsset(r)) return;
      if (url.pathname === '/static/app.js') return r.respond({status:200,contentType:'application/javascript',body:exposed});
      if (url.pathname === '/static/app.css') return r.respond({status:200,contentType:'text/css',body:fs.readFileSync(require.resolve('../static/app.css'),'utf8')});
      if (url.pathname.startsWith('/static/') && url.pathname.endsWith('.css')) {
        const file = require('node:path').join(__dirname,'..',url.pathname);
        if (fs.existsSync(file)) return r.respond({status:200,contentType:'text/css',body:fs.readFileSync(file,'utf8')});
      }
      if (url.pathname === '/api/conversations/session-one') return r.respond({status:200,contentType:'application/json',body:JSON.stringify({engine:'codex',last_line:2,events:[{type:'user_text',line:1,text:'Check the current task'},{type:'assistant',line:2,blocks:[{kind:'text',text:'This task is being investigated.'}]}]})});
      if (url.pathname === '/api/repo/avatar') return r.respond({status:200,contentType:'image/svg+xml',
        body:'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#075985"/><text x="32" y="43" text-anchor="middle" fill="white" font-size="32">EP</text></svg>'});
      return r.respond({status:200,contentType:'application/json',body:'[]'});
    });
    await page.goto('http://ccc-header.test/', {waitUntil:'load'});
    await page.waitForFunction(() => !!window.headerTest);
    await page.evaluate(async () => {headerTest.seed(); await headerTest.open('session-one');});
    await page.waitForFunction(() => document.querySelector('[data-role="pane-avatar"] img')?.naturalWidth > 0);
    const chip = '[data-role="pane-ticket"]';
    for (const theme of ['light','dark']) {
      await page.evaluate(theme => headerTest.applyTheme(theme), theme);
      const state = await page.evaluate(() => {
        const chip=document.querySelector('[data-role="pane-ticket"]'), rect=chip.getBoundingClientRect();
        const img=document.querySelector('[data-role="pane-avatar"] img'), style=getComputedStyle(img);
        const title=document.querySelector('[data-role="pane-title"]');
        return {label:chip.textContent,visible:chip.checkVisibility() && rect.left>=0 && rect.right<=innerWidth,
          hit:chip.contains(document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2)),
          filter:style.filter,opacity:style.opacity,fit:style.objectFit,
          titleAfter:rect.right <= title.getBoundingClientRect().left,
          engine:!!document.querySelector('[data-role="pane-avatar"] .conv-avatar-model.codex')};
      });
      if (process.env.CCC_HEADER_SCREENSHOTS) await page.screenshot({path:process.env.CCC_HEADER_SCREENSHOTS+'/header-'+theme+'.png'});
      assert.deepEqual(state,{label:'EXAMPLE-42',visible:true,hit:true,filter:'none',opacity:'1',fit:'contain',titleAfter:true,engine:true});
    }
    await page.focus(chip);
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => openedTicket), 'EXAMPLE-42');
    await page.evaluate(() => headerTest.seed('EXAMPLE-43',{name_overridden:true,display_name:'My worker'}));
    assert.equal(await page.$eval(chip,el=>el.textContent),'EXAMPLE-43','current claim must update without a new session');
    assert.equal(await page.$eval('[data-role="pane-title"]',el=>el.textContent),'My worker');
    await page.evaluate(() => {
      const img=document.querySelector('[data-role="pane-avatar"] img');
      img.dispatchEvent(new Event('error'));
    });
    assert.equal(await page.$eval('[data-role="pane-avatar"] img',el=>el.hidden),true);
    assert.equal(await page.$eval('[data-role="pane-avatar"] .repo-avatar-initials',el=>el.checkVisibility() && el.textContent==='EP'),true);
    if (process.env.CCC_HEADER_SCREENSHOTS) await page.screenshot({path:process.env.CCC_HEADER_SCREENSHOTS+'/header-offline.png'});
    await page.evaluate(() => headerTest.seed(null,{is_watchtower_worker:false,_worker_id:'',display_name:'A regular session'}));
    assert.equal(await page.$eval(chip,el=>el.hidden && !el.dataset.workerTicket),true,'regular sessions clear stale ticket identity');
    await page.evaluate(() => headerTest.seed('EXAMPLE-42',{}, {category:'Issue',title:'An issue'}));
    assert.equal(await page.$eval(chip,el=>el.hidden),true,'explicit non-session headers must not inherit a ticket');
    // Narrow popouts use the same header template and need a full touch target.
    await page.setViewport({width:390,height:850,isMobile:true,hasTouch:true});
    await page.evaluate(() => {document.body.classList.add('conversation-popout');headerTest.seed();});
    assert.equal(await page.$eval(chip,el=>{const r=el.getBoundingClientRect();return el.checkVisibility()&&r.width>=44&&r.height>=44&&r.left>=0&&r.right<=innerWidth;}),true);
    await page.click(chip);
    assert.equal(await page.evaluate(() => openedTicket),'EXAMPLE-42');
  } finally {await browser.close();}
});
