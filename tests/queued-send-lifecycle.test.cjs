// Executes the dashboard's send lifecycle against a real DOM without a server.
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
const lifecycle = [
  between('  function syncPendingSendsMapForConv(', '  function removePendingSendEcho('),
  between('  function markPendingSendQueued(', '  // State 2 of the echo lifecycle:'),
  between('  function removePendingSendEcho(', '  // A send the server *queued*'),
  between('  function restorePendingSendEchoes(', '  function restoreInputAfterSendFailure('),
].join('\n');
const reconciliation = between(
  '        const normed = _normSend(ev.text);\n        let _reconciledExact',
  '        // Webui panes: collapse a durable user bubble');
let browser;
before(async () => { browser = await puppeteer.launch({ headless: true }); });
after(async () => { if (browser) await browser.close(); });

async function fixture(run, input) {
  const page = await browser.newPage();
  try {
    await page.setRequestInterception(true);
    page.on('request', request => request.abort());
    await page.setContent('<div class="conv-pane" data-pane-id="main"><div class="conversations-view"></div><div class="conv-input-bar"></div></div>');
    await page.evaluate(({ lifecycle, reconciliation }) => {
      window.currentConversation = 'conversation';
      window._pendingSends = [];
      window._PENDING_SEND_ECHO_MAX_MS = 300000;
      window.sessionIdByConv = { conversation: 'session' };
      window.fixturePane = { conversationId: 'conversation', pendingSendsByConv: {} };
      window.paneByPaneId = () => fixturePane;
      window.activePaneId = () => 'main';
      window.getConvViewForPane = () => document.querySelector('.conversations-view');
      window.getConvView = getConvViewForPane;
      window._normSend = text => String(text || '').trim();
      window.escapeHtml = text => String(text || '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
      window.escapeAttr = escapeHtml;
      window.userMessageSteerHtml = () => '';
      window.showOptimisticAgentIndicator = () => {};
      window.clearOptimisticAgentIndicator = () => {};
      window.scrollConversationToEnd = () => {};
      window.markSessionSending = () => {};
      window.clearedSends = 0;
      window.clearSessionSending = () => { clearedSends++; };
      window.markPendingSendDelivered = pending => { pending.entry.delivered = true; };
      window.traySyncs = 0;
      window.syncQueuedSteerTray = view => {
        traySyncs++;
        const pane = view.closest('.conv-pane');
        let tray = pane.querySelector('.queued-steer-tray');
        if (!tray) {
          tray = document.createElement('div');
          tray.className = 'queued-steer-tray';
          tray.dataset.conversationId = currentConversation;
          pane.append(tray);
        }
        view.querySelectorAll('.send-queued').forEach(row => tray.append(row));
      };
      window.addQueueCard = (text, conversationId = currentConversation) => {
        let tray = document.querySelector('.queued-steer-tray');
        if (!tray) {
          tray = document.createElement('div');
          tray.className = 'queued-steer-tray';
          document.querySelector('.conv-pane').append(tray);
        }
        tray.dataset.conversationId = conversationId;
        const row = document.createElement('div');
        row.className = 'event user_text pending server-queued';
        row.dataset.queuedSteerServer = 'true';
        row.innerHTML = '<div class="user-msg"></div>';
        row.firstChild.textContent = text;
        tray.append(row);
        return row;
      };
      (0, eval)(lifecycle);
      window.reconcileUserEvent = new Function('ev', 'paneId', '$view', reconciliation);
    }, { lifecycle, reconciliation });
    return await page.evaluate(run, input);
  } finally {
    await page.close();
  }
}

test('restoring a saved send recognizes its current conversation tray card', async () => {
  const result = await fixture(() => {
    fixturePane.pendingSendsByConv.conversation = [{ text: 'Queued fixture', ts: Date.now() }];
    addQueueCard('Queued fixture');
    restorePendingSendEchoes('conversation', 'main');
    return { transcript: getConvView().querySelectorAll('.event').length, tray: document.querySelectorAll('.queued-steer-tray .event').length };
  });
  assert.deepEqual(result, { transcript: 0, tray: 1 });
});

test('restoration consumes existing occurrences only once for repeated genuine sends', async () => {
  const result = await fixture(() => {
    fixturePane.pendingSendsByConv.conversation = [1, 2].map(() => ({ text: 'Again', ts: Date.now() }));
    restorePendingSendEchoes('conversation', 'main');
    return getConvView().querySelectorAll('.event.user_text').length;
  });
  assert.equal(result, 2);
});

test('a tray belonging to another conversation cannot satisfy restoration', async () => {
  const result = await fixture(() => {
    fixturePane.pendingSendsByConv.conversation = [{ text: 'Queued fixture', ts: Date.now() }];
    addQueueCard('Queued fixture', 'other-conversation');
    restorePendingSendEchoes('conversation', 'main');
    return getConvView().querySelectorAll('.event.user_text').length;
  });
  assert.equal(result, 1);
});

test('queued acknowledgement persists the state and immediately relocates its echo', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Queue me', 'session', 'main');
    markPendingSendQueued(pending, 'Waiting for this turn');
    return {
      queued: fixturePane.pendingSendsByConv.conversation[0].queued,
      label: fixturePane.pendingSendsByConv.conversation[0].queuedLabel,
      inTray: !!pending.element.closest('.queued-steer-tray'),
      tracked: _pendingSends.includes(pending.entry),
      timer: pending.entry.timer,
    };
  });
  assert.deepEqual(result, { queued: true, label: 'Waiting for this turn', inTray: true, tracked: true, timer: null });
});

test('an old acknowledged queue survives restoration in the queued state', async () => {
  const result = await fixture(() => {
    fixturePane.pendingSendsByConv.conversation = [{ text: 'Long turn', ts: Date.now() - 600000, queued: true, queuedLabel: 'Waiting for this turn' }];
    restorePendingSendEchoes('conversation', 'main');
    return { cards: document.querySelectorAll('.queued-steer-tray .send-queued').length, tracked: _pendingSends.length };
  });
  assert.deepEqual(result, { cards: 1, tracked: 1 });
});

test('a queued ACK retains its originating pane bookkeeping after focus changes', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Original pane', 'session', 'main');
    window._pendingSends = [{ text: 'Other pane', ts: Date.now() }];
    markPendingSendQueued(pending, 'Still queued', { sync: false });
    return { saved: fixturePane.pendingSendsByConv.conversation.map(row => row.text), syncs: traySyncs };
  });
  assert.deepEqual(result, { saved: ['Original pane'], syncs: 0 });
});

test('one durable event acknowledges only one of two repeated queued sends', async () => {
  const result = await fixture(() => {
    const first = appendPendingSendEcho('Again', 'session', 'main');
    const second = appendPendingSendEcho('Again', 'session', 'main');
    markPendingSendQueued(first, '', { sync: false });
    markPendingSendQueued(second, '', { sync: false });
    reconcileUserEvent({ text: 'Again' }, 'main', getConvView());
    return { first: first.element.isConnected, second: second.element.isConnected, tracked: _pendingSends.length };
  });
  assert.deepEqual(result, { first: false, second: true, tracked: 1 });
});

test('identical earlier history does not swallow a new queued send on restore', async () => {
  const result = await fixture(() => {
    const row = document.createElement('div');
    row.className = 'event user_text';
    row.dataset.tsEpoch = String(Date.now() - 60000);
    row.innerHTML = '<div class="user-msg">Again</div>';
    getConvView().append(row);
    fixturePane.pendingSendsByConv.conversation = [{ text: 'Again', ts: Date.now(), queued: true }];
    restorePendingSendEchoes('conversation', 'main');
    return { history: getConvView().querySelectorAll('.event.user_text').length, queued: document.querySelectorAll('.queued-steer-tray .event.user_text').length };
  });
  assert.deepEqual(result, { history: 1, queued: 1 });
});

for (const incomingText of ['Steering fixture', 'Different queued fixture']) {
  test('synthetic pending event never acknowledges an optimistic send: ' + incomingText, async () => {
    const result = await fixture(incomingText => {
      const pending = appendPendingSendEcho('Steering fixture', 'session', 'main');
      pending.element.classList.add('steering-optimistic');
      reconcileUserEvent({ text: incomingText, pending: true }, 'main', getConvView());
      return { connected: pending.element.isConnected, tracked: _pendingSends.length, cleared: clearedSends };
    }, incomingText);
    assert.deepEqual(result, { connected: true, tracked: 1, cleared: 0 });
  });
}

for (const incomingText of ['Again', 'Older different message']) {
  test('older history cannot acknowledge a later queued send: ' + incomingText, async () => {
    const result = await fixture(incomingText => {
      const pending = appendPendingSendEcho('Again', 'session', 'main');
      markPendingSendQueued(pending, '', { sync: false });
      reconcileUserEvent({ text: incomingText, ts: new Date(pending.entry.ts - 60000).toISOString() }, 'main', getConvView());
      const beforeDelivery = { connected: pending.element.isConnected, tracked: _pendingSends.length };
      reconcileUserEvent({ text: 'Again', ts: new Date(pending.entry.ts + 1000).toISOString() }, 'main', getConvView());
      return { beforeDelivery, afterDelivery: { connected: pending.element.isConnected, tracked: _pendingSends.length } };
    }, incomingText);
    assert.deepEqual(result, { beforeDelivery: { connected: true, tracked: 1 }, afterDelivery: { connected: false, tracked: 0 } });
  });
}

test('a late original queue ACK preserves the optimistic steering presentation', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Steering fixture', 'session', 'main');
    markPendingSendQueued(pending, 'Original label', { sync: false });
    pending.element.classList.remove('send-queued');
    pending.element.classList.add('steering-optimistic');
    const note = pending.element.querySelector('.send-queued-note');
    note.hidden = true;
    const priorHtml = note.innerHTML;
    markPendingSendQueued(pending, 'Late ACK');
    return {
      queuedClass: pending.element.classList.contains('send-queued'),
      steeringClass: pending.element.classList.contains('steering-optimistic'),
      hidden: note.hidden,
      unchangedNote: priorHtml === note.innerHTML,
      syncs: traySyncs,
      tracked: _pendingSends.length,
    };
  });
  assert.deepEqual(result, { queuedClass: false, steeringClass: true, hidden: true, unchangedNote: true, syncs: 0, tracked: 1 });
});


test('a restored server queue card remains cancellable without resurrecting saved echoes', async () => {
  const result = await fixture(() => {
    fixturePane.pendingSendsByConv.conversation = [{ text: 'Cancel restored queue', ts: Date.now(), queued: true }];
    const row = addQueueCard('Cancel restored queue');
    restorePendingSendEchoes('conversation', 'main');
    const tracked = !!row._pendingRef;
    if (tracked) removePendingSendEcho(row._pendingRef);
    else row.remove();
    restorePendingSendEchoes('conversation', 'main');
    return { tracked, cards: document.querySelectorAll('.event.user_text').length };
  });
  assert.deepEqual(result, { tracked: true, cards: 0 });
});
