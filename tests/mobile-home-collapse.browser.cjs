// Run with node tests/mobile-home-collapse.browser.cjs. Uses the shipped CSS
// and scroll controller in a local fixture, without touching session state.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(process.env.CCC_COLLAPSE_SOURCE || path.join(root, 'static/app.js'), 'utf8');
const controller = source.slice(source.indexOf('(function initMobileHomeCollapse()'),
  source.indexOf('// CI, usage and savings', source.indexOf('(function initMobileHomeCollapse()')));
const css = fs.readFileSync(path.join(root, 'static/app.css'), 'utf8');
const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/app.css"><style>
body {display:block;padding:0} .sidebar {width:100%;height:100dvh;max-width:none;overflow:hidden}
#convList {flex:1;min-height:0;overflow:auto} .test-row {padding:20px;border-bottom:1px solid var(--border)}
</style><div class="sidebar"><div class="sidebar-header">
<div class="sidebar-header-brand"><div class="sh-title-row"><a class="sh-title-link"><span class="sh-title-main">Claude Command Center</span></a></div></div>
<div class="mobile-home-status"><div id="localCiMobileList"><button class="local-ci-mobile-toggle" aria-expanded="false">CI: checks passing</button></div></div></div>
<div class="new-session-panel"><button class="new-session-btn" id="sidebarNewBtn">+ New session</button><button id="sidebarSearchBtn">Search sessions</button><input id="convSearch" aria-label="Search sessions" hidden></div>
<div id="convListPanel" class="conv-list-panel"><div id="convList"><div class="conv-archived-tools"><button>Filters</button></div>${Array.from({length:45},(_,i)=>`<div class="test-row">Session ${i+1}</div>`).join('')}</div></div></div>
<script>document.querySelector('#sidebarNewBtn').onclick=()=>window.newClicks=(window.newClicks||0)+1;
document.querySelector('#sidebarSearchBtn').onclick=()=>document.querySelector('#convSearch').hidden=false;</script>
<script>${controller}</script>`;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const server = http.createServer((req,res) => {
    res.setHeader('Content-Type', req.url === '/app.css' ? 'text/css' : 'text/html');
    res.end(req.url === '/app.css' ? css : html);
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const url = `http://127.0.0.1:${server.address().port}`;
    const scroll = top => page.evaluate(top => {
      const list = document.querySelector('#convList');
      list.scrollTop = top;
      list.dispatchEvent(new Event('scroll'));
    }, top);
    const settled = () => page.waitForFunction(()=>!document.querySelector('.mobile-home-folding, .mobile-home-list-moving'));
    const traces = [];
    for (const width of [390,320,768]) {
      await page.setViewport({width,height:844});
      await page.goto(url,{waitUntil:'load'});
      const geometry = () => page.evaluate(() => {
        const rect = el => el.getBoundingClientRect().toJSON();
        return {panel:rect(document.querySelector('#convListPanel')),
          status:rect(document.querySelector('.mobile-home-status')),
          newPanel:rect(document.querySelector('.new-session-panel')),
          brand:rect(document.querySelector('.sidebar-header-brand')),
          opacity:getComputedStyle(document.querySelector('.mobile-home-status')).opacity};
      });
      const expanded = await geometry();
      const trace = [];
      // Small scrolls must already move the header, and stopping the gesture
      // must stop the header too. A timed threshold animation fails this.
      for (const top of [24,48,80,120,180]) {
        await scroll(top);
        await page.evaluate(() => new Promise(requestAnimationFrame));
        const frame = await geometry();
        assert.equal(await page.evaluate(()=>document.getAnimations().every(a=>
          a.effect.getKeyframes().every(frame=>!('height' in frame) && !('paddingTop' in frame)))),true,'motion stays off the layout path');
        assert.ok(frame.panel.top < (trace.at(-1)?.frame.panel.top ?? expanded.panel.top) - 1,
          'each scroll step progressively hides the header');
        assert.ok(frame.panel.bottom >= 843, 'fold fills the viewport');
        assert.ok(Math.abs(frame.brand.top - expanded.brand.top) < 1, 'brand stays pinned');
        if (top < 164) {
          assert.ok(frame.status.top < expanded.status.top, 'status slides upward instead of fading');
          assert.ok(frame.newPanel.top < expanded.newPanel.top, 'new-session controls slide upward');
          assert.equal(frame.opacity, '1', 'controls disappear by clipping at the top');
        }
        trace.push({top,frame});
        await delay(70);
        assert.ok(Math.abs((await geometry()).panel.top-frame.panel.top)<1, 'motion pauses with the finger');
        if (process.env.CCC_COLLAPSE_SCREENSHOTS && width === 390) {
          await page.screenshot({path:path.join(process.env.CCC_COLLAPSE_SCREENSHOTS, `scroll-${top}.png`)});
        }
      }
      await settled();
      assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),true);
      assert.equal(await page.$eval('.new-session-panel',el=>getComputedStyle(el).display),'none');
      // Reversing the gesture retraces the same positions without jumps.
      for (const top of [120,80,48,24,0]) {
        await scroll(top);
        await page.evaluate(() => new Promise(requestAnimationFrame));
        const frame = await geometry();
        const expected = top ? trace.find(sample=>sample.top === top).frame : expanded;
        assert.ok(Math.abs(frame.panel.top-expected.panel.top)<1, 'reverse retraces the scroll-linked fold');
        assert.ok(frame.panel.bottom>=843, 'reveal fills the viewport');
      }
      await settled();
      traces.push({width,trace});
      await scroll(180); await settled();
      assert.equal(await page.$eval('.new-session-panel',el=>el.inert),true);
      await page.click('.mobile-home-new');
      assert.equal(await page.evaluate(()=>window.newClicks),1);
      await page.click('.mobile-home-search');
      await settled();
      assert.equal(await page.evaluate(()=>document.activeElement.id),'convSearch');
      assert.equal(await page.$eval('.new-session-panel',el=>el.inert),false);
      await scroll(180); // Typing must not hide the focused search field.
      assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),false);
    }
    // The event is captured from nested session lists as well as #convList.
    await page.goto(url);
    await page.evaluate(()=>{
      const list=document.querySelector('#convList');
      const nested=document.createElement('div');
      nested.dataset.role='current-sessions-scroll';
      nested.style.cssText='height:300px;overflow:auto';
      while(list.firstChild) nested.append(list.firstChild);
      list.append(nested); nested.scrollTop=180;nested.dispatchEvent(new Event('scroll'));
    });
    await settled();
    assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),true);
    await page.evaluate(()=>{
      const nested=document.querySelector('[data-role="current-sessions-scroll"]');
      nested.scrollTop=0;nested.dispatchEvent(new Event('scroll'));
    });
    await settled();
    assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),false);
    await page.goto(url);
    await scroll(80);
    // Changing the preference mid-transition cleans up all temporary geometry.
    await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
    assert.equal(await page.$$eval('.mobile-home-folding, .mobile-home-list-moving',els=>els.length),0);
    await page.goto(url);await scroll(180);
    assert.equal(await page.$$eval('.mobile-home-folding',els=>els.length),0);
    await scroll(0);
    assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),false);
    await page.emulateMediaFeatures([]);
    await scroll(180);await settled();
    await scroll(80);
    await page.setViewport({width:1440,height:900});
    await page.waitForFunction(()=>!document.body.classList.contains('mobile-home-collapsed'));
    assert.equal(await page.$eval('.new-session-panel',el=>el.inert),false);
    await scroll(180);
    assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({pass:true,traces},null,2));
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
