// Run with node tests/mobile-home-collapse.browser.cjs. Uses the shipped CSS
// and scroll controller in a local fixture, without touching session state.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const controller = source.slice(source.indexOf('(function initMobileHomeCollapse()'),
  source.indexOf('// Preserve the desktop header', source.indexOf('(function initMobileHomeCollapse()')));
const css = fs.readFileSync(path.join(root, 'static/app.css'), 'utf8');
const html = `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/app.css"><style>
body {display:block;padding:0} .sidebar {width:100%;height:100dvh;max-width:none;overflow:hidden}
#convListPanel {display:flex;flex:1;min-height:0;flex-direction:column}
#convList {flex:1;min-height:0;overflow:auto} .test-row {padding:20px;border-bottom:1px solid var(--border)}
</style><div class="sidebar"><div class="sidebar-header">
<div class="sidebar-header-brand"><div class="sh-title-row"><a class="sh-title-link"><span class="sh-title-main">Claude Command Center</span></a></div></div>
<div class="mobile-home-status"><div id="localCiMobileList"><button class="local-ci-mobile-toggle" aria-expanded="false">CI: checks passing</button></div></div></div>
<div class="new-session-panel"><button class="new-session-btn" id="sidebarNewBtn">+ New session</button><button id="sidebarSearchBtn">Search sessions</button><input id="convSearch" aria-label="Search sessions" hidden></div>
<div id="convListPanel"><div id="convList"><div class="conv-archived-tools"><button>Filters</button></div>${Array.from({length:45},(_,i)=>`<div class="test-row">Session ${i+1}</div>`).join('')}</div></div></div>
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
      await scroll(180);
      assert.equal(await page.evaluate(()=>document.getAnimations().every(a=>
        a.effect.getKeyframes().every(frame=>!('height' in frame) && !('paddingTop' in frame)))),true,'motion stays off the layout path');
      await settled();
      assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),true);
      const trace = await page.evaluate(async () => {
        const list = document.querySelector('#convList'), panel = document.querySelector('#convListPanel');
        list.scrollTop = 0;
        const start = performance.now(), samples = [];
        const sample = () => samples.push({ms:performance.now()-start,y:panel.getBoundingClientRect().top});
        sample(); list.dispatchEvent(new Event('scroll')); sample();
        for (let i=0;i<25;i++) {await new Promise(requestAnimationFrame);sample();}
        return samples;
      });
      assert.ok(Math.abs(trace[1].y-trace[0].y)<2, 'top-of-list must not jump synchronously');
      assert.ok(trace.at(-1).y-trace[0].y>40,'header returns');
      assert.ok(new Set(trace.map(s=>Math.round(s.y))).size>=4,'header has intermediate positions');
      for (let i=1;i<trace.length;i++) assert.ok(trace[i].y>=trace[i-1].y-1,'reveal is monotonic');
      traces.push({width,trace});
      // Reversing mid-fold preserves the current position.
      await scroll(180); await delay(55);
      const reversal = await page.evaluate(()=>{
        const panel=document.querySelector('#convListPanel'), list=document.querySelector('#convList');
        const before=panel.getBoundingClientRect().top;
        list.scrollTop=0;list.dispatchEvent(new Event('scroll'));
        return Math.abs(panel.getBoundingClientRect().top-before);
      });
      assert.ok(reversal<2,'reverse starts at the interrupted height');
      await settled();
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
    await scroll(180);
    // Changing the preference mid-transition cleans up all temporary geometry.
    await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}]);
    await page.goto(url);await scroll(180);
    assert.equal(await page.$$eval('.mobile-home-folding',els=>els.length),0);
    await scroll(0);
    assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),false);
    await page.emulateMediaFeatures([]);
    await scroll(180);await settled();
    await page.setViewport({width:1440,height:900});
    await page.waitForFunction(()=>!document.body.classList.contains('mobile-home-collapsed'));
    assert.equal(await page.$eval('.new-session-panel',el=>el.inert),false);
    await scroll(180);
    assert.equal(await page.evaluate(()=>document.body.classList.contains('mobile-home-collapsed')),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({pass:true,traces},null,2));
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
