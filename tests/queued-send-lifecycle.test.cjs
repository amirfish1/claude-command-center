// Executes the dashboard's send lifecycle against a real DOM without a server.
const assert = require('node:assert/strict');
const { before, after, test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const { source, functions } = require('./conversation-source.cjs');

function between(start, end) {
  const first = source.indexOf(start);
  assert.ok(first >= 0, start);
  const last = source.indexOf(end, first);
  assert.ok(last > first, end);
  return source.slice(first, last);
}
const lifecycle = functions(['queuedSteerSource', 'sessionSupportsQueuedSteer', 'queuedSteerBlockedReason', 'takeQueuedSendAcknowledgement', 'syncPendingSendsMapForConv', 'bindPendingSendDeliveryId', 'readyQueuedSteerRows', 'appendPendingSendEcho', 'markPendingSendQueued', 'markPendingSendDelivered', 'removePendingSendEcho', 'restorePendingSendEchoes']);
const reconciliation = between(
  '        const normed = state._normSend(ev.text);\n        let _reconciledExact',
  '        // Webui panes: collapse a durable user bubble');
const renderedUserReconciliation = between(
  '      if (state._pendingSends.length) {\n        const _durableUserRows',
  '\n    }\n    if (!opts.provisionalOverlay) {\n      // Echoes follow new transcript content');
const renderTail = between(
  '    // The app-server live overlay is agent-output-only.',
  '    // Re-evaluate the end-of-session outcome banner');
const steerHandler = functions(['installConversationInteraction37', 'installConversationInteraction39'])
  + '\ninstallConversationInteraction37(); installConversationInteraction39();';
let browser;
before(async () => { browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] }); });
after(async () => { if (browser) await browser.close(); });

test('steering echoes stay above Generating across transcript refreshes and status ticks', async () => {
  const names = ['anchorCodexStateBadge', '_reanchorToTailUnlessOnlyTransientBetween',
    'getSingleLiveToolInline', '_wireLiveInlineExpand', 'updateLiveToolStrip'];
  const code = names.map(name => {
    const start = source.indexOf('  function ' + name + '(');
    return source.slice(start, source.indexOf('\n  }', start) + 4);
  }).join('\n');
  const result = await fixture(code => {
    window.liveStatusMatchesOpenConv = () => true;
    window.liveStatus = { live: true, sidecarStatus: 'active', sidecarTs: Date.now() / 1000 };
    window.currentSession = { id: 'session', source: 'devin-cli' };
    window._liveStripLastKeyTs = window._liveStripLastLiveTime = 0;
    window._liveStripShown = false;
    window._compactRunFor = () => null;
    window._streamingBubble = null;
    window.codexActiveItemLabel = () => ({ label: '', detail: '' });
    window.codexTokenUsageText = () => '';
    window.updateLiveStripOffset = () => {};
    (0, eval)(code);
    const view = getConvView();
    const first = appendPendingSendEcho('First steering message', 'session', 'main');
    const second = appendPendingSendEcho('Second steering message', 'session', 'main');
    for (const pending of [first, second]) {
      pending.element.classList.add('steering-optimistic');
      pending.element.insertAdjacentHTML('beforeend', '<div>Steering in background…</div>');
    }
    updateLiveToolStrip();
    const status = view.querySelector('.conv-live-tool-inline');
    const expected = [first.element, second.element, status];
    const tops = expected.map(node => node.getBoundingClientRect().top);
    const stable = () => expected.every((node, i) => view.children[i] === node
      && node.getBoundingClientRect().top === tops[i]);
    const observer = new MutationObserver(() => {});
    observer.observe(view, { childList: true });
    let stationary = true;
    for (let i = 0; i < 20; i++) {
      runRenderTail(view, 'main', {}, []);
      stationary &&= stable();
      updateLiveToolStrip();
      stationary &&= stable();
    }
    const moves = observer.takeRecords().length;
    const latest = document.createElement('div');
    latest.className = 'event assistant'; latest.textContent = 'New agent output';
    view.append(latest);
    updateLiveToolStrip();
    runRenderTail(view, 'main', {}, [{ type: 'assistant' }]);
    const followsNewOutput = latest.nextElementSibling === first.element
      && first.element.nextElementSibling === second.element
      && second.element.nextElementSibling === status;
    return { stationary, moves, followsNewOutput, label: status.querySelector('.cl-tool').textContent };
  }, code, false, { width: 1518, height: 686 });
  assert.deepEqual(result, { stationary: true, moves: 0, followsNewOutput: true, label: 'Generating…' });
});

async function fixture(run, input, realTray = false, viewport) {
  const page = await browser.newPage();
  if (viewport) await page.setViewport(viewport);
  try {
    await page.setRequestInterception(true);
    page.on('request', request => request.abort());
    await page.setContent('<div class="conv-pane" data-pane-id="main"><div class="conversations-view"></div><div class="conv-input-bar"></div></div>');
    await require('./conversation-content-fixture.cjs').install(page);
    await page.evaluate(({ lifecycle, reconciliation, renderedUserReconciliation, renderTail, steerHandler }) => {
      window.state = window;
      window.currentConversation = 'conversation';
      window._pendingSends = [];
      window._ultraFresh = { entries: new Map() };
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
      window.setOptimisticAgentThinking = () => {};
      window.clearedGenerating = 0;
      window.clearLiveGeneratingIndicator = () => { clearedGenerating++; };
      window.scrollConversationToEnd = () => {};
      window.markSessionSending = () => {};
      window.clearedSends = 0;
      window.clearSessionSending = () => { clearedSends++; };
      window.currentSession = { id: 'session' };
      window._anchorWakeBreakdown = () => {};
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
      window.sharedState = window.hostServices = window.helpers = window.state = window;
      (0, eval)(lifecycle);
      const reconcileEvent = new Function('ev', 'paneId', '$view', 'div', reconciliation);
      window.reconcileUserEvent = (ev, paneId, view) => {
        const row = document.createElement('div');
        row.className = 'event user_text';
        if (ev.ts) row.dataset.tsEpoch = String(Date.parse(ev.ts));
        row.innerHTML = '<div class="user-msg"></div>';
        row.firstChild.textContent = ev.text;
        reconcileEvent(ev, paneId, view, row);
        return row;
      };
      window.reconcileRenderedUserRows = new Function('$view', 'paneId', renderedUserReconciliation);
      window.runRenderTail = new Function('$view', 'paneId', 'opts', 'events', renderTail);
      window.trayConversationView = getConvView;
      window.markSteerInFlight = window.clearSteerInFlight = () => {};
      window.clearQueuedSteerError = () => {};
      window.beginOptimisticSteerMove = rows => rows;
      window.revertOptimisticSteerMove = () => {};
      window.showOpToast = () => {};
      window.steeredToastText = () => 'Steered';
      window.subagentRelayToastText = () => '';
      window.setQueuedSteerError = () => { throw new Error('Unexpected steer error'); };
      window.refreshConversationList = () => {};
      window.formatInjectFailure = data => data.error;
      (0, eval)(steerHandler);
    }, { lifecycle, reconciliation, renderedUserReconciliation, renderTail, steerHandler });
    if (realTray) await page.evaluate(helpers => {
      window.conversationsData = [{ id: 'conversation', session_id: 'session', codex_state: 'working' }];
      window.currentSession = { id: 'session', source: 'codex', engine: 'codex' };
      fixturePane.currentSession = currentSession;
      window.sessionIsActivelyWorking = row => row.codex_state === 'working';
      window.getConvInputBarForPane = () => document.querySelector('.conv-input-bar');
      window.isPendingSendEchoElement = el => el.matches('.pending, .send-queued, .send-delivered');
      (0, eval)(helpers);
    }, "window._steerInFlightTexts = new Map(); window._queuedSteerErrors = new Map();\n" + functions(['queuedSteerCardCount', 'trayConversationView', 'steerTextKeys', 'markSteerInFlight', 'clearSteerInFlight', 'steerIsInFlight', 'queuedSteerErrorKey', 'setQueuedSteerError', 'clearQueuedSteerError', 'applyQueuedSteerError', 'beginOptimisticSteerMove', 'settleSteeredServerCard', 'revertOptimisticSteerMove', 'syncQueuedSteerAllControl', 'takeQueuedSendAcknowledgement', 'queuedFromSessionRow', 'queuedFromDescription', 'syncQueuedFromLabel', 'syncQueuedSteerTray']));
    const result = await page.evaluate(run, input);
    if (process.env.STEER_SCREENSHOT && viewport) {
      await page.addStyleTag({ content: fs.readFileSync('static/app.css', 'utf8')
        + 'body{display:block;margin:0}.conv-pane{height:95vh;width:100%}.conversations-view{display:block!important}.conv-input-bar{display:flex!important;min-height:100px}' });
      assert.equal(await page.evaluate(() => [...document.querySelectorAll('.steering-optimistic > button')]
        .every(button => getComputedStyle(button).display === 'none')), true);
      await page.screenshot({ path: process.env.STEER_SCREENSHOT + '-' + viewport.width + '.png' });
    }
    return result;
  } finally {
    await page.close();
  }
}

test('busy sends paint once in the tray and keep the same node through queue acknowledgement', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Busy follow-up', 'session', 'main');
    const row = pending.element;
    const before = { inTray: !!row.closest('.queued-steer-tray'),
      transcript: getConvView().querySelectorAll('.event.user_text').length,
      steerDisabled: row.querySelector('[data-steer-queued-message]').disabled,
      cancelDisabled: row.querySelector('[data-cancel-queued-message]').disabled,
      label: row.querySelector('.send-queued-text').textContent };
    syncQueuedSteerTray(getConvView(), 'main', true);
    restorePendingSendEchoes('conversation', 'main');
    markPendingSendQueued(pending, 'Queued for Codex');
    return { before, sameNode: row === pending.element && row.isConnected,
      count: document.querySelectorAll('.queued-steer-tray .event').length,
      enabled: !row.querySelector('[data-steer-queued-message]').disabled
        && !row.querySelector('[data-cancel-queued-message]').disabled };
  }, undefined, true);
  assert.deepEqual(result, { before: { inTray: true, transcript: 0,
    steerDisabled: false, cancelDisabled: true, label: 'Sending…' },
    sameNode: true, count: 1, enabled: true });
});

test('busy send restoration retains staging and a direct delivery removes the tray', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Turn just ended', 'session', 'main');
    document.querySelector('.queued-steer-tray').remove();
    restorePendingSendEchoes('conversation', 'main');
    const restoredInTray = !!pending.element.closest('.queued-steer-tray');
    markPendingSendDelivered(pending, { via: 'codex-app-turn' });
    return { restoredInTray, delivered: pending.element.classList.contains('send-delivered'),
      transcript: getConvView().contains(pending.element),
      tray: !!document.querySelector('.queued-steer-tray') };
  }, undefined, true);
  assert.deepEqual(result, { restoredInTray: true, delivered: true, transcript: true, tray: false });
});

test('idle sends stay in the transcript and rejected busy sends remove the empty tray', async () => {
  const result = await fixture(() => {
    conversationsData[0].codex_state = 'idle';
    const idle = appendPendingSendEcho('Idle follow-up', 'session', 'main');
    const idleInTranscript = getConvView().contains(idle.element);
    conversationsData[0].codex_state = 'working';
    const busy = appendPendingSendEcho('Refused follow-up', 'session', 'main');
    removePendingSendEcho(busy);
    return { idleInTranscript, tray: !!document.querySelector('.queued-steer-tray'),
      pending: _pendingSends.length };
  }, undefined, true);
  assert.deepEqual(result, { idleInTranscript: true, tray: false, pending: 1 });
});

test('a queue snapshot confirms a busy send before its POST returns without duplicating it', async () => {
  const result = await fixture(() => {
    const first = appendPendingSendEcho('First', 'session', 'main');
    const second = appendPendingSendEcho('Second', 'session', 'main');
    const allDisabled = document.querySelector('[data-steer-all-queued]').disabled;
    const server = addQueueCard('First');
    getConvView().appendChild(server);
    syncQueuedSteerTray(getConvView(), 'main', false);
    markPendingSendQueued(first, 'Queued');
    markPendingSendQueued(second, 'Queued');
    return { allDisabled, count: document.querySelectorAll('.queued-steer-tray .event').length,
      sameNode: first.element.isConnected && second.element.isConnected,
      confirmed: !first.entry.busySend && first.entry.queued,
      allEnabled: !document.querySelector('[data-steer-all-queued]').disabled };
  }, undefined, true);
  assert.deepEqual(result, { allDisabled: false, count: 2, sameNode: true, confirmed: true, allEnabled: true });
});

test('Steer accepts a click before send acknowledgement and replaces the confirmed delivery ID once', async () => {
  const result = await fixture(async () => {
    const pending = appendPendingSendEcho('Immediate steer', 'session', 'main');
    const requests = [];
    let sent;
    const done = new Promise(resolve => { sent = resolve; });
    window.postInjectInput = async (...args) => {
      requests.push(args);
      sent();
      return { ok: true, queued_consumed: 1, via: 'codex-steer' };
    };
    pending.element.querySelector('[data-steer-queued-message]').click();
    await Promise.resolve();
    const beforeAck = requests.length;
    syncQueuedSteerTray(getConvView(), 'main', false);
    pending.element.querySelector('[data-steer-queued-message]').click();
    bindPendingSendDeliveryId(pending, 'delivery-17');
    markPendingSendQueued(pending, 'Queued');
    await done;
    await Promise.resolve();
    return { beforeAck, requests, delivered: pending.entry.delivered };
  }, undefined, true);
  assert.deepEqual(result, { beforeAck: 0,
    requests: [['session', 'Immediate steer', 'steer', { replaceQueued: true, userInitiated: true, replaceQueuedIds: ['delivery-17'] }]],
    delivered: true });
});

test('early steer waits end without another send after delivery, rejection or a durable receipt', async () => {
  const result = await fixture(async () => {
    const results = [];
    for (const outcome of ['delivered', 'rejected', 'receipt']) {
      const pending = appendPendingSendEcho(outcome, 'session', 'main');
      const waiting = readyQueuedSteerRows([pending.element]);
      if (outcome === 'delivered') markPendingSendDelivered(pending, { via: 'codex-app-turn' });
      else if (outcome === 'rejected') removePendingSendEcho(pending);
      else reconcileUserEvent({ text: outcome, ts: new Date().toISOString() }, 'main', getConvView());
      results.push((await waiting).length);
    }
    return results;
  }, undefined, true);
  assert.deepEqual(result, [0, 0, 0]);
});

test('Steer all accepts pending sends and waits for both delivery IDs before replacing them', async () => {
  const result = await fixture(async () => {
    const first = appendPendingSendEcho('First', 'session', 'main');
    const second = appendPendingSendEcho('Second', 'session', 'main');
    const requests = [];
    let sent;
    const done = new Promise(resolve => { sent = resolve; });
    window.postInjectInput = async (...args) => {
      requests.push(args);
      sent();
      return { ok: true, queued_consumed: 2 };
    };
    document.querySelector('[data-steer-all-queued]').click();
    bindPendingSendDeliveryId(first, 'first-id');
    markPendingSendQueued(first, 'Queued');
    await Promise.resolve();
    const beforeSecond = requests.length;
    bindPendingSendDeliveryId(second, 'second-id');
    markPendingSendQueued(second, 'Queued');
    await done;
    return { beforeSecond, requests };
  }, undefined, true);
  assert.deepEqual(result, { beforeSecond: 0,
    requests: [['session', 'First\n\nSecond', 'steer', { replaceQueuedTexts: ['First', 'Second'], replaceQueuedIds: ['first-id', 'second-id'], userInitiated: true }]] });
});

test('an unacknowledged busy send becomes a retry notice instead of a stuck tray card', async () => {
  const result = await fixture(() => {
    let expire;
    window.setTimeout = callback => { expire = callback; return 123; };
    const pending = appendPendingSendEcho('No acknowledgement', 'session', 'main');
    expire();
    return { inTranscript: getConvView().contains(pending.element),
      retry: !!pending.element.querySelector('.not-ack-retry'),
      tray: !!document.querySelector('.queued-steer-tray'), pending: _pendingSends.length };
  }, undefined, true);
  assert.deepEqual(result, { inTranscript: true, retry: true, tray: false, pending: 0 });
});

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

test('rebuilding queued cards reuses pending records across repeated restores', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Rebuild this queue', 'session', 'main');
    markPendingSendQueued(pending, 'Waiting for this turn');
    for (let i = 0; i < 6; i++) {
      document.querySelector('.queued-steer-tray')?.remove();
      restorePendingSendEchoes('conversation', 'main');
      restorePendingSendEchoes('conversation', 'main');
    }
    return { cards: document.querySelectorAll('.queued-steer-tray .send-queued').length,
      tracked: _pendingSends.length, saved: fixturePane.pendingSendsByConv.conversation.length,
      original: _pendingSends[0] === pending.entry,
      liveReference: pending.element.isConnected && pending.element === pending.entry.element };
  });
  assert.deepEqual(result, { cards: 1, tracked: 1, saved: 1, original: true, liveReference: true });
});

test('rebuilding genuine identical queued sends preserves each occurrence', async () => {
  const result = await fixture(() => {
    for (let i = 0; i < 2; i++) {
      markPendingSendQueued(appendPendingSendEcho('Again', 'session', 'main'), 'Waiting');
    }
    const originals = _pendingSends.slice();
    for (let i = 0; i < 4; i++) {
      document.querySelector('.queued-steer-tray')?.remove();
      restorePendingSendEchoes('conversation', 'main');
    }
    return { cards: document.querySelectorAll('.queued-steer-tray .send-queued').length,
      tracked: _pendingSends.length, saved: fixturePane.pendingSendsByConv.conversation.length,
      original: originals.every(entry => _pendingSends.includes(entry)) };
  });
  assert.deepEqual(result, { cards: 2, tracked: 2, saved: 2, original: true });
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

test('event reconciliation and repeated render sweeps share one delivery receipt', async () => {
  const result = await fixture(() => {
    const first = appendPendingSendEcho('Again', 'session', 'main');
    const second = appendPendingSendEcho('Again', 'session', 'main');
    const stamp = Math.max(first.entry.ts, second.entry.ts) + 1000;
    const row = reconcileUserEvent({ text: 'Again', ts: new Date(stamp).toISOString() }, 'main', getConvView());
    getConvView().append(row);
    reconcileRenderedUserRows(getConvView(), 'main');
    reconcileRenderedUserRows(getConvView(), 'main');
    return { first: first.element.isConnected, second: second.element.isConnected, tracked: _pendingSends.length };
  });
  assert.deepEqual(result, { first: false, second: true, tracked: 1 });
});

test('rendered history cannot acknowledge a newer pending send', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Again', 'session', 'main');
    const row = document.createElement('div');
    row.className = 'event user_text';
    row.dataset.tsEpoch = String(pending.entry.ts - 60000);
    row.innerHTML = '<div class="user-msg">Again</div>';
    getConvView().append(row);
    reconcileRenderedUserRows(getConvView(), 'main');
    return { connected: pending.element.isConnected, tracked: _pendingSends.length };
  });
  assert.deepEqual(result, { connected: true, tracked: 1 });
});

test('one rendered durable occurrence clears one pending send across repeated sweeps', async () => {
  const result = await fixture(() => {
    const first = appendPendingSendEcho('Again', 'session', 'main');
    const second = appendPendingSendEcho('Again', 'session', 'main');
    const row = document.createElement('div');
    row.className = 'event user_text';
    row.dataset.tsEpoch = String(Math.max(first.entry.ts, second.entry.ts) + 1000);
    row.innerHTML = '<div class="user-msg">Again</div>';
    getConvView().append(row);
    reconcileRenderedUserRows(getConvView(), 'main');
    reconcileRenderedUserRows(getConvView(), 'main');
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

test('provisional Codex live-overlay user event never acknowledges an injected send', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Injected fixture', 'session', 'main');
    reconcileUserEvent({
      text: 'Injected fixture',
      provisional: true,
      live_key: 'turn-1:user-message-1',
    }, 'main', getConvView());
    return {
      connected: pending.element.isConnected,
      tracked: _pendingSends.length,
      cleared: clearedSends,
    };
  });
  assert.deepEqual(result, { connected: true, tracked: 1, cleared: 0 });
});

test('provisional rendered user bubble is not durable proof for an injected send', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Injected fixture', 'session', 'main');
    const provisional = document.createElement('div');
    provisional.className = 'event user_text provisional';
    provisional.dataset.liveKey = 'turn-1:user-message-1';
    provisional.innerHTML = '<div class="user-msg" data-raw-text="Injected fixture">Injected fixture</div>';
    getConvView().append(provisional);
    reconcileRenderedUserRows(getConvView(), 'main');
    return {
      connected: pending.element.isConnected,
      tracked: _pendingSends.length,
      cleared: clearedSends,
    };
  });
  assert.deepEqual(result, { connected: true, tracked: 1, cleared: 0 });
});

test('peer message is not durable proof for an injected send', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Injected fixture', 'session', 'main');
    const peer = document.createElement('div');
    peer.className = 'event user_text peer-message';
    peer.innerHTML = '<div class="user-msg" data-raw-text="Injected fixture">Injected fixture</div>';
    getConvView().append(peer);
    reconcileRenderedUserRows(getConvView(), 'main');
    return {
      connected: pending.element.isConnected,
      tracked: _pendingSends.length,
      cleared: clearedSends,
    };
  });
  assert.deepEqual(result, { connected: true, tracked: 1, cleared: 0 });
});

test('optimistically steering row is not durable proof for its own pending send', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Steering fixture', 'session', 'main');
    pending.element.classList.remove('pending');
    pending.element.classList.add('steering-optimistic');
    reconcileRenderedUserRows(getConvView(), 'main');
    return {
      connected: pending.element.isConnected,
      tracked: _pendingSends.length,
      cleared: clearedSends,
    };
  });
  assert.deepEqual(result, { connected: true, tracked: 1, cleared: 0 });
});

test('provisional agent output does not reorder pending input or clear sending state', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Injected fixture', 'session', 'main');
    const sentinel = document.createElement('div');
    sentinel.id = 'tail-sentinel';
    getConvView().append(sentinel);
    runRenderTail(getConvView(), 'main', { provisionalOverlay: true }, [{ type: 'assistant' }]);
    return {
      pendingBeforeSentinel: pending.element.nextElementSibling === sentinel,
      tracked: _pendingSends.length,
      cleared: clearedSends,
      generatingClears: clearedGenerating,
    };
  });
  assert.deepEqual(result, {
    pendingBeforeSentinel: true,
    tracked: 1,
    cleared: 0,
    generatingClears: 0,
  });
});

test('durable agent output clears sending state without clearing the live turn indicator', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Injected fixture', 'session', 'main');
    const sentinel = document.createElement('div');
    sentinel.id = 'tail-sentinel';
    getConvView().append(sentinel);
    runRenderTail(getConvView(), 'main', {}, [{ type: 'assistant' }]);
    return {
      pendingAtTail: pending.element === getConvView().lastElementChild,
      tracked: _pendingSends.length,
      cleared: clearedSends,
      generatingClears: clearedGenerating,
    };
  });
  assert.deepEqual(result, {
    pendingAtTail: true,
    tracked: 1,
    cleared: 1,
    generatingClears: 0,
  });
});

test('durable duplicate-collapse ignores provisional live-overlay user rows', () => {
  const branch = source.slice(
    source.indexOf('// Webui panes: collapse a durable user bubble'),
    source.indexOf('        const imagesHtml = renderImageDescriptors',
      source.indexOf('// Webui panes: collapse a durable user bubble')),
  );
  assert.match(branch,
    /\.event\.user_text:not\(\.pending\):not\(\.send-queued\):not\(\.send-delivered\):not\(\.not-acknowledged\):not\(\.provisional\)/);
});

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

test('a whole-second durable timestamp acknowledges a send made later in that second', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('what is the status', 'session', 'main');
    pending.entry.ts = Math.floor(pending.entry.ts / 1000) * 1000 + 900;
    reconcileUserEvent({ text: 'what is the status', ts: new Date(Math.floor(pending.entry.ts / 1000) * 1000).toISOString().replace(/\.\d+Z$/, 'Z') }, 'main', getConvView());
    return { connected: pending.element.isConnected, tracked: _pendingSends.length };
  });
  assert.deepEqual(result, { connected: false, tracked: 0 });
});

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

test('a successful steer stays delivered after a late queue ACK and pane rebuild', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Already steered', 'session', 'main');
    markPendingSendQueued(pending, 'Waiting');
    pending.element.classList.remove('send-queued');
    pending.element.classList.add('steering-optimistic');
    markPendingSendDelivered(pending, { via: 'codex-steer', queued_consumed: 1 });
    markPendingSendQueued(pending, 'Late queue ACK');
    const delivered = pending.entry.delivered;
    const queued = pending.entry.queued;
    document.querySelector('.queued-steer-tray')?.remove();
    getConvView().replaceChildren();
    restorePendingSendEchoes('conversation', 'main');
    return { delivered, queued, cards: document.querySelectorAll('.send-queued').length,
      echoes: document.querySelectorAll('.send-delivered').length,
      cancel: !!document.querySelector('.send-delivered .pending-send-cancel') };
  });
  assert.deepEqual(result, { delivered: true, queued: false, cards: 0, echoes: 1, cancel: false });
});

test('a queue ACK after durable reconciliation cannot revive a retired send', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Already landed', 'session', 'main');
    reconcileUserEvent({ text: 'Already landed' }, 'main', getConvView());
    markPendingSendQueued(pending, 'Late queue ACK');
    return { cards: document.querySelectorAll('.send-queued').length,
      saved: fixturePane.pendingSendsByConv.conversation || [], tracked: _pendingSends.length };
  });
  assert.deepEqual(result, { cards: 0, saved: [], tracked: 0 });
});

test('confirmed queue consumption overrides a queued card even without an optimistic move', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Consumed queue', 'session', 'main');
    markPendingSendQueued(pending, 'Waiting');
    markPendingSendDelivered(pending, { via: 'codex-steer', queued_consumed: 1 });
    return { delivered: pending.entry.delivered, queued: pending.entry.queued,
      actionable: !!pending.element.querySelector('[data-steer-queued-message]') };
  });
  assert.deepEqual(result, { delivered: true, queued: false, actionable: false });
});

test('an ordinary send ACK cannot override an existing authoritative queued state', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Still queued', 'session', 'main');
    markPendingSendQueued(pending, 'Waiting');
    markPendingSendDelivered(pending, { ok: true });
    return { queued: pending.entry.queued, delivered: !!pending.entry.delivered,
      actionable: !!pending.element.querySelector('[data-steer-queued-message]') };
  });
  assert.deepEqual(result, { queued: true, delivered: false, actionable: true });
});

test('a steer completion persists delivery to its original pane after focus changes', async () => {
  const result = await fixture(() => {
    const pending = appendPendingSendEcho('Original pane', 'session', 'main');
    markPendingSendQueued(pending, 'Waiting', { sync: false });
    window._pendingSends = [{ text: 'Other pane', ts: Date.now() }];
    markPendingSendDelivered(pending, { via: 'codex-steer', queued_consumed: 1 });
    return fixturePane.pendingSendsByConv.conversation.map(row => ({
      text: row.text, delivered: row.delivered, queued: row.queued,
    }));
  });
  assert.deepEqual(result, [{ text: 'Original pane', delivered: true, queued: false }]);
});

test('a missing queued steer refreshes older durable history without resending', async () => {
  const result = await fixture(async () => {
    currentSession.source = 'codex';
    const pending = appendPendingSendEcho('Previously delivered', 'session', 'main');
    markPendingSendQueued(pending, 'Waiting');
    let requests = 0;
    window.postInjectInput = async () => {
      requests++;
      return { ok: false, code: 'queued_message_missing' };
    };
    let refreshed;
    const done = new Promise(resolve => { refreshed = resolve; });
    window.fetchConversationEvents = async pid => {
      const full = fixturePane.wantFull && fixturePane.lastLine === 0;
      reconcileUserEvent({ text: 'Previously delivered', ts: new Date().toISOString() }, pid, getConvView());
      refreshed(full);
    };
    pending.element.querySelector('[data-steer-queued-message]').click();
    const full = await done;
    restorePendingSendEchoes('conversation', 'main');
    return { full, requests, tracked: _pendingSends.length,
      queued: document.querySelectorAll('.send-queued').length };
  });
  assert.deepEqual(result, { full: true, requests: 1, tracked: 0, queued: 0 });
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

test('Devin reply chunks preserve the generating row until live status settles the turn', async () => {
  const result = await fixture(() => {
    window.clearLiveGeneratingIndicator = view => {
      view.querySelectorAll('.conv-live-tool-inline.is-generating').forEach(node => node.remove());
    };
    currentSession.source = 'devin-cli';
    const indicator = document.createElement('div');
    indicator.className = 'conv-live-tool-inline is-generating';
    indicator.textContent = 'Generating…';
    getConvView().append(indicator);
    const connected = [];
    for (const type of ['assistant', 'tool_result', 'assistant']) {
      runRenderTail(getConvView(), 'main', {}, [{ type }]);
      connected.push(indicator.isConnected);
    }
    return connected;
  });
  assert.deepEqual(result, [true, true, true]);
});


for (const width of [390, 1280]) {
  test(`Steer moves immediately while both Send and steer delivery wait (${width}px)`, async () => {
    const result = await fixture(async () => {
      const pending = appendPendingSendEcho('Please use the new instructions.', 'session', 'main');
      getConvInputBarForPane().innerHTML = '<textarea aria-label="Message">Unsent draft stays editable</textarea>';
      let requests = 0;
      window.postInjectInput = () => { requests++; return new Promise(() => {}); };
      const before = performance.now();
      pending.element.querySelector('[data-steer-queued-message]').click();
      const immediate = {
        inConversation: pending.element.parentElement === getConvView(),
        tray: !!document.querySelector('.queued-steer-tray'),
        background: pending.element.querySelector('[role="status"]').textContent,
        delivered: !!pending.entry.delivered,
        requests,
        draftEnabled: !getConvInputBarForPane().querySelector('textarea').disabled,
      };
      const elapsed = performance.now() - before;
      // Polls while the original Send waits must not pull it back into the tray.
      syncQueuedSteerTray(getConvView(), 'main', true);
      restorePendingSendEchoes('conversation', 'main');
      const afterPoll = pending.element.parentElement === getConvView()
        && !document.querySelector('.queued-steer-tray');
      // Rebuilding the pane keeps the accepted action visible, too.
      getConvView().replaceChildren();
      restorePendingSendEchoes('conversation', 'main');
      const afterRebuild = pending.element.parentElement === getConvView()
        && !!pending.element.querySelector('[role="status"]');
      bindPendingSendDeliveryId(pending, 'confirmed-delivery');
      markPendingSendQueued(pending, 'Queued');
      await new Promise(resolve => setTimeout(resolve, 0));
      return { immediate, elapsed, afterPoll, afterRebuild, requests,
        inConversation: pending.element.parentElement === getConvView(),
        status: pending.element.querySelector('[role="status"]').textContent };
    }, undefined, true, { width, height: 800 });
    assert.deepEqual(result.immediate, { inConversation: true, tray: false,
      background: 'Steering in background…', delivered: false, requests: 0, draftEnabled: true });
    assert.ok(result.elapsed < 300, `click took ${result.elapsed}ms`);
    assert.equal(result.afterPoll, true);
    assert.equal(result.afterRebuild, true);
    assert.equal(result.requests, 1);
    assert.equal(result.inConversation, true);
    assert.equal(result.status, 'Steering in background…');
  });
}

test('Steer all immediately clears the tray while Send acknowledgements wait', async () => {
  const result = await fixture(async () => {
    const first = appendPendingSendEcho('First instruction', 'session', 'main');
    const second = appendPendingSendEcho('Second instruction', 'session', 'main');
    let requests = 0;
    window.postInjectInput = () => { requests++; return new Promise(() => {}); };
    document.querySelector('[data-steer-all-queued]').click();
    const immediate = { tray: !!document.querySelector('.queued-steer-tray'),
      rows: getConvView().querySelectorAll('.steering-optimistic').length, requests };
    bindPendingSendDeliveryId(first, 'first');
    markPendingSendQueued(first, 'Queued');
    await Promise.resolve();
    const beforeSecond = requests;
    bindPendingSendDeliveryId(second, 'second');
    markPendingSendQueued(second, 'Queued');
    await new Promise(resolve => setTimeout(resolve, 0));
    return { immediate, beforeSecond, requests };
  }, undefined, true);
  assert.deepEqual(result, { immediate: { tray: false, rows: 2, requests: 0 }, beforeSecond: 0, requests: 1 });
});

test('an early accepted steer failure restores a visible error and working retry/cancel', async () => {
  const result = await fixture(async () => {
    const pending = appendPendingSendEcho('Recover this instruction', 'session', 'main');
    window.postInjectInput = async () => ({ ok: false, error: 'Engine unavailable', queued_preserved: true });
    pending.element.querySelector('[data-steer-queued-message]').click();
    bindPendingSendDeliveryId(pending, 'recover-id');
    markPendingSendQueued(pending, 'Queued');
    // Change focus while delivery waits; recovery still belongs to the original pane.
    window.activePaneId = () => 'other-pane';
    const originalGetView = getConvViewForPane;
    window.getConvViewForPane = pid => pid === 'main' ? originalGetView() : null;
    await new Promise(resolve => setTimeout(resolve, 0));
    const row = pending.element;
    return { tray: !!row.closest('.queued-steer-tray'),
      error: row.querySelector('[role="alert"]')?.textContent,
      background: !!row.querySelector('.steer-background-note'),
      retry: !row.querySelector('[data-steer-queued-message]').disabled,
      cancel: !row.querySelector('[data-cancel-queued-message]').disabled };
  }, undefined, true);
  assert.deepEqual(result, { tray: true, error: 'Not delivered: Engine unavailable',
    background: false, retry: true, cancel: true });
});

for (const outcome of ['delivered', 'rejected', 'receipt']) {
  test(`an early Steer finishes cleanly when original Send is ${outcome}`, async () => {
    const result = await fixture(async outcome => {
      const pending = appendPendingSendEcho('No duplicate please', 'session', 'main');
      let requests = 0;
      window.postInjectInput = async () => { requests++; return { ok: true }; };
      pending.element.querySelector('[data-steer-queued-message]').click();
      if (outcome === 'delivered') markPendingSendDelivered(pending, { via: 'codex-app-turn' });
      else if (outcome === 'rejected') removePendingSendEcho(pending);
      else reconcileUserEvent({ text: pending.text, ts: new Date().toISOString() }, 'main', getConvView());
      await new Promise(resolve => setTimeout(resolve, 0));
      return { requests, held: steerIsInFlight('session', pending.text),
        statuses: document.querySelectorAll('.steer-background-note').length,
        tray: !!document.querySelector('.queued-steer-tray') };
    }, outcome, true);
    assert.deepEqual(result, { requests: 0, held: false, statuses: 0, tray: false });
  });
}
