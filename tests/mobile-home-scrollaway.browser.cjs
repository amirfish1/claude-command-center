// Run with node tests/mobile-home-scrollaway.browser.cjs. Uses the shipped CSS
// and controller in a local fixture, without touching session state.
//
// On phones the header bands above the main session list (#convList) scroll
// away with it. The list's own box must never move or resize while it
// scrolls: iOS ends a momentum scroll when the scroller or its ancestors
// change layout or layers mid-flick, which made scrolling stop abruptly.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const start = source.indexOf('(function initMobileHomeCollapse()');
const controller = source.slice(start, source.indexOf('// CI, usage and savings', start));
const css = fs.readFileSync(path.join(root, 'static/app.css'), 'utf8');
const rows = Array.from({length:45}, (_, i) => `<div class="test-row">Session ${i + 1}</div>`).join('');
const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/app.css"><style>
body {display:block;padding:0} .sidebar {width:100%;height:100dvh;max-width:none;overflow:hidden;display:flex;flex-direction:column}
#convListPanel {flex:1;min-height:0;display:flex;flex-direction:column} .test-row {padding:20px;border-bottom:1px solid var(--border)}
</style><div class="sidebar"><div class="sidebar-header">
<div class="sidebar-header-brand"><div class="sh-title-row"><a class="sh-title-link"><span class="sh-title-main">Claude Command Center</span></a></div></div>
<div class="mobile-home-status"><div id="localCiMobileList"><button class="local-ci-mobile-toggle" aria-expanded="false">CI: checks passing</button></div></div>
<div class="worker-activity-line"><span>Workers: 2 live</span></div></div>
<div class="new-session-panel"><button class="new-session-btn" id="sidebarNewBtn">+ New session</button><button id="sidebarSearchBtn">Search sessions</button><input id="convSearch" aria-label="Search sessions"></div>
<div id="convListPanel" class="conv-list-panel"><div class="today-tray" id="todayTray" hidden></div>
<div class="conv-ptr" id="convPtr" hidden></div>
<div class="log-list" id="convList" style="overflow-y:auto;flex:1;min-height:0;max-height:none;">${rows}</div></div></div>
<script>document.querySelector('#sidebarNewBtn').onclick=()=>window.newClicks=(window.newClicks||0)+1;</script>
<script>${controller}</script>`;

(async () => {
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', req.url === '/app.css' ? 'text/css' : 'text/html');
    res.end(req.url === '/app.css' ? css : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const url = `http://127.0.0.1:${server.address().port}`;
    const frame = () => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    const scroll = async top => {
      await page.evaluate(top => {
        const list = document.querySelector('#convList');
        list.scrollTop = top;
        list.dispatchEvent(new Event('scroll'));
      }, top);
      await frame();
    };
    const geometry = () => page.evaluate(() => {
      const rect = sel => document.querySelector(sel).getBoundingClientRect().toJSON();
      const list = document.querySelector('#convList');
      return {list:rect('#convList'), panel:rect('#convListPanel'), brand:rect('.sidebar-header-brand'),
        status:rect('.mobile-home-status'), workers:rect('.worker-activity-line'), newPanel:rect('.new-session-panel'),
        row:rect('.test-row'), clientHeight:list.clientHeight, scrollTop:list.scrollTop,
        spacer:parseFloat(getComputedStyle(list, '::before').height) || 0,
        quick:getComputedStyle(document.querySelector('.mobile-home-new')).visibility,
        newInert:document.querySelector('.new-session-panel').inert,
        travel:Math.ceil(document.querySelector('.new-session-panel').getBoundingClientRect().bottom - document.querySelector('.sidebar-header-brand').getBoundingClientRect().bottom)};
    });
    for (const width of [390, 320, 768]) {
      await page.setViewport({width, height:844});
      await page.goto(url, {waitUntil:'load'});
      await page.waitForFunction(() => document.body.classList.contains('mobile-home-scrollaway'));
      await frame();
      const top0 = await geometry();
      assert.ok(top0.spacer > 60, 'list reserves room for the pinned bands');
      assert.ok(top0.status.top >= top0.brand.bottom - 1, 'status sits below the brand');
      assert.ok(top0.row.top >= top0.newPanel.bottom - 1, 'first row starts below the bands');
      assert.equal(top0.quick, 'hidden', 'quick actions hidden while bands show');
      for (const top of [24, 48, 80, 120, 180, 400]) {
        await scroll(top);
        const g = await geometry();
        const shift = Math.min(top, top0.travel);
        // The scroller itself never moves, resizes or animates.
        for (const key of ['top', 'height', 'left', 'width']) {
          assert.ok(Math.abs(g.list[key] - top0.list[key]) < 0.5, `list ${key} holds still at ${top}`);
          assert.ok(Math.abs(g.panel[key] - top0.panel[key]) < 0.5, `panel ${key} holds still at ${top}`);
        }
        assert.equal(g.clientHeight, top0.clientHeight);
        assert.equal(await page.evaluate(() => document.getAnimations().filter(a => !(a instanceof CSSTransition)).length), 0, 'no scripted animations');
        assert.equal(await page.$$eval('.mobile-home-folding, .mobile-home-list-moving', els => els.length), 0);
        // Rows move 1:1 with the scroll and the bands move with them.
        assert.ok(Math.abs(g.row.top - (top0.row.top - top)) < 1, 'rows track the scroll');
        assert.ok(Math.abs(g.newPanel.top - (top0.newPanel.top - shift)) < 1, 'bands track the rows');
        assert.ok(Math.abs(g.brand.top - top0.brand.top) < 0.5, 'brand stays pinned');
        assert.equal(g.newInert, shift >= top0.travel, 'hidden bands are inert');
        assert.equal(g.quick, shift >= top0.travel - 1 ? 'visible' : 'hidden');
      }
      for (const top of [120, 24, 0]) {
        await scroll(top);
        const g = await geometry();
        assert.ok(Math.abs(g.status.top - (top0.status.top - Math.min(top, top0.travel))) < 1, 'reverse retraces');
      }
      const back = await geometry();
      assert.ok(Math.abs(back.newPanel.top - top0.newPanel.top) < 0.5 && Math.abs(back.row.top - top0.row.top) < 0.5);
      // Quick actions work once folded; search brings the bands back.
      await scroll(400);
      // CCC-683: a remeasure while folded (the CI line re-renders about once
      // a second) must not make the quick buttons vanish and fade back in.
      await page.evaluate(() => new Promise(r => setTimeout(r, 300)));  // the fold's own fade-in
      const dips = await page.evaluate(() => new Promise(done => {
        const quick = document.querySelector('.mobile-home-new');
        let low = 0, n = 0;
        const iv = setInterval(() => { window.dispatchEvent(new Event('resize')); if (++n >= 4) clearInterval(iv); }, 60);
        const t0 = performance.now();
        const tick = () => {
          const style = getComputedStyle(quick);
          if (style.display === 'none' || parseFloat(style.opacity) < 0.99) low++;
          if (performance.now() - t0 < 600) requestAnimationFrame(tick); else done(low);
        };
        requestAnimationFrame(tick);
      }));
      assert.equal(dips, 0, 'folded quick buttons hold steady through a remeasure');
      await page.click('.mobile-home-new');
      assert.equal(await page.evaluate(() => window.newClicks), 1);
      await page.click('.mobile-home-search');
      await frame();
      assert.equal(await page.evaluate(() => document.activeElement.id), 'convSearch');
      const searching = await geometry();
      assert.ok(Math.abs(searching.newPanel.top - top0.newPanel.top) < 1, 'focused search stays visible');
      assert.equal(searching.newInert, false);
      await page.evaluate(() => document.activeElement.blur());
      await page.evaluate(() => new Promise(r => setTimeout(r, 20)));
      await scroll(0);
      // Pull-to-refresh shows below the bands instead of under them.
      await page.evaluate(() => { const ptr = document.querySelector('#convPtr'); ptr.hidden = false; ptr.style.height = '40px'; });
      await frame();
      const pulling = await geometry();
      assert.equal(pulling.spacer, 0);
      assert.ok(await page.$eval('#convPtr', el => el.getBoundingClientRect().top) >= pulling.newPanel.bottom - 1, 'pull indicator visible');
      await page.evaluate(() => { const ptr = document.querySelector('#convPtr'); ptr.hidden = true; ptr.style.height = ''; });
    }
    // A taller band (expanded CI line) is re-measured without a scroll.
    await page.setViewport({width:390, height:844});
    await page.goto(url, {waitUntil:'load'});
    await page.waitForFunction(() => document.body.classList.contains('mobile-home-scrollaway'));
    const before = await geometry();
    await page.evaluate(() => { document.querySelector('.mobile-home-status').style.height = '90px'; });
    await page.waitForFunction(h => parseFloat(getComputedStyle(document.querySelector('#convList'), '::before').height) > h + 20, {}, before.spacer);
    // Opening a session or widening to desktop restores the normal layout.
    await page.evaluate(() => document.body.classList.add('mobile-show-main'));
    await page.waitForFunction(() => !document.body.classList.contains('mobile-home-scrollaway'));
    assert.equal(await page.$eval('.new-session-panel', el => el.getAttribute('style') || ''), '');
    await page.evaluate(() => document.body.classList.remove('mobile-show-main'));
    await page.waitForFunction(() => document.body.classList.contains('mobile-home-scrollaway'));
    await page.setViewport({width:1440, height:900});
    await page.waitForFunction(() => !document.body.classList.contains('mobile-home-scrollaway'));
    assert.equal(await page.$eval('#convList', el => getComputedStyle(el, '::before').content), 'none');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({pass:true}));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
