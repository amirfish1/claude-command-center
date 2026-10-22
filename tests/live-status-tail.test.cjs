const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');
function fn(name) {
  const start = app.indexOf('  function ' + name + '(');
  assert.ok(start >= 0, name);
  return app.slice(start, app.indexOf('\n  }', start) + 4);
}
test('status follows new messages and streaming updates without repeated DOM rebuilds', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<style>${css}</style><div class="conversations-view" style="height:400px;overflow:auto"><div class="event assistant">First message</div></div>`);
    await page.evaluate(code => {
      window.getConvView = () => document.querySelector('.conversations-view');
      window.liveStatusMatchesOpenConv = () => true;
      window.liveStatus = { codexState: 'working', codexFresh: true, workingSince: 1 };
      window.escapeHtml = window.escapeAttr = s => String(s);
      window._optimisticAgeLabel = () => '3s';
      (0, eval)(code + '\nwindow.updateBadge = updateCodexStateBadge; window.anchorLive = _reanchorToTailUnlessOnlyTransientBetween;');
    }, fn('anchorCodexStateBadge') + fn('_reanchorToTailUnlessOnlyTransientBetween') + fn('updateCodexStateBadge'));
    const result = await page.evaluate(() => {
      const view = getConvView();
      updateBadge();
      const badge = view.querySelector('.conv-codex-state');
      const label = badge.querySelector('.ccs-label');
      const observer = new MutationObserver(() => {});
      observer.observe(view, { subtree: true, childList: true });
      for (let i = 0; i < 20; i++) updateBadge();
      const repeatedMutations = observer.takeRecords().length;
      const sameLabel = label === badge.querySelector('.ccs-label');
      const next = document.createElement('div');
      next.className = 'event assistant'; next.textContent = 'Latest message';
      view.appendChild(next);
      updateBadge();
      const messageBeforeStatus = next.nextElementSibling === badge;
      const stream = document.createElement('div');
      stream.className = 'stream-bubble'; stream.textContent = 'Streaming message';
      view.appendChild(stream);
      anchorLive(view, stream);
      observer.takeRecords();
      for (let i = 0; i < 20; i++) { updateBadge(); anchorLive(view, stream); }
      const stableStreamOrder = observer.takeRecords().length === 0;
      const bottom = view.scrollHeight;
      const statusBelowMessage = badge.getBoundingClientRect().top >= stream.getBoundingClientRect().bottom;
      liveStatus.codexState = 'idle'; updateBadge();
      const idleLabel = badge.querySelector('.ccs-label').textContent;
      const duplicate = badge.cloneNode(true);
      view.appendChild(duplicate);
      updateBadge();
      const singleStatus = view.querySelectorAll('.conv-codex-state').length === 1;
      const statusLast = view.lastElementChild === badge;
      observer.disconnect();
      return { repeatedMutations, sameLabel, messageBeforeStatus, stableStreamOrder, statusBelowMessage, statusLast, singleStatus, idleLabel,
        position: getComputedStyle(badge).position, scrollHeightUnchanged: view.scrollHeight === bottom,
        labelAnimation: getComputedStyle(badge).animationName };
    });
    assert.deepEqual(result, { repeatedMutations: 0, sameLabel: true, messageBeforeStatus: true,
      stableStreamOrder: true, statusBelowMessage: true, statusLast: true, singleStatus: true, idleLabel: 'Idle',
      position: 'relative', scrollHeightUnchanged: true, labelAnimation: 'none' });
  } finally { await browser.close(); }
});
