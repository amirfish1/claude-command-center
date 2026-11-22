// Run with node tests/mobile-reader-scrollaway.browser.cjs. Uses the shipped
// CSS and controller in a local fixture.
//
// On phones the reader toolbar (last message + speaker) above a transcript
// scrolls away with it. The transcript's own box must never move or resize
// while it scrolls: iOS ends a momentum flick when that happens.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const start = source.indexOf('(function initMobileReaderScrollaway()');
const controller = source.slice(start, source.indexOf('// CI, usage and savings', start));
const css = fs.readFileSync(path.join(root, 'static/app.css'), 'utf8');
const events = Array.from({length:60}, (_, i) => `<div class="event" style="height:60px">Message ${i + 1}</div>`).join('');
const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/app.css"><style>
body {display:block;margin:0;padding:0}
#convSplit {height:100dvh;display:flex}
.conv-pane {flex:1;display:grid;position:relative;min-height:0;grid-template-rows:auto auto 1fr auto;
  grid-template-areas:"written" "present-toolbar" "conv" "input"}
.conv-pane > .conv-last-written {grid-area:written} .conv-pane > .conv-presentation-toolbar {grid-area:present-toolbar}
.conv-pane > .conversations-view {grid-area:conv;min-height:0} .conv-pane > .input {grid-area:input;height:56px}
</style><div id="convSplit"><div class="conv-pane">
<div class="conv-last-written">Last written: now</div>
<div class="conv-presentation-toolbar is-reader-only"><div class="conv-reader-audio"><button class="conv-scroll-speak-btn">Speak</button></div></div>
<div class="conversations-view"><div class="conv-live-tool-strip" style="position:sticky;top:0">Running tool</div>${events}</div>
<div class="input"></div></div></div>
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
    const frame = () => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    const scrollTo = async fromBottom => {
      await page.evaluate(d => { const v = document.querySelector('.conversations-view'); v.scrollTop = v.scrollHeight - v.clientHeight - d; }, fromBottom);
      await frame();
    };
    const geometry = () => page.evaluate(() => {
      const rect = sel => document.querySelector(sel).getBoundingClientRect().toJSON();
      const bar = document.querySelector('.conv-presentation-toolbar');
      return {view:rect('.conversations-view'), bar:rect('.conv-presentation-toolbar'), written:rect('.conv-last-written'),
        strip:rect('.conv-live-tool-strip'), inert:bar.inert, on:document.querySelector('.conv-pane').classList.contains('reader-scrollaway')};
    });
    await page.setViewport({width:390, height:844});
    await page.goto(`http://127.0.0.1:${server.address().port}`, {waitUntil:'load'});
    await page.waitForFunction(() => document.querySelector('.conv-pane').classList.contains('reader-scrollaway'));
    await scrollTo(0);
    const rest = await geometry();
    assert.ok(Math.abs(rest.bar.top - rest.written.bottom) < 1, 'toolbar sits under Last written');
    assert.ok(Math.abs(rest.view.top - rest.bar.top) < 1, 'transcript starts under the overlaid toolbar');
    assert.ok(rest.strip.top >= rest.bar.bottom - 1, 'stuck live strip shows below the toolbar');
    // Older messages: the toolbar slides away 1:1, the transcript box holds still.
    for (const d of [10, 30, 60, 200, 600]) {
      await scrollTo(d);
      const g = await geometry();
      const shift = Math.min(d, rest.bar.height);
      for (const key of ['top', 'height', 'left', 'width']) assert.ok(Math.abs(g.view[key] - rest.view[key]) < 0.5, `view ${key} holds still at ${d}`);
      assert.ok(Math.abs(g.bar.top - (rest.bar.top - shift)) < 1, `toolbar tracks the scroll at ${d}`);
      assert.equal(g.inert, shift >= rest.bar.height, JSON.stringify({d, shift, h:rest.bar.height, top:g.bar.top}));
      assert.ok(g.strip.top >= g.bar.bottom - 1 && g.strip.top <= g.bar.bottom + 1 || g.strip.top > rest.view.top + 200, 'sticky strip follows the toolbar edge '+JSON.stringify({d,strip:g.strip.top,bar:g.bar.bottom,view:g.view.top}));
      assert.equal(await page.evaluate(() => document.getAnimations().length), 0);
    }
    // Newer messages: stays hidden until the last stretch, then slides back in.
    await scrollTo(300);
    assert.equal((await geometry()).inert, true, 'stays hidden scrolling newer mid-transcript');
    await scrollTo(40);
    assert.ok(Math.abs((await geometry()).bar.top - (rest.bar.top - 40)) < 1, 'slides back near the latest message');
    await scrollTo(0);
    assert.ok(Math.abs((await geometry()).bar.top - rest.bar.top) < 0.5, 'fully back at the latest message');
    // Focus inside the toolbar keeps it visible.
    await scrollTo(600);
    await page.evaluate(() => document.querySelector('.conv-scroll-speak-btn').removeAttribute('disabled'));
    await page.evaluate(() => { const bar = document.querySelector('.conv-presentation-toolbar'); bar.inert = false; bar.querySelector('button').focus(); });
    await frame();
    assert.ok(Math.abs((await geometry()).bar.top - rest.bar.top) < 1, 'focused toolbar stays visible');
    await page.evaluate(() => document.activeElement.blur());
    // Presentation mode or desktop width restores the normal row.
    await page.evaluate(() => document.querySelector('.conv-presentation-toolbar').classList.remove('is-reader-only'));
    await page.waitForFunction(() => !document.querySelector('.conv-pane').classList.contains('reader-scrollaway'));
    await page.evaluate(() => document.querySelector('.conv-presentation-toolbar').classList.add('is-reader-only'));
    await page.waitForFunction(() => document.querySelector('.conv-pane').classList.contains('reader-scrollaway'));
    await page.setViewport({width:1440, height:900});
    await page.waitForFunction(() => !document.querySelector('.conv-pane').classList.contains('reader-scrollaway'));
    const desk = await geometry();
    assert.ok(desk.view.top >= desk.bar.bottom - 1, 'desktop keeps the toolbar row');
    assert.equal(await page.$eval('.conv-presentation-toolbar', el => el.style.transform), '');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({pass:true}));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
