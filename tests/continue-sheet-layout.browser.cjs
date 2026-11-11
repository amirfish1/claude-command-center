// Real browser check: the "Continue in a new session" sheet keeps its
// primary button on screen on phones, short screens, tablets and desktop.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const puppeteer = require('../require-puppeteer');
const {findChromePath} = require('../puppeteer-browser-config');
const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const fitStart = app.indexOf('  function f2FitSheet(panel) {');
const fitEnd = app.indexOf("\n  window.addEventListener('resize'", fitStart);
assert.ok(fitStart >= 0 && fitEnd > fitStart, 'f2FitSheet is present in app.js');
const fitSource = app.slice(fitStart, fitEnd);

const sheet = `<div class="f2c-config" role="dialog" aria-label="Continue in a new session">
  <div class="f2c-sheet-title">Continue in a new session</div>
  <label class="f2c-field"><span class="f2c-field-label">Engine</span><select class="f2c-select"><option>Claude</option></select></label>
  <div class="f2c-field-row">
    <label class="f2c-field"><span class="f2c-field-label">Model</span><select class="f2c-select"><option>sonnet-5-5</option></select></label>
    <label class="f2c-field"><span class="f2c-field-label">Effort</span><select class="f2c-select"><option>Medium</option></select></label>
  </div>
  <div class="f2c-quick-picks"><span class="f2c-quick-picks-label">Recent</span>
    <button type="button" class="f2c-quick-pick">Codex · example</button><button type="button" class="f2c-quick-pick">Claude · sonnet-5-5 · Medium</button></div>
  <label class="f2c-field f2c-prompt-field"><span class="f2c-field-label">Prompt</span>
    <textarea class="f2c-prompt" rows="4" placeholder="Task for the new session (optional)"></textarea></label>
  <div class="f2c-actions">
    <button type="button" class="f2c-start">Continue in new session</button>
    <button type="button" class="f2c-close">Done</button>
  </div>
</div>`;
const page = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/app.css"></head>
<body style="margin:0">
<div style="position:fixed;inset:0;overflow:hidden;display:flex">
  <div class="conv-pane is-active" style="position:relative;display:flex;flex-direction:column;flex:1 1 auto;min-width:0;height:100%">
    <div style="flex:0 0 58px">Session title</div>
    <div class="conversations-view" style="flex:1 1 auto;min-height:0;overflow:auto"><p>Transcript</p></div>
    <div class="conv-input-bar visible" id="convInputBar">
      <div class="f2c-rail"><strong>New session</strong></div>
      <div class="conv-input-row-top"><textarea rows="1" placeholder="Resume and send..."></textarea></div>
      <div class="conv-input-row-bottom"><div class="conv-input-buttons">
        <div class="f2c-panel"><div class="routes"><div class="route is-recommended">
          <button type="button" class="route-main">Continue new</button>${sheet}
        </div></div></div>
        <button type="button">Send</button>
      </div></div>
      <div class="conv-ready-archive-footer"><button type="button" class="conv-ready-archive-btn">Close</button></div>
    </div>
  </div>
</div>
<script>${fitSource}
window.fit = () => document.querySelectorAll('.f2c-panel').forEach(f2FitSheet);
window.fit();</script></body></html>`;

const server = http.createServer((req, res) => {
  if (req.url === '/static/app.css') {
    res.writeHead(200, {'Content-Type': 'text/css'});
    return res.end(fs.readFileSync(path.join(root, 'static/app.css')));
  }
  res.writeHead(200, {'Content-Type': 'text/html'});
  res.end(page);
});

const sizes = [
  {name: 'phone', width: 390, height: 844, touch: true},
  {name: 'small phone', width: 360, height: 640, touch: true},
  {name: 'phone with keyboard up', width: 390, height: 420, touch: true},
  {name: 'narrow window', width: 1000, height: 700, touch: false},
  {name: 'desktop', width: 1440, height: 900, touch: false},
];

(async () => {
  let browser; let checks = 0;
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  try {
    browser = await puppeteer.launch({headless: true, executablePath: findChromePath(), args: ['--no-sandbox']});
    for (const size of sizes) {
      const tab = await browser.newPage();
      await tab.setViewport({width: size.width, height: size.height, isMobile: size.touch, hasTouch: size.touch});
      await tab.goto(base + '/');
      await tab.evaluate(() => window.fit());
      const seen = await tab.evaluate(() => {
        const box = el => { const r = el.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom}; };
        const reachable = el => {
          const r = el.getBoundingClientRect();
          const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return at === el || el.contains(at);
        };
        const sheet = document.querySelector('.f2c-config');
        const start = sheet.querySelector('.f2c-start');
        const close = sheet.querySelector('.f2c-close');
        const before = {sheet: box(sheet), start: box(start), startReachable: reachable(start), closeReachable: reachable(close),
          scrolls: sheet.scrollHeight > sheet.clientHeight, vw: innerWidth, vh: innerHeight,
          transcriptTop: document.querySelector('.conversations-view').getBoundingClientRect().top};
        sheet.scrollTop = 0;
        const title = sheet.querySelector('.f2c-sheet-title');
        return {...before, titleReachable: reachable(title), startStillReachable: reachable(start)};
      });
      const label = size.name + ' ' + size.width + 'x' + size.height + ': ';
      assert.ok(seen.start.top >= 0 && seen.start.bottom <= seen.vh, label + 'Continue is on screen ' + JSON.stringify(seen.start)); checks++;
      assert.ok(seen.startReachable, label + 'Continue can be tapped'); checks++;
      assert.ok(seen.closeReachable, label + 'Done can be tapped'); checks++;
      // Wide windows anchor the sheet to the offer pill, whose place in the
      // toolbar this small page does not reproduce; width matters below 1200.
      if (size.width <= 1200) { assert.ok(seen.sheet.left >= 0 && seen.sheet.right <= seen.vw, label + 'sheet fits the width'); checks++; }
      assert.ok(seen.sheet.top >= seen.transcriptTop - 1, label + 'sheet stays below the session header ' + JSON.stringify(seen.sheet)); checks++;
      assert.ok(seen.titleReachable && seen.startStillReachable, label + 'fields scroll while Continue stays pinned'); checks++;
      if (size.height <= 420) { assert.ok(seen.scrolls, label + 'a short screen scrolls inside the sheet'); checks++; }
      await tab.close();
    }
    console.log('PASS: ' + checks + ' continue sheet checks');
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
