// The inline native Codex view carries no footer, like Claude panes: the
// pane's top row already shows the live connection.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');

let browser;
test.before(async () => { browser = await puppeteer.launch({ headless: true }); });
test.after(async () => { await browser.close(); });

async function fixture() {
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900 });
  await page.setContent('<div class="conv-pane is-codex-session" data-pane-id="p1"><div class="conversations-view"><div class="event">Old transcript</div></div><div class="conv-input-bar"><textarea></textarea></div></div>');
  await page.addStyleTag({ path: path.resolve('static/codex-client.css') });
  await page.evaluate(() => {
    window.fetch = async (url) => ({ ok: true, json: async () => String(url).includes('/catalog')
      ? { ok: true, methods: [], experimental_enabled: true }
      : { ok: true, connected: true, generation: 'g', cursor: 1, thread: { id: 'one', turns: [] }, requests: [], events: [] } });
  });
  await page.addScriptTag({ path: path.resolve('static/codex-client.js') });
  return page;
}

test('inline Codex drops the footer and keeps Preview features with the tools', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      const pane = document.querySelector('.conv-pane');
      await window.CCCCodexClient.attachInline({ paneEl: pane, viewEl: pane.querySelector('.conversations-view'), threadId: 'one', repoPath: '/repo' });
      const toggle = pane.querySelector('[data-codex-preview]');
      return {
        footers: pane.querySelectorAll('.codex-client-footer').length,
        connection: pane.querySelectorAll('[data-codex-connection]').length,
        toggleInTools: !!(toggle && toggle.closest('.codex-client-toolhead')),
        checked: !!(toggle && toggle.checked),
        connected: !!window.CCCCodexClient.inlineState(pane)?.connected,
      };
    });
    assert.deepEqual(result, { footers: 0, connection: 0, toggleInTools: true, checked: true, connected: true });
  } finally {
    await page.close();
  }
});
