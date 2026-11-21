const respondConversationAsset = require('./conversation-assets.cjs');
// Opt-in behavior check: CCC_FROZEN_ASK_TEST_URL=http://127.0.0.1:8091 node tests/frozen-ask.browser.cjs
// API responses are disposable; no session messages or preferences are sent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const base = process.env.CCC_FROZEN_ASK_TEST_URL;
if (!base) throw new Error('Set CCC_FROZEN_ASK_TEST_URL to a running dashboard');
const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const anchor = '  // Shared conversation-core.js: positionConversationEndAffordance.';
const rowGetter = 'get convRowForPane() { return convRowForPane; },';
const fixture = `
  window.__frozenAskFill = (withUser) => {
    const view = getConvView();
    const reply = '<div class="event assistant"><div class="assistant-text">' +
      Array.from({length:120}, (_, i) => '<p>Reply paragraph ' + i + ' with enough words to take a full line.</p>').join('') + '</div></div>';
    view.innerHTML = withUser
      ? ['First question', 'Second question', 'Third question ' + Array.from({length:60}, (_, i) => 'long detail ' + i + ';').join(' ') + '\\nmore\\nlines\\nhere'].map(q =>
          '<div class="event user_text"><div class="user-msg" data-raw-text="' + q + '">' + q + '</div></div>' + reply).join('')
      : reply + reply;
    view.scrollTop = view.scrollHeight;
    view._pinnedToBottom = true;
  };
  window.__frozenAskScroll = (to) => {
    const view = getConvView();
    view._pinnedToBottom = to === 'end';
    if (to === 'end') view.scrollTop = view.scrollHeight;
    else if (typeof to === 'number') view.scrollTop = to;
    else {
      const el = [...view.querySelectorAll('.event.user_text')].find(e => e.textContent.includes(to));
      view.scrollTop = el.offsetTop + el.offsetHeight + 200;
    }
  };
  window.__frozenAskRow = (row) => { window.__frozenAskRowFn = () => row; };
  window.__frozenAskState = () => {
    const view = getConvView();
    const bar = view._frozenAskBar || view.querySelector(':scope > .conv-frozen-ask');
    const bubble = bar && bar.firstElementChild;
    const visible = !!bar && !bar.hidden && bubble.getClientRects().length > 0;
    return {
      visible, text: visible ? bar.querySelector('.user-msg').textContent : '',
      scrollHeight: view.scrollHeight,
      height: visible ? Math.round(bubble.getBoundingClientRect().height) : null,
      offset: visible ? Math.round(bubble.getBoundingClientRect().top - view.getBoundingClientRect().top) : null,
      docked: !!bar && !!bar.parentNode && bar.parentNode !== view,
      bubbleBottom: visible ? Math.round(bubble.getBoundingClientRect().bottom) : null,
      bubbleRight: visible ? Math.round(bubble.getBoundingClientRect().right) : null,
      viewTop: Math.round(view.getBoundingClientRect().top),
    };
  };
  window.__frozenAskToolbar = (show) => {
    const toolbar = getConvView().closest('.conv-pane').querySelector('[data-role="presentation-toolbar"]');
    toolbar.classList.add('is-reader-only');
    toolbar.hidden = !show;
    const dock = toolbar.querySelector('.conv-reader-audio');
    if (dock) dock.hidden = !show;
    return { height: Math.round(toolbar.getBoundingClientRect().height), dockLeft: dock ? Math.round(dock.getBoundingClientRect().left) : null };
  };
`;
(async () => {
  assert.ok(source.includes(anchor), 'fixture anchor exists');
  assert.ok(source.includes(rowGetter), 'row getter anchor exists');
  const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.evaluateOnNewDocument(() => {
      if (window.self !== window.top || !/^https?:$/.test(location.protocol)) return;
      localStorage.setItem('ccc-tour-done', '1');
      localStorage.setItem('ccc-pwa-install-dismissed', String(Date.now()));
      localStorage.setItem('ccc-engines-first-run-done', '1');
      localStorage.setItem('ccc-ui-mode', 'advanced');
      if (/[?&]light\b/.test(location.search)) localStorage.setItem('ccc-theme', 'light');
    });
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (respondConversationAsset(request)) return;
      if (url.pathname === '/static/app.js') return request.respond({contentType:'text/javascript', body:source.replace(anchor, fixture + anchor).replace(rowGetter, 'get convRowForPane() { return window.__frozenAskRowFn || convRowForPane; },')});
      if (url.pathname.startsWith('/api/')) return request.respond({contentType:'application/json', body:JSON.stringify(url.pathname === '/api/sessions' ? [] : url.pathname === '/api/conversations/list' ? {conversations:[], total:0} : {})});
      return request.continue();
    });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const settle = () => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    const at = async (to) => { await page.evaluate(to => window.__frozenAskScroll(to), to); await settle(); return page.evaluate(() => window.__frozenAskState()); };
    for (const width of [390, 1440]) {
      await page.setViewport({width, height:900});
      await page.goto(base + (process.env.CCC_FROZEN_ASK_LIGHT ? '/?light' : '/'), {waitUntil:'domcontentloaded'});
      await page.waitForSelector('.conv-scroll-end-btn');
      await page.evaluate(() => window.__frozenAskFill(true));
      await settle();
      const bottom = await page.evaluate(() => window.__frozenAskState());
      assert.equal(bottom.visible, false, 'nothing is frozen while reading the end');

      const third = await at('Third question');
      assert.equal(third.visible, true, 'the ask freezes once the reader scrolls up');
      assert.ok(third.text.startsWith('Third question'), 'the ask being answered is the one shown');
      assert.ok(third.height <= 64, 'a long ask stays two lines tall, got ' + third.height);
      assert.ok(third.offset >= 0 && third.offset < 40, 'it sits at the top of the transcript');
      assert.equal(third.scrollHeight, bottom.scrollHeight, 'freezing never changes the scroll height');
      if (process.env.CCC_FROZEN_ASK_SHOTS) await page.screenshot({path:'/tmp/ccc-frozen-ask-' + width + '.png'});

      assert.equal((await at('Second question')).text, 'Second question', 'scrolling further up follows the earlier ask');
      assert.equal((await at(0)).visible, false, 'no ask is above the very top');
      assert.equal((await at('end')).visible, false, 'returning to the end releases it');

      await at('First question');
      await page.click('.conv-frozen-ask__bubble .user-msg');
      await settle();
      const jumped = await page.evaluate(() => {
        const view = document.querySelector('.conv-frozen-ask__bubble').closest('.conv-pane').querySelector('.conversations-view');
        const first = view.querySelector('.event.user_text');
        return Math.round(first.getBoundingClientRect().top - view.getBoundingClientRect().top);
      });
      assert.ok(Math.abs(jumped) < 60, 'clicking it jumps to the message');

      // With the read-aloud row showing, the ask moves into its empty left
      // side: the row keeps its height and nothing covers the transcript.
      const row = await page.evaluate(() => window.__frozenAskToolbar(true));
      const docked = await at('Third question');
      assert.equal(docked.visible, true, 'the ask still shows with the read-aloud row');
      assert.equal(docked.docked, true, 'it sits in the read-aloud row');
      assert.ok(docked.bubbleBottom <= docked.viewTop, 'it no longer covers transcript text');
      assert.ok(row.dockLeft != null && docked.bubbleRight <= row.dockLeft, 'it stays left of the speaker controls');
      assert.equal((await page.evaluate(() => window.__frozenAskToolbar(true))).height, row.height, 'the row does not grow');
      assert.equal(docked.scrollHeight, bottom.scrollHeight, 'docking never changes the scroll height');
      if (process.env.CCC_FROZEN_ASK_SHOTS) await page.screenshot({path:'/tmp/ccc-frozen-ask-docked-' + width + '.png'});
      assert.equal((await at('end')).visible, false, 'the docked ask is released at the end');
      await page.evaluate(() => window.__frozenAskToolbar(false));
      assert.equal((await at('Third question')).docked, false, 'it floats again once the row is gone');

      // A transcript with no user message falls back to the session's first message.
      await page.evaluate(() => window.__frozenAskFill(false));
      assert.equal((await at(300)).visible, false, 'no known first message shows nothing');
      await page.evaluate(() => window.__frozenAskRow({first_message:'Session opener'}));
      const opener = await at(400);
      assert.equal(opener.visible, true, 'a transcript without user messages still freezes an ask');
      assert.equal(opener.text, 'Session opener', 'it falls back to the first message of the session');
      assert.equal((await at('end')).visible, false, 'the fallback is released at the end too');
    }
    assert.deepEqual(errors, []);
    console.log('frozen ask: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
