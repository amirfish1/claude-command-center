// Native Codex panes skip the legacy transcript fetch, so queued messages
// reach the steer tray through the dedicated queue read instead.
const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function between(start, end) {
  const first = source.indexOf(start);
  assert.ok(first >= 0, start);
  const last = source.indexOf(end, first);
  assert.ok(last > first, end);
  return source.slice(first, last);
}
const code = [
  between('  const _nativeCodexQueuedSync = new Map();', '  // ── Conversation presentation modes'),
  between('  function markPendingSendQueued(', '  // State 2 of the echo lifecycle:'),
].join('\n');

let browser;
before(async () => { browser = await puppeteer.launch({ headless: true }); });
after(async () => { if (browser) await browser.close(); });

async function fixture(run) {
  const page = await browser.newPage();
  try {
    await page.setContent('<div class="conv-pane" data-pane-id="main"><div class="conversations-view"><div class="codex-client-root"></div></div><div class="conv-input-bar"></div></div>');
    await page.evaluate((code) => {
      window.fixturePane = { conversationId: 'conversation' };
      window.sessionIdByConv = { conversation: 'session' };
      window.paneByPaneId = () => fixturePane;
      window.activePaneId = () => 'main';
      window.convRowForPane = () => null;
      window.convPaneElById = () => document.querySelector('.conv-pane');
      window.getConvViewForPane = () => document.querySelector('.conversations-view');
      window._normSend = text => String(text || '').trim();
      window.escapeHtml = text => String(text || '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
      window.escapeAttr = escapeHtml;
      window.queue = [];
      window.fetchUrls = [];
      window.fetch = async (url) => {
        fetchUrls.push(url);
        return { ok: true, json: async () => ({ ok: true, events: queue.map(text => ({ type: 'user_text', text, pending: true, ts: '2026-09-12T10:00:00Z' })) }) };
      };
      // Minimal tray: replaces server cards, moves fresh ones out of the view.
      window.syncQueuedSteerTray = (view, pid, replace) => {
        const pane = view.closest('.conv-pane');
        let tray = pane.querySelector('.queued-steer-tray');
        if (replace && tray) tray.querySelectorAll('[data-queued-steer-server="true"]').forEach(el => el.remove());
        const rows = view.querySelectorAll('[data-queued-steer-server="true"]');
        if (!rows.length && !(tray && tray.children.length)) { if (tray) tray.remove(); return; }
        if (!tray) { tray = document.createElement('div'); tray.className = 'queued-steer-tray'; pane.append(tray); }
        rows.forEach(row => tray.append(row));
        if (!tray.children.length) tray.remove();
      };
      window.removedEchoes = [];
      window.removePendingSendEcho = pending => { removedEchoes.push(pending.nativeMessageId); };
      (0, eval)(code);
    }, code);
    return await page.evaluate(run);
  } finally {
    await page.close();
  }
}

test('queued messages for a native Codex pane appear in the tray, not the transcript', async () => {
  const result = await fixture(async () => {
    queue = ['first', 'second'];
    await syncNativeCodexQueuedInputs('main');
    return {
      url: fetchUrls[0],
      tray: Array.from(document.querySelectorAll('.queued-steer-tray .user-msg')).map(el => el.textContent),
      inView: document.querySelectorAll('.conversations-view [data-queued-steer-server]').length,
    };
  });
  assert.deepEqual(result, { url: '/api/session/session/queued-inputs', tray: ['first', 'second'], inView: 0 });
});

test('an unchanged queue keeps the same card nodes across polls', async () => {
  const same = await fixture(async () => {
    queue = ['stay'];
    await syncNativeCodexQueuedInputs('main');
    const card = document.querySelector('.queued-steer-tray .event');
    await syncNativeCodexQueuedInputs('main');
    return card === document.querySelector('.queued-steer-tray .event');
  });
  assert.equal(same, true);
});

test('a drained queue removes the tray', async () => {
  const tray = await fixture(async () => {
    queue = ['gone soon'];
    await syncNativeCodexQueuedInputs('main');
    queue = [];
    await syncNativeCodexQueuedInputs('main');
    return !!document.querySelector('.queued-steer-tray');
  });
  assert.equal(tray, false);
});

test('a queued native echo hands off to the tray card once the queue lists it', async () => {
  const result = await fixture(async () => {
    queue = ['parked'];
    markPendingSendQueued({ text: 'parked', paneId: 'main', nativeMessageId: 'native-1' }, 'Queued');
    await new Promise(resolve => setTimeout(resolve, 20));
    return { removedEchoes, tray: Array.from(document.querySelectorAll('.queued-steer-tray .user-msg')).map(el => el.textContent) };
  });
  assert.deepEqual(result, { removedEchoes: ['native-1'], tray: ['parked'] });
});

test('a native echo the CCC queue does not list stays visible', async () => {
  const removed = await fixture(async () => {
    queue = [];
    markPendingSendQueued({ text: 'codex-owned', paneId: 'main', nativeMessageId: 'native-2' }, 'Queued for Codex');
    await new Promise(resolve => setTimeout(resolve, 20));
    return removedEchoes;
  });
  assert.deepEqual(removed, []);
});

test('an echo whose queue entry shows up a poll later still hands off', async () => {
  const removed = await fixture(async () => {
    queue = [];
    markPendingSendQueued({ text: 'late', paneId: 'main', nativeMessageId: 'native-3' }, 'Queued');
    await new Promise(resolve => setTimeout(resolve, 20));
    const early = removedEchoes.slice();
    queue = ['late'];
    await syncNativeCodexQueuedInputs('main');
    return { early, after: removedEchoes };
  });
  assert.deepEqual(removed, { early: [], after: ['native-3'] });
});

test('the inline Queued banner gives way to the tray card', async () => {
  const banners = await fixture(async () => {
    const banner = document.createElement('div');
    banner.className = 'conv-live-tool-inline is-wake-status is-queued';
    document.querySelector('.conversations-view').append(banner);
    queue = ['parked'];
    await syncNativeCodexQueuedInputs('main');
    return document.querySelectorAll('.is-wake-status').length;
  });
  assert.equal(banners, 0);
});
