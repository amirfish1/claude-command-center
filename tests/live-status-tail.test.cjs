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

test('working status keeps its Thinking row while a completed sidecar name lingers', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<style>${css}</style><div class="conversations-view"><div class="event assistant">Latest reply</div></div>`);
    await page.evaluate(code => {
      window.getConvView = () => document.querySelector('.conversations-view');
      window.liveStatusMatchesOpenConv = () => true;
      window.liveStatus = { live: true, codexState: 'working', sidecarStatus: 'idle', sidecarTs: Date.now() / 1000 };
      window.currentSession = { source: 'codex' };
      window.escapeHtml = window.escapeAttr = s => String(s);
      window._liveStripLastKeyTs = window._liveStripLastLiveTime = 0;
      window._liveStripShown = false;
      window._streamingBubble = null;
      window.codexActiveItemLabel = () => ({ label: '', detail: '' });
      window.codexTokenUsageText = () => '';
      window.updateLiveStripOffset = () => {};
      (0, eval)(code + '\nwindow.updateLive = updateLiveToolStrip;');
    }, fn('anchorCodexStateBadge') + fn('_reanchorToTailUnlessOnlyTransientBetween')
      + fn('getSingleLiveToolInline') + fn('_wireLiveInlineExpand') + fn('updateLiveToolStrip'));
    const result = await page.evaluate(() => {
      const view = getConvView();
      updateLive();
      const row = view.querySelector('.conv-live-tool-inline');
      const label = row.querySelector('.cl-tool');
      const height = view.scrollHeight;
      for (let i = 0; i < 10; i++) {
        liveStatus.sidecarTool = i % 2 ? 'Bash' : null;
        updateLive();
      }
      return { sameRow: row === view.querySelector('.conv-live-tool-inline'),
        sameLabel: label === row.querySelector('.cl-tool'),
        label: row.querySelector('.cl-tool').textContent,
        heightStable: height === view.scrollHeight };
    });
    assert.deepEqual(result, { sameRow: true, sameLabel: true, label: 'Thinking…', heightStable: true });
  } finally { await browser.close(); }
});

test('Codex in-flight tool card expands on click to the full command (CCC-227)', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<style>${css}</style><div class="conversations-view"><div class="event assistant">Latest reply</div></div>`);
    const full = "/bin/bash -lc 'node ../../node_modules/eslint/bin/eslint.js src/components/notifications/NotificationCenter.tsx --max-warnings 0'";
    await page.evaluate((code, full) => {
      window.getConvView = () => document.querySelector('.conversations-view');
      window.liveStatusMatchesOpenConv = () => true;
      window.liveStatus = { live: true, codexState: 'working', sidecarStatus: 'idle', sidecarTs: Date.now() / 1000 };
      window.currentSession = { source: 'codex' };
      window.escapeHtml = window.escapeAttr = s => String(s);
      window.truncate = (s, n) => (s.length > n ? s.slice(0, n) + '…' : s);
      window._liveStripLastKeyTs = window._liveStripLastLiveTime = 0;
      window._liveStripShown = false;
      window._streamingBubble = null;
      window.codexActiveItemLabel = () => ({ label: 'Bash', detail: full });
      window.codexTokenUsageText = () => '';
      window.updateLiveStripOffset = () => {};
      (0, eval)(code + '\nwindow.updateLive = updateLiveToolStrip;');
    }, fn('anchorCodexStateBadge') + fn('_reanchorToTailUnlessOnlyTransientBetween')
      + fn('getSingleLiveToolInline') + fn('_wireLiveInlineExpand') + fn('updateLiveToolStrip'), full);
    const result = await page.evaluate(() => {
      updateLive();
      const row = document.querySelector('.conv-live-tool-inline');
      const before = row.querySelector('.cl-file').textContent;
      row.querySelector('.cl-tool').click();
      const after = row.querySelector('.cl-file').textContent;
      updateLive();
      const afterPoll = row.querySelector('.cl-file').textContent;
      return { before, after, afterPoll, expanded: row.classList.contains('is-expanded') };
    });
    assert.ok(result.before.length < full.length);
    assert.equal(result.after, full);
    assert.equal(result.afterPoll, full);
    assert.equal(result.expanded, true);
  } finally {
    await browser.close();
  }
});

test('compaction clock and live tail updates keep the card stationary (CCC-423)', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<style>${css}</style><div class="conversations-view" style="height:500px;overflow:auto"><div class="event assistant">Investigating the reported issue.</div></div>`);
    const code = ['anchorCodexStateBadge', '_reanchorToTailUnlessOnlyTransientBetween',
      '_compactElapsedLabel', '_compactTokenLabel', '_compactStepIndexFor', '_compactRunStepsHtml',
      '_compactRunResultHtml', '_compactRunPaint', '_compactRunMount'].map(fn).join('\n');
    await page.evaluate(code => {
      window.getConvView = window._compactRunView = () => document.querySelector('.conversations-view');
      window._compactRunIsForeground = () => true;
      window.escapeHtml = s => String(s);
      window._COMPACT_TYPICAL_MS = 90000;
      window._COMPACT_STEPS = [{ label: 'Handing the transcript over' }, { label: 'Writing the summary' }, { label: 'Rebuilding the context window' }];
      window.clock = Date.now();
      Date.now = () => window.clock;
      window._compactRun = { sid: 'test-session', stage: 'working', engineLabel: 'Codex',
        startedAt: clock - 14000, before: { tokens: 182000 } };
      (0, eval)(code + '\nwindow.mount = _compactRunMount; window.paint = _compactRunPaint; window.anchor = _reanchorToTailUnlessOnlyTransientBetween;');
    }, code);
    const result = await page.evaluate(() => {
      const view = getConvView();
      mount(view); paint();
      const card = _compactRun.el;
      const spinner = card.querySelector('.compact-run-spinner');
      const progress = card.querySelector('.compact-run-bar-fill');
      const initialProgress = progress.style.width;
      const tool = document.createElement('div');
      tool.className = 'conv-live-tool-inline'; tool.textContent = 'contextCompaction';
      view.appendChild(tool);
      const observer = new MutationObserver(() => {});
      observer.observe(view, { childList: true });
      const top = card.getBoundingClientRect().top;
      let stationary = true;
      for (let i = 0; i < 20; i++) {
        clock += 1000;
        anchor(view, tool); mount(view); paint();
        stationary &&= card.getBoundingClientRect().top === top;
      }
      const stable = { stationary, moves: observer.takeRecords().length,
        sameSpinner: spinner === card.querySelector('.compact-run-spinner'),
        sameProgress: progress === card.querySelector('.compact-run-bar-fill'),
        progressAdvanced: initialProgress !== progress.style.width,
        clock: card.querySelector('.compact-run-clock').textContent };
      const next = document.createElement('div'); next.className = 'event'; next.textContent = 'New transcript content';
      view.appendChild(next); mount(view); anchor(view, tool);
      stable.followsNewContent = next.nextElementSibling === card;
      _compactRun.stage = 'done'; _compactRun.endedAt = clock; _compactRun.after = { tokens: 12000 };
      mount(view); paint();
      stable.completed = card.textContent.includes('Context compacted') && card.textContent.includes('170000 freed');
      const later = document.createElement('div'); later.className = 'event'; view.appendChild(later);
      mount(view);
      stable.finishedStaysPut = view.lastElementChild === later;
      observer.disconnect();
      return stable;
    });
    assert.deepEqual(result, { stationary: true, moves: 0, sameSpinner: true, sameProgress: true,
      progressAdvanced: true, clock: '0:34', followsNewContent: true, completed: true, finishedStaysPut: true });
    if (process.env.CCC_FLICKER_SCREENSHOT) await page.screenshot({ path: process.env.CCC_FLICKER_SCREENSHOT });
  } finally { await browser.close(); }
});
