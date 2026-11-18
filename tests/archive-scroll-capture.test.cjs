const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const respondConversationAsset = require('./conversation-assets.cjs');

const app = fs.readFileSync(process.env.CCC_SCROLL_APP || require.resolve('../static/app.js'), 'utf8');
const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
assert.ok(app.includes(marker));
const exposed = app.replace('function _captureArchiveListScroll(q, $list) {',
  'function _captureArchiveListScroll(q, $list) { window.scrollCaptures++;')
  .replace(marker, `
  const scrollTestRender = renderArchiveList;
  window.scrollTest = {
    seed() {
      renderArchiveList = () => {};
      window.CCCNavigation.setTab('coding');
      localStorage.setItem('ccc-archive-window', 'all');
      localStorage.setItem('ccc-archived-grouping', 'time');
      const now = Date.now() / 1000;
      archiveData = Array.from({length:80}, (_,i) => ({id:'row-'+i, session_id:'row-'+i,
        source:'interactive', engine:'claude', display_name:'Topic '+i,
        modified:now-i*60, folder_path:'/workspace/example', folder_label:'Example',
        is_live:true, state:'working'}));
      archiveLoaded = true; archiveDataWindow = 'all';
      _convListRenderSig = null;
      scrollTestRender('', {force:true, skipRestore:true});
    },
    render(change=false) {
      if (change) archiveData = archiveData.map((r,i) => i===0 ? {...r, display_name:r.display_name+' changed'} : r);
      scrollTestRender('', {force:true, skipRestore:true});
      return _convListRenderVersion;
    },
    empty() { archiveData=[]; scrollTestRender('', {force:true, skipRestore:true}); },
    capture() { return _captureArchiveListScroll('', document.getElementById('convList')); }
  };
` + marker);

test('archive refresh captures scroll only for rebuilt rows and retains the visible anchor', async () => {
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width:1440, height:1000});
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-conv-list-collapsed', '0');
      localStorage.setItem('ccc-tour-done', '1');
      window.scrollCaptures = 0;
    });
    await page.setRequestInterception(true);
    page.on('request', r => {
      const url = new URL(r.url());
      if (r.isNavigationRequest()) return r.respond({status:200, contentType:'text/html', body:fs.readFileSync(require.resolve('../static/index.html'),'utf8')});
      if (respondConversationAsset(r)) return;
      if (url.pathname === '/static/app.js') return r.respond({status:200, contentType:'application/javascript', body:exposed});
      if (url.pathname.startsWith('/static/') && (url.pathname.endsWith('.css') || url.pathname === '/static/sidebar-navigation.js')) {
        const file = path.join(__dirname, '..', url.pathname);
        if (fs.existsSync(file)) return r.respond({status:200, contentType:url.pathname.endsWith('.css')?'text/css':'application/javascript', body:fs.readFileSync(file,'utf8')});
      }
      return r.respond({status:200, contentType:'application/json', body:'[]'});
    });
    await page.goto('http://ccc-scroll.test/', {waitUntil:'load'});
    await page.waitForFunction(() => !!window.scrollTest);
    await page.waitForNetworkIdle({idleTime:200});
    await page.evaluate(() => scrollTest.seed());
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const unchanged = await page.evaluate(() => {
      scrollCaptures=0;
      const version=scrollTest.render();
      const next=scrollTest.render();
      return {version, next, captures:scrollCaptures};
    });
    assert.equal(unchanged.next, unchanged.version, 'unchanged refresh must retain the DOM');
    assert.equal(unchanged.captures, 0, 'unchanged refresh must not measure scroll geometry');
    const before = await page.evaluate(() => {
      const list=document.getElementById('convList');
      list.scrollTop=400;
      return scrollTest.capture();
    });
    assert.ok(before.top > 0 && before.anchorValue, 'fixture must have a scrolled visible anchor: '+JSON.stringify(await page.$eval('#convList',el=>({state:window.scrollTest.capture(),height:el.clientHeight,scroll:el.scrollHeight,text:el.innerText.slice(0,1000)}))));
    const rebuild = await page.evaluate(() => {
      scrollCaptures=0;
      const version=scrollTest.render(true);
      return {version, captures:scrollCaptures};
    });
    assert.equal(rebuild.captures, 1, 'actual replacement must capture exactly once');
    assert.ok(rebuild.version > unchanged.next);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const after = await page.evaluate(() => scrollTest.capture());
    assert.equal(after.anchorValue, before.anchorValue);
    assert.ok(Math.abs(after.anchorOffset-before.anchorOffset) < 2);
    await page.evaluate(() => { scrollCaptures=0; scrollTest.empty(); });
    assert.equal(await page.evaluate(() => scrollCaptures), 1, 'empty-state replacement retains the capture path');
  } finally { await browser.close(); }
});
