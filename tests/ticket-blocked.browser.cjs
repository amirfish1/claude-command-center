// Browser checks for progressive disclosure, retry and reply navigation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
(async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 390, height: 844 });
    await page.setContent('<main id="modal"></main>');
    await page.addStyleTag({ content: fs.readFileSync(path.join(root, 'static/ticket-prose.css'), 'utf8') });
    await page.addScriptTag({ path: path.join(root, 'static/ticket-prose.js') });
    await page.evaluate(() => {
      window.calls = [];
      window.fail = true;
      window.fetch = async (url, args) => {
        window.calls.push({ url, body: JSON.parse(args.body) });
        return { ok: true, json: async () => window.fail ? { ok: false } : {
          ok: true, reading: { question: 'Show shorter completed task rows?', proposal: 'Load details on open.',
            consequence: 'You will open a task to see its full history.', context: 'Loading has slowed down.',
            alternatives: [{ label: 'Keep current rows', explanation: 'No change is proposed for this option.' }] }
        } };
      };
      const source = '[Q12:rows] Loading slowed down. Recommended: shorter rows. Approve the change?';
      const root = document.querySelector('#modal');
      root.innerHTML = window.CCCTicketProse.renderBlocked(source) + '<input class="uxq-answer-input" aria-label="Reply">';
      window.CCCTicketProse.hydrateBlocked(root, source);
    });
    await page.waitForSelector('.tp-reading-error');
    assert.equal(await page.$eval('.tp-blocked-source', e => !!e.offsetHeight), true);
    assert.equal(await page.evaluate(() => calls.length), 1);
    await page.evaluate(() => { window.fail = false; });
    await page.click('.tp-explain');
    await page.waitForSelector('.tp-reading-question');
    assert.equal(await page.$eval('.tp-reading-source', e => e.open), false);
    await page.click('.tp-reading-source > summary');
    assert.equal(await page.$eval('.tp-reading-source', e => e.open), true);
    await page.click('.tp-reading-reply');
    assert.equal(await page.evaluate(() => document.activeElement.className), 'uxq-answer-input');
    assert.equal(await page.evaluate(() => calls.length), 2); // navigation never submits
    assert.equal(await page.$eval('.tp-blocked', e => e.scrollWidth <= e.clientWidth), true);
    console.log('PASS: fallback, retry, disclosure, mobile width, reply focus without submission');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
