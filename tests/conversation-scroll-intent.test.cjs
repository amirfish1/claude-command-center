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

test('mirroring image asks preserves their layout and reading position', async () => {
  const css = fs.readFileSync(process.env.CCC_SCROLL_CSS_SOURCE || 'static/app.css', 'utf8');
  const page = await browser.newPage();
  try {
    for (const width of [1100, 390]) {
      await page.setViewport({ width, height: 800 });
      for (const engine of ['', 'is-webui-session', 'is-webui-session is-codex-session']) {
        await page.setContent(`<style>${css}</style><style>
          body { display:block !important; margin:0; }
          .conv-pane { display:block !important; width:100%; }
          .conversations-view { height:560px !important; overflow-y:auto !important; overflow-anchor:none; }
          .spacer { height:800px; }
        </style><div class="conv-pane ${engine}"><div class="conversations-view">
          <div class="spacer"></div>
          <div class="event user_text peer-message has-user-steer"><div class="user-msg">
            <p>A completed check with two screenshots and enough text to wrap across multiple lines.</p>
            <div class="peer-image-gallery">${[1, 2].map(n => `<figure>
              <img class="msg-image" alt="Example ${n}" width="240" height="180"
                src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='180'/%3E">
              <figcaption>Example ${n}</figcaption></figure>`).join('')}</div>
          </div></div>
          <div class="event assistant" data-jsonl-line="2">The answer being read</div>
          <div class="spacer"></div>
        </div></div>`);
        const result = await page.evaluate(async () => {
          await Promise.all([...document.images].map(img => img.decode()));
          const view = document.querySelector('.conversations-view');
          const ask = view.querySelector('.user_text');
          const answer = view.querySelector('.assistant');
          view.scrollTop = answer.offsetTop - 100;
          const measure = () => ({ height:ask.offsetHeight, width:ask.offsetWidth,
            answerTop:answer.getBoundingClientRect().top, scrollHeight:view.scrollHeight,
            display:getComputedStyle(ask).display });
          const before = measure();
          ask.classList.add('is-dynamic-pinned-in-sticky');
          const during = measure();
          const visible = getComputedStyle(ask).visibility;
          ask.classList.remove('is-dynamic-pinned-in-sticky');
          const after = measure();
          ask.classList.add('is-pinned-in-sticky');
          return { before, during, after, visible, originalVisible:getComputedStyle(ask).visibility,
            originalDisplay:getComputedStyle(ask).display };
        });
        assert.deepEqual(result.during, result.before, `mirrored image ask reflowed (${width}, ${engine})`);
        assert.deepEqual(result.after, result.before);
        assert.equal(result.visible, 'visible');
        assert.equal(result.originalVisible, 'visible');
        assert.equal(result.originalDisplay, result.before.display);
      }
    }
  } finally { await page.close(); }
});

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

// Exercise history paging's real detach/render/reattach path and the renderer
// scroll decision together. The isolated gesture tests above keep the DOM in
// place, which misses the momentary empty viewport during a history response.
const prependStart = app.indexOf('  function _prependConversationEvents(');
const prepend = app.slice(prependStart, app.indexOf('  // ── Spawn stats', prependStart));
const renderStart = app.indexOf('  function renderConversationEvents(');
const bottomStart = app.indexOf('    const wasAtBottom =', renderStart);
const renderBottom = app.slice(bottomStart, app.indexOf('\n', bottomStart));
const decisionStart = app.indexOf('    const _newCompactCard =', renderStart);
const renderScroll = app.slice(decisionStart, app.indexOf('    // Prepare after painting', decisionStart));

for (const scenario of ['short page', 'long page', 'saved reading position', 'compact boundary']) {
  test(`history ${scenario} preserves the visible message and keeps bottom-follow off`, async () => {
    const page = await fixture();
    try {
      const result = await page.evaluate(async ({ prepend, renderBottom, renderScroll, scenario }) => {
        window.getConvViewForPane = () => view;
        window._currentToolGroup = null;
        window._currentToolCount = 0;
        window._firstUserMsgRendered = true;
        window._dynamicAskState = null;
        window.currentConversation = 'test';
        window._convScrollByConv = scenario === 'saved reading position'
          ? { test: { top: 900, atBottom: false } } : {};
        window.renderConversationEvents = new Function('events', 'paneId', 'opts',
          'const $view = view;\n' + renderBottom + '\n'
          + 'for (const ev of events) {\n'
          + '  const row = document.createElement("div");\n'
          + '  row.className = "event" + (ev.compact ? " compact-resume-event" : "");\n'
          + '  row.textContent = "Earlier message " + ev.line;\n'
          + '  row.dataset.jsonlLine = ev.line;\n'
          + '  $view.appendChild(row);\n'
          + '}\n' + renderScroll + '\nreturn true;');
        (0, eval)(prepend);
        view.scrollTop = 600;
        view.dispatchEvent(new Event('scroll'));
        const anchor = _topVisibleAnchor(view);
        const events = Array.from({ length: scenario === 'long page' ? 12 : 1 }, (_, i) => ({
          line: i + 1,
          compact: scenario === 'compact boundary',
          type: 'user_text',
          text: scenario === 'compact boundary'
            ? 'This session is being continued from a previous conversation that ran out of context' : 'Earlier text',
        }));
        _prependConversationEvents(events, 'p1');
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const row = view.querySelector(`[data-jsonl-line="${anchor.line}"]`);
        const afterHistory = row.offsetTop - view.scrollTop;
        // A later poll must not undo the position preserved by the prepend.
        view.lastElementChild.append(' polled update');
        await new Promise(resolve => requestAnimationFrame(resolve));
        return { before: anchor.delta, afterHistory, afterPoll: row.offsetTop - view.scrollTop,
          pinned: view._pinnedToBottom };
      }, { prepend, renderBottom, renderScroll, scenario });
      assert.equal(result.afterHistory, result.before, 'history response moved the visible message');
      assert.equal(result.afterPoll, result.before, 'next poll moved the visible message');
      assert.equal(result.pinned, false, 'history re-enabled bottom-follow');
      if (scenario === 'long page' && process.env.CCC_HISTORY_SCREENSHOT) {
        await page.screenshot({ path: process.env.CCC_HISTORY_SCREENSHOT });
      }
    } finally { await page.close(); }
  });
}
