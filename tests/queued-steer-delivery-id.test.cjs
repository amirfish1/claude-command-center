// The server names one queued message with one delivery_id. The tray pairs
// server cards to local echoes by that id first, so two sends with the same
// words stay two cards, and Cancel targets the one the user clicked.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const app = fs.readFileSync('static/app.js', 'utf8');
function between(start, end) {
  const first = app.indexOf(start);
  assert.ok(first >= 0, start);
  const last = app.indexOf(end, first);
  assert.ok(last > first, end);
  return app.slice(first, last);
}
const helpers = [
  between('  function queuedSteerSource(', '  function syncUserMessageSteerButtons('),
  between('  function syncPendingSendsMapForConv(', '  function appendPendingSendEcho('),
  between('  function queuedSteerCardCount(', '  // ── Conversation presentation modes'),
].join('\n');
const css = fs.readFileSync('static/app.css', 'utf8');
let browser;
test.before(async () => { browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']}); });
test.after(async () => { await browser?.close(); });

async function fixture() {
  const page = await browser.newPage();
  await page.setViewport({width: 1100, height: 800});
  await page.setContent('<body class="status-pos-right"><div class="conv-pane" data-pane-id="left" style="height:760px"><div class="conversations-view"></div><div class="conv-input-bar visible"><textarea></textarea></div></div></body>');
  await page.addStyleTag({content: css});
  await page.evaluate(helpers => {
    window.state = {conversationId: 'session-one'};
    window.paneByPaneId = () => state;
    window.getConvInputBarForPane = () => document.querySelector('.conv-input-bar');
    window.getConvViewForPane = window.getConvView = () => document.querySelector('.conversations-view');
    window.activePaneId = () => 'left';
    window.conversationsData = [{id: 'session-one', session_id: 'session-one', source: 'codex'}];
    window._normSend = t => String(t || '').replace(/\s+/g, ' ').trim();
    window.scrollConversationToEnd = () => {};
    window.removedPending = [];
    window.removePendingSendEcho = p => { removedPending.push(p); p.element.remove(); };
    window.markPendingSendQueued = (p) => { p.element.classList.remove('pending'); p.element.classList.add('send-queued'); p.entry.queued = true; };
    (0, eval)(helpers);
    window.sessionSupportsQueuedSteer = () => true;
    window.makeRow = (text, kind, id) => {
      const row = document.createElement('div');
      row.className = 'event user_text ' + (kind === 'server' ? 'pending server-queued' : 'pending');
      row.dataset.tsEpoch = String(Date.now());
      if (kind === 'server') { row.dataset.queuedSteerServer = 'true'; if (id) row.dataset.deliveryId = id; }
      const msg = document.createElement('div'); msg.className = 'user-msg'; msg.dataset.rawText = text; msg.textContent = text; row.append(msg);
      if (kind === 'local') {
        const entry = {text, sid: 'session-one', ts: Date.now()};
        row._pendingRef = {element: row, entry, sid: 'session-one', list: [entry], paneId: 'left', conversationId: 'session-one'};
        if (id) bindPendingSendDeliveryId(row._pendingRef, id);
      }
      getConvView().append(row);
      return row;
    };
    window.sync = () => syncQueuedSteerTray(getConvView(), 'left', false);
  }, helpers);
  return page;
}

test('identical texts with distinct delivery ids stay two cards and pair by id', async () => {
  const page = await fixture();
  try {
    const out = await page.evaluate(() => {
      const a = makeRow('same words', 'local', 'id-a');
      const b = makeRow('same words', 'local', 'id-b');
      // Server snapshot arrives in the opposite order: pairing must follow ids.
      makeRow('same words', 'server', 'id-b');
      makeRow('same words', 'server', 'id-a');
      sync();
      const tray = document.querySelector('.queued-steer-tray');
      return {
        cards: queuedSteerCardCount(tray),
        ids: [...tray.querySelectorAll('.event')].map(el => el.dataset.deliveryId).sort(),
        localKept: a.isConnected && b.isConnected && a.classList.contains('send-queued') && b.classList.contains('send-queued'),
        serverLeft: document.querySelectorAll('[data-queued-steer-server="true"]').length,
        removed: removedPending.length,
      };
    });
    assert.deepEqual(out, {cards: 2, ids: ['id-a', 'id-b'], localKept: true, serverLeft: 0, removed: 0});
  } finally { await page.close(); }
});

test('an echo without an id adopts the server card id by text; a known different id never pairs', async () => {
  const page = await fixture();
  try {
    const out = await page.evaluate(() => {
      const plain = makeRow('hello', 'local');
      const other = makeRow('hello', 'local', 'id-other');
      makeRow('hello', 'server', 'id-1');
      sync();
      return {
        plainId: queuedRowDeliveryId(plain),
        otherId: queuedRowDeliveryId(other),
        plainQueued: plain.classList.contains('send-queued'),
        otherQueued: other.classList.contains('send-queued'),
      };
    });
    assert.deepEqual(out, {plainId: 'id-1', otherId: 'id-other', plainQueued: true, otherQueued: false});
  } finally { await page.close(); }
});

test('repeated polls of the same server row collapse by id, not by text', async () => {
  const page = await fixture();
  try {
    const out = await page.evaluate(() => {
      makeRow('again', 'server', 'id-x');
      makeRow('again', 'server', 'id-x');
      makeRow('again', 'server', 'id-y');
      sync();
      const tray = document.querySelector('.queued-steer-tray');
      return [...tray.querySelectorAll('.event')].map(el => el.dataset.deliveryId).sort();
    });
    assert.deepEqual(out, ['id-x', 'id-y']);
  } finally { await page.close(); }
});

test('the saved echo record carries the delivery id for restore after a pane rebuild', async () => {
  const page = await fixture();
  try {
    const out = await page.evaluate(() => {
      const pane = {conversationId: 'conv-1'};
      const entry = {text: 'later', ts: 1, queued: true};
      const pending = {entry, element: document.createElement('div'), list: [entry], paneId: 'left', conversationId: 'conv-1'};
      window.paneByPaneId = () => pane;
      bindPendingSendDeliveryId(pending, 'id-saved');
      return pane.pendingSendsByConv['conv-1'][0];
    });
    assert.equal(out.deliveryId, 'id-saved');
    assert.equal(out.text, 'later');
  } finally { await page.close(); }
});
