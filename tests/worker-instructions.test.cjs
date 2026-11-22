const respondConversationAsset = require('./conversation-assets.cjs');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const marker = '\n})();\n\n/* === Hero: Fleet Pulse === */';
assert.ok(app.includes(marker));
const exposed = app.replace(marker, '\nwindow.workerPromptTest={renderConversationEvents,activePaneId,getConvViewForPane,paneByPaneId};' + marker);

test('worker instructions fold without losing text or collapsing ordinary and peer messages', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 850, hasTouch: true });
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = new URL(request.url());
      if (request.isNavigationRequest()) return request.respond({ status: 200, contentType: 'text/html',
        body: fs.readFileSync(require.resolve('../static/index.html'), 'utf8') });
      if (respondConversationAsset(request)) return;
      if (url.pathname === '/static/app.js') return request.respond({ status: 200, contentType: 'application/javascript', body: exposed });
      if (url.pathname === '/static/app.css') return request.respond({ status: 200, contentType: 'text/css',
        body: fs.readFileSync(require.resolve('../static/app.css'), 'utf8') });
      return request.respond({ status: 200, contentType: 'application/json', body: '[]' });
    });
    await page.goto('http://ccc-worker.test/', { waitUntil: 'load' });
    await page.waitForFunction(() => !!window.workerPromptTest);
    for (const width of [1280, 390]) for (const codex of [false, true]) {
      await page.setViewport({ width, height: 850, hasTouch: true });
      await page.evaluate(() => window.cccMobileNav.show());
      await page.waitForFunction(() => !document.querySelector('.main').getAnimations().some(a => a.playState === 'running')); 
      const result = await page.evaluate(codex => {
        const h = workerPromptTest, paneId = h.activePaneId(), view = h.getConvViewForPane(paneId);
        const pane = view.closest('.conv-pane');
        pane.classList.toggle('is-webui-session', codex);
        pane.classList.toggle('is-codex-session', codex);
        h.paneByPaneId(paneId).firstUserMsgRendered = true;
        document.body.classList.add('status-pos-right');
        view.style.cssText = 'display:block;position:fixed;inset:60px 16px 100px;overflow:auto;z-index:9999;background:var(--bg)';
        view.replaceChildren();
        const prompt = 'Drain the EXAMPLE-TOOLS WatchTower queue and keep it empty. Your worker id is example-worker.\n\n'
          + 'Follow the runbook, preserve the complete instructions <example> & quoted text.\n'.repeat(150).trimEnd();
        const normal = 'Please review this detailed request.\n' + 'Keep all of this visible.\n'.repeat(80).trimEnd();
        const events = [
          { type: 'user_text', line: 990001, text: prompt },
          { type: 'user_text', line: 990002, text: normal },
          { type: 'user_text', line: 990003, text: prompt, peer: { from_session: 'other-session', from_name: 'Reviewer' } },
          { type: 'user_text', line: 990004, text: 'Drain the EXAMPLE-TOOLS WatchTower queue documentation needs updating.' },
          { type: 'user_text', line: 990005, text: 'Announced from: CCC silence check\n\n?' },
          { type: 'user_text', line: 990006, text: 'Announced from: CCC silence check\n\n? Is this expected?' },
        ];
        h.renderConversationEvents(events, paneId, { initialLoad: true });
        const row = view.querySelector('.worker-instructions-event');
        if (innerWidth > 600) row.classList.add('is-pinned-in-sticky');
        const details = row.querySelector('details'), body = details.querySelector('.worker-instructions-body');
        view.scrollTop = 0;
        return { cards: view.querySelectorAll('.worker-instructions-card').length, closed: !details.open,
          raw: row.querySelector('.user-msg').dataset.rawText === prompt, full: body.textContent === prompt,
          escaped: !body.querySelector('example'), height: row.getBoundingClientRect().height,
          ordinary: [...view.querySelectorAll('.user-msg')].some(el => el.dataset.rawText === normal && !el.querySelector('details')),
          silenceCheck: [...view.querySelectorAll('.peer-message .user-msg')].some(el =>
            el.dataset.rawText === '?' && el.querySelector('.peer-sender-name')?.textContent === 'CCC silence check'
            && !el.textContent.includes('Announced from:')),
          similarText: [...view.querySelectorAll('.user-msg')].some(el =>
            el.dataset.rawText.endsWith('? Is this expected?') && el.textContent.includes('Announced from:')),
          summary: details.querySelector('summary').textContent };
      }, codex);
      assert.deepEqual(result, { cards: 1, closed: true, raw: true, full: true, escaped: true,
        height: result.height, ordinary: true, silenceCheck: true, similarText: true,
        summary: 'EXAMPLE-TOOLS worker instructions' });
      assert.ok(result.height <= 120, JSON.stringify({width, codex, result}));
      const summary = '.worker-instructions-card > summary';
      await page.focus(summary);
      await page.keyboard.press('Enter');
      const expanded = await page.$eval('.worker-instructions-body', el => ({
        visible: el.checkVisibility(), height: el.getBoundingClientRect().height, scrolls: el.scrollHeight > el.clientHeight,
        open:el.parentElement.open, focused:document.activeElement.outerHTML.slice(0,200),
        overflow: el.scrollWidth > el.clientWidth,
      }));
      assert.ok(expanded.visible && expanded.height <= 430 && expanded.scrolls && !expanded.overflow, JSON.stringify({width,codex,expanded}));
      await page.keyboard.press('Space');
      assert.equal(await page.$eval('.worker-instructions-card', el => el.open), false);
      await page.tap(summary);
      assert.equal(await page.$eval('.worker-instructions-card', el => el.open), true);
      await page.$eval('.worker-instructions-body', el => { el.scrollTop = el.scrollHeight; });
      assert.ok(await page.$eval('.worker-instructions-body', el => el.scrollTop > 0));
      // A repeated poll must retain the native disclosure and its scroll position.
      const retained = await page.evaluate(() => {
        const h = workerPromptTest, card = document.querySelector('.worker-instructions-card');
        const body = card.querySelector('.worker-instructions-body'), scroll = body.scrollTop;
        h.renderConversationEvents([{ type: 'user_text', line: 990001, text: card.closest('.user-msg').dataset.rawText }], h.activePaneId(), { initialLoad: true });
        return card.isConnected && card.open && body.scrollTop === scroll;
      });
      assert.equal(retained, true);
    }
  } finally {
    await browser.close();
  }
});
