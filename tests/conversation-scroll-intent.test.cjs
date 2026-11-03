const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = process.env.CCC_SCROLL_APP_SOURCE
  ? fs.readFileSync(process.env.CCC_SCROLL_APP_SOURCE, 'utf8')
  : fs.readFileSync('static/app.js', 'utf8');
const helpers = app.slice(app.indexOf('  const CONV_BOTTOM_TOLERANCE'),
  app.indexOf('  // CCC-131: per-conversation'));
const pinStart = app.indexOf('    view._convEndAffordanceAttached = true;');
const pinSetup = app.slice(pinStart, app.indexOf('\n  function ensureAllConversationEndAffordances', pinStart));
const historyStart = app.indexOf('  function _insertLoadEarlierBanner(');
const history = app.slice(historyStart, app.indexOf('  // Codex rollouts spend', historyStart));
const scrollHelper = app.slice(app.indexOf('  function scrollConversationToEnd('),
  app.indexOf('  // CCC-1299 tap-backs'));
let browser;
test.before(async () => {
  browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
});
test.after(async () => { await browser?.close(); });

async function fixture() {
  const page = await browser.newPage();
  await page.setContent(`<style>
    body { margin: 0; background: #151719; color: #ddd; font: 16px sans-serif; }
    .conversations-view { height: 560px; overflow-y: auto; overflow-anchor: none; }
    .event { height: 100px; box-sizing: border-box; padding: 16px; border-bottom: 1px solid #444; }
  </style><div class="conversations-view">${Array.from({ length: 20 }, (_, i) =>
    `<div class="event" data-jsonl-line="${i + 100}">Message ${i + 100}</div>`).join('')}</div>`);
  await page.evaluate(({ helpers, pinSetup, scrollHelper, history }) => {
    window.view = document.querySelector('.conversations-view');
    window.updateConversationEndAffordance = () => {};
    window._topVisibleAnchor = v => {
      const row = [...v.querySelectorAll('.event')].find(e => e.offsetTop + e.offsetHeight > v.scrollTop);
      return row && { line: row.dataset.jsonlLine, delta: row.offsetTop - v.scrollTop };
    };
    window._restoreAnchor = (v, a) => {
      if (a) v.scrollTop = v.querySelector(`[data-jsonl-line="${a.line}"]`).offsetTop - a.delta;
    };
    window._observeConvEventSizes = () => {};
    window._ensureLoadEarlierStyle = () => {};
    window.pane = { conversationId: 'test', firstLine: 100 };
    window.paneByPaneId = () => pane;
    window._contNextParentForPane = () => '';
    window._showConvLoading = () => ({ remove() {} });
    // Keep the history request pending while the reader continues scrolling.
    window.fetchConversationEvents = () => new Promise(resolve => {
      window.finishHistory = () => {
        const top = view.scrollTop, height = view.scrollHeight;
        view.insertAdjacentHTML('afterbegin', '<div class="event">Earlier message</div>');
        view.scrollTop = top + view.scrollHeight - height;
        resolve();
      };
    });
    view.scrollTop = view.scrollHeight;
    new Function(helpers + scrollHelper
      + 'window.isConversationAtBottom = isConversationAtBottom;'
      + 'window.scrollConversationToEnd = scrollConversationToEnd;'
      + 'function attach(view) {\n' + pinSetup + '\nattach(view);'
      + history + '\nwindow.addHistory = () => _insertLoadEarlierBanner(view, "test", "p1");')();
  }, { helpers, pinSetup, scrollHelper, history });
  await page.evaluate(() => new Promise(requestAnimationFrame));
  return page;
}

test('an upward trackpad tick beats a mutation before the scroll event', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      const before = view.scrollTop;
      view.dispatchEvent(new WheelEvent('wheel', { deltaY: -0.5 }));
      // A streaming microtask can land before the browser scrolls the view.
      view.lastElementChild.append(' streamed text');
      await Promise.resolve();
      const pinnedBeforeScroll = view._pinnedToBottom;
      view.scrollTop = before - 1;
      view.dispatchEvent(new Event('scroll'));
      const follows = isConversationAtBottom(view);
      if (follows) scrollConversationToEnd(view);
      view.lastElementChild.append(' more text');
      await Promise.resolve();
      return { pinnedBeforeScroll, follows, before, after: view.scrollTop };
    });
    assert.equal(result.pinnedBeforeScroll, false);
    assert.equal(result.follows, false);
    assert.equal(result.after, result.before - 1);
  } finally { await page.close(); }
});

test('stationary scroll and near-bottom downward drift do not repin; reaching end does', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      view.scrollTop -= 40;
      view.dispatchEvent(new Event('scroll'));
      view.dispatchEvent(new Event('scroll'));
      const stationary = view._pinnedToBottom;
      view.scrollTop += 10;
      view.dispatchEvent(new Event('scroll'));
      const drift = view._pinnedToBottom;
      view.lastElementChild.append(' poll update');
      await Promise.resolve();
      const distance = view.scrollHeight - view.clientHeight - view.scrollTop;
      view.scrollTop = view.scrollHeight;
      view.dispatchEvent(new Event('scroll'));
      const atEnd = view._pinnedToBottom;
      view.insertAdjacentHTML('beforeend', '<div class="event">New reply</div>');
      await Promise.resolve();
      return { stationary, drift, distance, atEnd,
        finalDistance: view.scrollHeight - view.clientHeight - view.scrollTop };
    });
    assert.equal(result.stationary, false);
    assert.equal(result.drift, false);
    assert.equal(result.distance, 30);
    assert.equal(result.atEnd, true);
    assert.equal(result.finalDistance, 0);
  } finally { await page.close(); }
});

test('native wheel scrolling survives repeated transcript updates', async () => {
  const page = await fixture();
  try {
    await page.mouse.move(200, 300);
    const before = await page.evaluate(() => view.scrollTop);
    for (let i = 0; i < 6; i++) {
      await page.mouse.wheel({ deltaY: -8 });
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.evaluate(() => {
        const follows = isConversationAtBottom(view);
        view.lastElementChild.append(' update');
        if (follows) scrollConversationToEnd(view);
      });
    }
    const after = await page.evaluate(() => view.scrollTop);
    assert.ok(before - after >= 40, `upward scroll was lost: ${before} -> ${after}`);
    if (process.env.CCC_SCROLL_SCREENSHOT) {
      await page.screenshot({ path: process.env.CCC_SCROLL_SCREENSHOT });
    }
  } finally { await page.close(); }
});

test('history response preserves scrolling done while the request was pending', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      view.scrollTop = 600;
      view.dispatchEvent(new Event('scroll'));
      addHistory();
      view.querySelector('.conv-load-earlier').click();
      view.scrollTop = 250;
      view.dispatchEvent(new Event('scroll'));
      const anchor = _topVisibleAnchor(view);
      finishHistory();
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const row = view.querySelector(`[data-jsonl-line="${anchor.line}"]`);
      return { before: anchor.delta, after: row.offsetTop - view.scrollTop };
    });
    assert.equal(result.after, result.before);
  } finally { await page.close(); }
});

test('a fresh open resumes following after leaving the previous conversation scrolled up', async () => {
  const page = await fixture();
  try {
    const result = await page.evaluate(async () => {
      view.scrollTop -= 40;
      view.dispatchEvent(new Event('scroll'));
      view.replaceChildren();
      const follows = isConversationAtBottom(view);
      view.innerHTML = '<div class="event" style="height:900px">New conversation</div>';
      if (follows) scrollConversationToEnd(view);
      await new Promise(requestAnimationFrame);
      // A shorter new conversation must not look like an upward gesture.
      view.dispatchEvent(new Event('scroll'));
      view.insertAdjacentHTML('beforeend', '<div class="event">New content</div>');
      await Promise.resolve();
      return { follows, pinned: view._pinnedToBottom,
        distance: view.scrollHeight - view.clientHeight - view.scrollTop };
    });
    assert.equal(result.follows, true);
    assert.equal(result.pinned, true);
    assert.equal(result.distance, 0);
  } finally { await page.close(); }
});
