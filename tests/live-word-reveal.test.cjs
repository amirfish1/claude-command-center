const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const shared = app.slice(app.indexOf('  function _replayRunId('),
  app.indexOf('  function _gcReplayReceiptsHtml('));
const live = app.slice(app.indexOf('  function _convShouldLiveRevealNewText('),
  app.indexOf('  function startConvReplay('));
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');

test('live prose catches up promptly without hiding already received answers', async t => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.setContent(`<style>${css}</style><body><div class="conversations-view" style="height:400px;overflow:auto"></div></body>`);
  await page.evaluate(code => { (0, eval)(`
    let _convReplayActive = false;
    const $conversationsView = document.querySelector('.conversations-view');
    const getConvViewForPane = () => $conversationsView;
    window.tailDistance = 0; window.follows = 0; window.affordances = 0;
    const conversationDistanceFromBottom = () => window.tailDistance;
    const scrollConversationToEnd = view => { window.follows++; view.scrollTop = view.scrollHeight; };
    const updateConversationEndAffordance = () => window.affordances++;
    ${code}
    window.reveal = (html, opts) => {
      $conversationsView.innerHTML = '<div class="event assistant"><div class="assistant-text">' + html + '</div></div>';
      _convLiveRevealNewText($conversationsView.firstElementChild, 'p1', opts);
    };
    window.setReplay = value => { _convReplayActive = value; };
  `); }, shared + live);

  await t.test('a 325 word response has a bounded reveal delay', async () => {
    await page.evaluate(() => {
      window.started = performance.now();
      reveal('<p>' + Array(325).fill('received').join(' ') + '</p>');
    });
    await page.waitForFunction(() => !document.querySelector('.conv-live-word-reveal'), { timeout: 15000 });
    const elapsed = await page.evaluate(() => performance.now() - window.started);
    console.log('325-word reveal ms:', Math.round(elapsed));
    assert.ok(elapsed < 1000, `already received response took ${elapsed}ms to reveal`);
    if (process.env.LIVE_REVEAL_SCREENSHOT) {
      await page.screenshot({ path: process.env.LIVE_REVEAL_SCREENSHOT });
    }
  });

  await t.test('long markdown catches up after a delayed frame and preserves reading position', async () => {
    const expected = await page.evaluate(() => {
      window.tailDistance = 100;
      window.follows = 0;
      const html = '<p>' + Array(2000).fill('available').join(' ') + '</p><ul><li>first <code>inline ref</code> last</li></ul><pre>whole code block</pre><table><tbody><tr><td>cell</td></tr></tbody></table>';
      const plain = new DOMParser().parseFromString(html, 'text/html').body.textContent;
      window.started = performance.now();
      reveal(html);
      const until = performance.now() + 700;
      while (performance.now() < until) {} // foreground tab missed its animation deadline
      return plain;
    });
    await page.waitForFunction(() => !document.querySelector('.conv-live-word-reveal'), { timeout: 1500 });
    const result = await page.evaluate(() => ({
      elapsed: performance.now() - window.started,
      text: document.querySelector('.assistant-text').textContent,
      hidden: document.querySelectorAll('.assistant-text [style*="display: none"]').length,
      follows: window.follows, affordances: window.affordances,
      shimmer: document.querySelectorAll('.gc-typing-shimmer').length,
    }));
    // The shared replay wrapper normalizes whitespace at inline boundaries.
    assert.equal(result.text.replace(/\s/g, ''), expected.replace(/\s/g, ''));
    assert.equal(result.hidden, 0);
    assert.equal(result.follows, 0);
    assert.ok(result.affordances > 0);
    assert.equal(result.shimmer, 0);
    assert.ok(result.elapsed < 1200, `missed frame created a backlog: ${result.elapsed}ms`);
  });

  await t.test('manual scrolling releases follow during the same animation', async () => {
    await page.evaluate(() => {
      window.tailDistance = 0; window.follows = 0; window.affordances = 0;
      reveal('<p>' + Array(100).fill('received').join(' ') + '</p>');
      window.followsAtScroll = window.follows;
      window.tailDistance = 10;
    });
    await page.waitForFunction(() => !document.querySelector('.conv-live-word-reveal'));
    assert.deepEqual(await page.evaluate(() => ({
      followedInitially: window.followsAtScroll > 0,
      stopped: window.follows === window.followsAtScroll,
      affordance: window.affordances > 0,
    })), { followedInitially: true, stopped: true, affordance: true });
  });

  await t.test('history, replay and reduced motion appear whole immediately', async () => {
    for (const opts of [{ initialLoad: true }, { prepending: true }]) {
      await page.evaluate(opts => reveal('<p>Existing history</p>', opts), opts);
      assert.equal(await page.$('.conv-live-word'), null);
    }
    await page.evaluate(() => { setReplay(true); reveal('<p>Replay owns its pacing</p>'); setReplay(false); });
    assert.equal(await page.$('.conv-live-word'), null);
    await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
    await page.evaluate(() => reveal('<p>Read immediately</p>'));
    assert.equal(await page.$('.conv-live-word'), null);
    await page.emulateMediaFeatures([]);
  });

  await t.test('detached messages stop scheduling display work', async () => {
    await page.evaluate(() => {
      reveal('<p>' + Array(325).fill('received').join(' ') + '</p>');
      document.querySelector('.event').remove();
      window.follows = 0; window.affordances = 0;
    });
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.deepEqual(await page.evaluate(() => [window.follows, window.affordances]), [0, 0]);
  });
});
