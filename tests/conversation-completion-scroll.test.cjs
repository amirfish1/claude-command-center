const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync('static/app.js', 'utf8');
// Exercise the renderer's actual post-append scroll decision with browser
// layout, including the result event that completes an assistant turn.
const start = app.indexOf('    const _newCompactCard = $view.querySelector(');
const finish = app.indexOf('    ffcRefreshForCurrent();', start);
const decision = app.slice(start, finish);
const scrollHelper = app.slice(app.indexOf('  function scrollConversationToEnd('),
  app.indexOf('  // CCC-1299 tap-backs'));
// Include the former helper when testing an older revision: the regression
// must fail on the old behavior rather than on an undefined function.
const oldStart = app.indexOf('  function scrollConversationToLastReplyStart(');
const oldHelper = oldStart < 0 ? '' : app.slice(oldStart,
  app.indexOf('  function preserveConversationBottomOnComposerPointerDown', oldStart));
let browser;
test.before(async () => {
  browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
});
test.after(async () => { await browser?.close(); });

async function fixture(width) {
  const page = await browser.newPage();
  await page.setViewport({ width, height: 844 });
  await page.setContent(`<style>
    body { margin: 0; background: #151719; color: #ddd; font: 16px sans-serif; }
    .conversations-view { height: 560px; overflow-y: auto; overflow-anchor: none; }
    .event { padding: 16px; border-bottom: 1px solid #444; }
    .assistant-text { line-height: 1.6; }
  </style><div class="conversations-view">
    <div class="event user_text">Explain the scroll behavior</div>
    <div class="event assistant"><div class="assistant-text">${'<p>A long reply with enough detail to extend past the viewport.</p>'.repeat(35)}</div></div>
  </div>`);
  await page.evaluate(({ decision, scrollHelper, oldHelper }) => {
    window.updateConversationEndAffordance = () => {};
    // The slice ends with the TTS prefetch hook (f2147144); it is not under test.
    window.paneId = 'test-pane';
    window.activePaneId = () => 'test-pane';
    window.scheduleTtsPrefetch = () => {};
    window._convScrollByConv = {};
    window.currentConversation = 'test-conversation';
    window.complete = new Function('$view', 'events', 'opts', 'wasAtBottom',
      scrollHelper + oldHelper + decision);
    window.view = document.querySelector('.conversations-view');
    view.scrollTop = view.scrollHeight;
  }, { decision, scrollHelper, oldHelper });
  return page;
}

for (const width of [390, 1280]) {
  test(`completing or replaying a result keeps the reader at the end (${width}px)`, async () => {
    const page = await fixture(width);
    try {
      const result = await page.evaluate(() => {
        for (let i = 0; i < 3; i++) {
          const wasAtBottom = view.scrollHeight - view.scrollTop - view.clientHeight <= 80;
          const row = document.createElement('div');
          row.className = 'event result';
          row.textContent = 'Turn complete';
          view.append(row);
          complete(view, [{ type: 'result' }], {}, wasAtBottom);
          if (view.scrollHeight - view.scrollTop - view.clientHeight > 1) return false;
        }
        return true;
      });
      assert.equal(result, true, 'a result must not jump to the start of the reply');
    } finally { await page.close(); }
  });
  test(`completion preserves a reader's position in history (${width}px)`, async () => {
    const page = await fixture(width);
    try {
      const result = await page.evaluate(() => {
        view.scrollTop = 200;
        const before = view.scrollTop;
        view.insertAdjacentHTML('beforeend', '<div class="event result">Turn complete</div>');
        complete(view, [{ type: 'result' }], {}, false);
        return { before, after: view.scrollTop };
      });
      assert.equal(result.after, result.before);
    } finally { await page.close(); }
  });
}
