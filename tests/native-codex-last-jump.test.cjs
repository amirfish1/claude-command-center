// The Last/Previous/Next jump buttons find user messages in the native Codex
// transcript too, and measure from below its sticky toolbar.
const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('  const CONV_USER_MESSAGE_SELECTOR');
const end = source.indexOf('\n  }\n', source.indexOf('function _nextUserMessageTarget', start)) + 5;
assert.ok(start >= 0 && end > start);
const code = source.slice(start, end);

let browser;
before(async () => { browser = await puppeteer.launch({ headless: true }); });
after(async () => { if (browser) await browser.close(); });

test('native Codex user messages are jump targets below the sticky toolbar', async () => {
  const page = await browser.newPage();
  try {
    await page.setContent(`<style>
      .conversations-view { height: 300px; overflow: auto; }
      .codex-client-topbar { position: sticky; top: 0; height: 32px; }
      .codex-client-item { height: 400px; }
    </style>
    <div class="conversations-view"><div class="codex-client-shell is-inline">
      <div class="codex-client-topbar"></div>
      <article class="codex-client-item codex-client-message is-user" id="u1"></article>
      <article class="codex-client-item"></article>
      <article class="codex-client-item codex-client-message is-user" id="u2"></article>
      <article class="codex-client-item"></article>
    </div></div>`);
    const result = await page.evaluate((code) => {
      (0, eval)(code.replace(/^/, 'window.__fns = (() => {\n') + '\nreturn { _prevUserMessageTarget, _nextUserMessageTarget };\n})();');
      const view = document.querySelector('.conversations-view');
      view.scrollTop = view.scrollHeight;
      const last = __fns._prevUserMessageTarget(view);
      // Pin u2 just below the toolbar, the way the Last click does.
      view.scrollTop = document.getElementById('u2').offsetTop - 32 - 12;
      const prev = __fns._prevUserMessageTarget(view);
      const next = __fns._nextUserMessageTarget(view);
      return { last: last && last.el.id, isLast: last && last.isLast, prev: prev && prev.el.id, next: next && next.id };
    }, code);
    assert.deepEqual(result, { last: 'u2', isLast: true, prev: 'u1', next: null });
  } finally {
    await page.close();
  }
});
