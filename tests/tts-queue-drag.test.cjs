// CCC-278: the TTS queue inspector drags anywhere by its header, with a
// mouse or a finger; a plain tap still expands it, and the spot persists.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const slice = app.slice(app.indexOf("  const TTS_QUEUE_POS_KEY"), app.indexOf('  function _ttsRenderDebugQueue('));

test('TTS queue inspector drags by mouse and touch, taps still toggle', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1000, height: 700, hasTouch: true });
    // A real origin, so localStorage (the saved position) is available.
    await page.setRequestInterception(true);
    page.on('request', req => req.respond({ status: 200, contentType: 'text/html', body: `<style>#q{position:fixed;top:74px;left:12px;width:240px;background:#222;color:#fff}
      #q summary{padding:7px 10px;touch-action:none}</style>
      <details id="q"><summary>TTS: 2 sent · 0 waiting</summary><div>body</div></details>` }));
    await page.goto('http://ccc-drag.test/');
    await page.addScriptTag({ content: slice + `
      const q = document.getElementById('q');
      _ttsMakeDebugQueueDraggable(q, q.querySelector('summary'));` });
    const box = () => page.evaluate(() => { const r = document.getElementById('q').getBoundingClientRect(); return { x: r.left, y: r.top, open: document.getElementById('q').open }; });

    await page.mouse.move(40, 85); await page.mouse.down();
    await page.mouse.move(200, 200, { steps: 6 }); await page.mouse.move(500, 400, { steps: 6 });
    await page.mouse.up();
    let b = await box();
    assert.ok(Math.abs(b.x - 472) < 3 && Math.abs(b.y - 389) < 3, JSON.stringify(b));
    assert.equal(b.open, false, 'a drag does not toggle the panel');
    assert.deepEqual(JSON.parse(await page.evaluate(() => localStorage.getItem('ccc-tts-queue-pos'))), { left: b.x, top: b.y });

    await page.touchscreen.touchStart(b.x + 20, b.y + 10);
    await page.touchscreen.touchMove(b.x - 100, b.y - 100);
    await page.touchscreen.touchMove(b.x - 300, b.y - 300);
    await page.touchscreen.touchEnd();
    const t = await box();
    // The finger grabbed the header 20,10 in, and keeps that grip.
    assert.ok(Math.abs(t.x - (b.x - 320)) < 3 && Math.abs(t.y - (b.y - 310)) < 3, JSON.stringify(t));

    // Dragging off-screen clamps so the header stays reachable.
    await page.mouse.move(t.x + 20, t.y + 10); await page.mouse.down();
    await page.mouse.move(2000, 2000, { steps: 8 }); await page.mouse.up();
    const c = await box();
    assert.ok(c.x <= 1000 - 240 + 1 && c.y <= 700 - 20, JSON.stringify(c));

    await page.click('#q summary');
    assert.equal((await box()).open, true, 'a tap still expands');
  } finally {
    await browser.close();
  }
});
