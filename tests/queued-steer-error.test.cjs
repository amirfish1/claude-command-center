// A rejected steer must leave a visible reason on its queued card.
const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('  const _queuedSteerErrors = new Map();');
const end = source.indexOf('  function beginOptimisticSteerMove(', start);
assert.ok(start >= 0 && end > start, 'queued steer error helpers not found');
const helpers = source.slice(start, end);

let browser;
before(async () => { browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] }); });
after(async () => { if (browser) await browser.close(); });

async function run(fn) {
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="queued-steer-tray"><div class="event user_text"><div class="user-msg">hello   world</div></div></div>');
    await page.evaluate(`window._normSend = t => String(t).replace(/\\s+/g, ' ').trim();\n${helpers}\nObject.assign(window, { setQueuedSteerError, clearQueuedSteerError, applyQueuedSteerError });`);
    return await page.evaluate(fn);
  } finally { await page.close(); }
}

test('a failed steer leaves a persistent reason on the queued card', async () => {
  const text = await run(() => {
    const el = document.querySelector('.event.user_text');
    setQueuedSteerError('s1', 'hello world', 'Codex CLI not found');
    applyQueuedSteerError(el, 's1');
    applyQueuedSteerError(el, 's1');
    return [el.querySelectorAll('.send-queued-error').length, el.querySelector('.send-queued-error').textContent];
  });
  assert.deepEqual(text, [1, 'Not delivered: Codex CLI not found']);
});

test('the reason is cleared on the next attempt and is scoped to its session', async () => {
  const result = await run(() => {
    const el = document.querySelector('.event.user_text');
    setQueuedSteerError('s1', 'hello world', 'boom');
    applyQueuedSteerError(el, 's2');
    const otherSession = el.querySelectorAll('.send-queued-error').length;
    applyQueuedSteerError(el, 's1');
    clearQueuedSteerError('s1', 'hello world');
    applyQueuedSteerError(el, 's1');
    return [otherSession, el.querySelectorAll('.send-queued-error').length];
  });
  assert.deepEqual(result, [0, 0]);
});
