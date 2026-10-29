const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf(`      return '<div class="conv-item'`);
const endMarker = `          : '');`;
assert.ok(start >= 0);
const template = app.slice(start, app.indexOf(endMarker, start) + endMarker.length);
function row() {
  const values = {
    c: { id: 'layout-test', session_id: 'layout-test' },
    title: 'A longer session title that should keep the same wrapping while its agent starts and stops working',
    titleClass: '', rel: 'now',
    pctBadgeHtml: '<span class="conv-pct-badge">30%</span>',
    escapeHtml: String, escapeAttr: String,
    rowDraggableAttr: () => 'false', isMobileChromeActive: () => false,
    _uxFixesWorkerHistoryHtml: () => '', _continuationChainBadgeHtml: () => '',
    sessionStuckWarningHtml: () => '', workerOriginBadgeHtml: () => '',
    ideaTicketChipHtml: () => '', _ideaRowLine: () => '',
    readyToCloseArchiveHtml: () => '', _nyaRowBlockHtml: () => '',
    window: {},
  };
  const context = new Proxy(values, {
    has: () => true,
    get: (target, key) => key in target ? target[key] : '',
  });
  vm.createContext(context);
  return vm.runInContext(`(function () { ${template} })()`, context);
}

test('sidebar status changes preserve title position, width and row height', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1200, height: 800 });
    for (const width of [320, 440, 800]) {
      await page.setContent(`<style>${fs.readFileSync('static/app.css', 'utf8')}</style>
        <div class="wrap-titles" style="width:${width}px;container:sidebar / inline-size">
          <div id="convList"><div class="conv-current-sessions-scroll">${row()}</div></div>
        </div>`);
      await page.evaluate(async () => {
        await document.fonts.ready;
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      const measure = () => page.evaluate(() => {
        const title = document.querySelector('.conv-title').getBoundingClientRect();
        const row = document.querySelector('.conv-item').getBoundingClientRect();
        return { x: title.x, width: title.width, height: title.height, rowHeight: row.height };
      });
      const idle = await measure();
      for (const marker of ['conv-working-dot', 'conv-needs-you', '']) {
        await page.evaluate(marker => {
          document.querySelectorAll('.conv-working-dot, .conv-needs-you').forEach(el => el.remove());
          if (marker) {
            const dot = document.createElement('span');
            dot.className = marker;
            if (marker === 'conv-needs-you') dot.textContent = '●';
            document.querySelector('.conv-main-row').insertBefore(dot, document.querySelector('.conv-title'));
          }
        }, marker);
        assert.deepEqual(await measure(), idle, `${width}px sidebar shifts for ${marker || 'idle'}`);
      }
    }
    const densePosition = await page.evaluate(() => {
      document.getElementById('convList').classList.add('workers-dense');
      const marker = document.createElement('span');
      marker.className = 'conv-needs-you';
      marker.textContent = '●';
      document.querySelector('.conv-main-row').appendChild(marker);
      return getComputedStyle(marker).position;
    });
    assert.equal(densePosition, 'static', 'Workers table needs-you marker must retain its grid cell');
    if (process.env.STATUS_LAYOUT_SCREENSHOT) {
      await page.screenshot({ path: process.env.STATUS_LAYOUT_SCREENSHOT });
    }
  } finally { await browser.close(); }
});
