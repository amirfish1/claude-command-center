// CCC-1051 E2E: clicking the breadcrumb session chip copies "ID (transcript path)".
const puppeteer = require('./require-puppeteer.js');
const { findChromePath } = require('./puppeteer-browser-config.js');

(async () => {
  const chromePath = findChromePath();
  const browser = await puppeteer.launch({ executablePath: chromePath, args: ['--no-sandbox'] });
  let failed = false;
  const fail = (msg) => { failed = true; console.error('FAIL: ' + msg); };
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900 });
    await browser.defaultBrowserContext().overridePermissions('http://127.0.0.1:8090', ['clipboard-read', 'clipboard-write']);
    await page.evaluateOnNewDocument(() => { localStorage.setItem('ccc-tour-done', '1'); });

    await page.goto('http://127.0.0.1:8090', { waitUntil: 'load', timeout: 120000 });
    await page.waitForSelector('.conv-tab-bar [data-conv-tab], #conversationsView .conv-item, .conv-item', { timeout: 300000 }).catch(() => {});

    // Wait for the archive list to render rows, then click the first one.
    await page.waitForFunction(() => {
      const rows = document.querySelectorAll('.conv-item');
      return rows.length > 0;
    }, { timeout: 300000, polling: 1000 });

    await page.evaluate(() => document.querySelector('.conv-item').click());
    await page.waitForFunction(() => {
      const el = document.getElementById('convSessionId');
      return el && el.dataset.copySessionId;
    }, { timeout: 30000, polling: 500 });

    const info = await page.evaluate(() => {
      const el = document.getElementById('convSessionId');
      return { sid: el.dataset.copySessionId, path: el.dataset.copyTranscriptPath || '', title: el.title };
    });
    console.log('breadcrumb sid: ' + info.sid);
    console.log('transcript path: ' + (info.path || '(none)'));
    if (!info.sid) fail('no session id on breadcrumb');
    if (!info.path) fail('no transcript path captured on breadcrumb');

    await page.evaluate(() => document.getElementById('convSessionId').click());
    await new Promise(r => setTimeout(r, 800));
    const clip = await page.evaluate(() => navigator.clipboard.readText().catch(() => ''));
    console.log('clipboard: ' + clip);
    if (!clip.includes(info.sid)) fail('clipboard missing session id');
    if (info.path && !clip.includes(info.path)) fail('clipboard missing transcript path');
    if (info.path && !/^[0-9a-f-]{36} \(.+\)$/.test(clip.trim()) && !clip.includes(' (')) fail('clipboard not in "ID (path)" shape: ' + clip);

    // The chip must restore its formatted markup after the flash.
    await new Promise(r => setTimeout(r, 1200));
    const restored = await page.evaluate(() => {
      const el = document.getElementById('convSessionId');
      return { hasCode: !!el.querySelector('code.sid-short'), text: el.textContent };
    });
    if (!restored.hasCode) fail('breadcrumb did not restore formatted chip: ' + restored.text);

    console.log(failed ? 'E2E RESULT: FAIL' : 'E2E RESULT: PASS');
  } catch (e) {
    fail('exception: ' + (e && e.message));
  } finally {
    if (browser) await browser.close();
  }
  process.exit(failed ? 1 : 0);
})();
