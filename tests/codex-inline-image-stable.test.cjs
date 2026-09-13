// The native Codex transcript rebuilds on every poll. An image in a turn must
// keep its already-decoded <img>, or it collapses to zero height for a frame
// and the transcript below it jumps.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');

const PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==';

let browser;
test.before(async () => { browser = await puppeteer.launch({ headless: true }); });
test.after(async () => { await browser.close(); });

test('an image keeps its node across transcript polls', async () => {
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="conv-pane is-codex-session" data-pane-id="p1"><div class="conversations-view"></div><div class="conv-input-bar"><textarea></textarea></div></div>');
    await page.evaluate((pixel) => {
      const content = [{ type: 'text', text: 'look' }, { type: 'image', url: pixel }, { type: 'image', url: pixel }];
      const thread = { id: 'one', turns: [{ id: 't1', items: [{ id: 'u1', type: 'userMessage', content }] }] };
      window.polls = 0;
      window.fetch = async (url) => {
        if (String(url).includes('/events') || String(url).includes('/state')) window.polls += 1;
        return { ok: true, json: async () => String(url).includes('/catalog')
          ? { ok: true, methods: [], experimental_enabled: true }
          : { ok: true, connected: true, generation: 'g', cursor: 1, thread, requests: [], events: [] } };
      };
    }, PIXEL);
    await page.addScriptTag({ path: path.resolve('static/codex-client.js') });
    const result = await page.evaluate(async () => {
      const pane = document.querySelector('.conv-pane');
      await window.CCCCodexClient.attachInline({ paneEl: pane, viewEl: pane.querySelector('.conversations-view'), threadId: 'one', repoPath: '/repo' });
      const images = () => Array.from(pane.querySelectorAll('[data-codex-transcript] img'));
      await new Promise(resolve => { const wait = () => images().length === 2 && images().every(img => img.complete) ? resolve() : setTimeout(wait, 20); wait(); });
      const before = images();
      const host = pane.querySelector('[data-codex-transcript]');
      let rebuilt = false;
      new MutationObserver(() => { rebuilt = true; }).observe(host, { childList: true });
      await new Promise(resolve => { const wait = () => rebuilt ? resolve() : setTimeout(wait, 50); wait(); });
      const after = images();
      return { count: after.length, distinct: after[0] !== after[1], kept: before.every((img, i) => img === after[i]) };
    });
    assert.deepEqual(result, { count: 2, distinct: true, kept: true });
  } finally {
    await page.close();
  }
});
