const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = require('./legacy-dashboard-source.cjs');
const css = fs.readFileSync('static/app.css', 'utf8');
const helpers = app.slice(app.indexOf('  // Try one status check before offering Investigate.'),
  app.indexOf('  async function investigateSilentSession('));
const inject = app.slice(app.indexOf('  async function postInjectInput('), app.indexOf('  function steeredToastText('));
let browser, server, origin;
before(async () => {
  server = http.createServer((req, res) => res.end('<body><div id="view"></div></body>'));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = 'http://127.0.0.1:' + server.address().port;
  browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
});
after(async () => {
  await browser?.close();
  await new Promise(resolve => server?.close(resolve));
});
async function fixture(page, { reset = true, sid = 'silent-test' } = {}) {
  await page.goto(origin);
  await page.addStyleTag({ content: css });
  await page.evaluate(({ helpers, inject, reset, sid }) => {
    if (reset) localStorage.clear();
    window.clock = 1800000000000;
    Date.now = () => clock;
    window.currentSession = { id: sid, source: 'codex' };
    window.liveStatus = { forSessionId: sid, codexState: 'working', transcriptMtime: (clock - 300000) / 1000 };
    window.sent = [];
    window.reply = { ok: true, via: 'codex-steer' };
    window.fetch = async (url, opts) => {
      sent.push({ url, payload: JSON.parse(opts.body) });
      if (window.pendingReply) await new Promise(resolve => { window.resolveReply = resolve; });
      if (window.networkError) throw new Error('Lost reply');
      return { ok: !reply.error, json: async () => reply };
    };
    window.durableActionId = () => 'ordinary-action';
    window.escapeHtml = window.escapeAttr = s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;');
    window._optimisticAgeLabel = ms => Math.floor(ms / 60000) + 'm';
    window.investigated = [];
    window.investigateSilentSession = sid => investigated.push(sid);
    (0, eval)(inject + helpers);
    window.tick = async (ms = 0) => {
      clock += ms;
      updateSilentInvestigateBar(document.querySelector('#view'));
      // Drain the Web Lock and the asynchronous injection without wall waits.
      await navigator.locks.request('ccc-silent-nudge:' + currentSession.id, () => {});
    };
    window.addEvent = (kind, text = '', epoch = clock) => {
      const row = document.createElement('div');
      row.className = 'event ' + kind;
      row.dataset.tsEpoch = String(epoch);
      if (kind.includes('user_text')) {
        const msg = document.createElement('span');
        msg.className = 'user-msg'; msg.dataset.rawText = text; msg.textContent = text; row.append(msg);
      } else row.textContent = text;
      document.querySelector('#view').append(row);
    };
    window.bar = () => document.querySelector('.conv-silent-bar')?.textContent || '';
  }, { helpers, inject, reset, sid });
}

test('five minutes sends exactly one attributed question mark; another five minutes offers Investigate', async () => {
  const page = await browser.newPage();
  try {
    await fixture(page);
    assert.deepEqual(await page.evaluate(async () => {
      clock -= 1; await tick();
      return { sends: sent.length, bar: bar() };
    }), { sends: 0, bar: '' });
    const first = await page.evaluate(async () => { await tick(1); return { sent, bar: bar() }; });
    assert.equal(first.bar, '');
    assert.equal(first.sent.length, 1);
    assert.deepEqual(first.sent[0], { url: '/api/inject-input', payload: {
      session_id: 'silent-test', text: '?', mode: 'steer',
      idempotency_key: 'silent-nudge:silent-test:1799999700000', announced_from: 'CCC silence check',
    } });
    await page.evaluate(async () => { for (let i = 0; i < 5; i++) await tick(); });
    assert.equal(await page.evaluate(() => sent.length), 1);
    assert.equal(await page.evaluate(async () => { await tick(299999); return bar(); }), '');
    assert.match(await page.evaluate(async () => { await tick(1); return bar(); }), /has not responded to the automatic/);
    await page.click('[data-role="silent-investigate"]');
    assert.deepEqual(await page.evaluate(() => investigated), ['silent-test']);
    assert.deepEqual(await page.evaluate(async () => { await tick(3600000); return { sends: sent.length, bars: document.querySelectorAll('.conv-silent-bar').length }; }), { sends: 1, bars: 1 });
    if (process.env.SILENT_NUDGE_SCREENSHOT) await page.screenshot({ path: process.env.SILENT_NUDGE_SCREENSHOT });
    await fixture(page, { reset: false });
    assert.equal(await page.evaluate(async () => { await tick(); return sent.length; }), 0, 'reload preserves the attempt');
  } finally { await page.close(); }
});

test('the automatic prompt and its transcript write never count as an agent response', async () => {
  const page = await browser.newPage();
  try {
    await fixture(page);
    await page.evaluate(async () => {
      await tick(); clock += 1000;
      addEvent('user_text peer-message', 'Announced from: CCC silence check\n\n?');
      liveStatus.transcriptMtime = clock / 1000;
      await tick(300000);
    });
    assert.equal(await page.evaluate(() => sent.length), 1);
    assert.match(await page.evaluate(() => bar()), /has not responded/);
  } finally { await page.close(); }
});

test('an existing check in the transcript prevents another send in a fresh browser profile', async () => {
  const page = await browser.newPage();
  try {
    await fixture(page);
    assert.deepEqual(await page.evaluate(async () => {
      addEvent('user_text', 'Original request', clock - 600000);
      addEvent('user_text peer-message', 'Announced from: CCC silence check\n\n?', clock - 300000);
      await tick(); return { sends: sent.length, warning: bar().includes('has not responded') };
    }), { sends: 0, warning: true });
  } finally { await page.close(); }
});

test('plain question marks, pending inputs and bookkeeping do not rearm an unanswered check', async () => {
  const page = await browser.newPage();
  try {
    await fixture(page);
    assert.equal(await page.evaluate(async () => {
      await tick(); clock += 1000;
      addEvent('user_text', '?'); addEvent('user_text pending', 'Waiting input'); addEvent('system', 'Heartbeat');
      await tick(300000); return sent.length;
    }), 1);
  } finally { await page.close(); }
});

test('active Claude turns and stale tools use the same single nudge path', async () => {
  const page = await browser.newPage();
  try {
    await fixture(page);
    assert.equal(await page.evaluate(async () => {
      currentSession.source = 'claude'; liveStatus.codexState = null;
      liveStatus.sidecarStatus = 'active'; liveStatus.sidecarInFlight = true; liveStatus.staleToolCall = true;
      await tick(); return sent.length;
    }), 1);
  } finally { await page.close(); }
});

test('a response recorded in the same rounded second also starts a new episode', async () => {
  const page = await browser.newPage();
  try {
    await fixture(page);
    assert.equal(await page.evaluate(async () => {
      await tick(); addEvent('assistant', 'Recovered', Math.floor(clock / 1000) * 1000);
      await tick(300000); return sent.length;
    }), 2);
  } finally { await page.close(); }
});

for (const kind of ['assistant', 'thinking', 'tool_use', 'tool_result', 'user_text']) {
  test(`new ${kind} activity allows a check for a later silence episode`, async () => {
    const page = await browser.newPage();
    try {
      await fixture(page);
      assert.equal(await page.evaluate(async kind => {
        await tick(); clock += 1000; addEvent(kind, 'New work'); await tick();
        if (bar()) throw new Error('fresh output must hide the warning');
        await tick(300000); return sent.length;
      }, kind), 2);
    } finally { await page.close(); }
  });
}

for (const [name, patch] of Object.entries({
  idle: { codexState: 'idle' }, approval: { needsApproval: true }, question: { questionWaiting: true },
  tool: { sidecarInFlight: true }, wrongSession: { forSessionId: 'other' },
  reasoning: { codexAppServerLastActivityAt: 1800000000 }, sidecar: { sidecarTs: 1800000000 },
})) {
  test(`${name} suppresses automatic input and warnings`, async () => {
    const page = await browser.newPage();
    try {
      await fixture(page);
      assert.deepEqual(await page.evaluate(async patch => {
        Object.assign(liveStatus, patch); await tick(); return { sends: sent.length, bar: bar() };
      }, patch), { sends: 0, bar: '' });
    } finally { await page.close(); }
  });
}

for (const failure of ['rejected', 'network']) {
  test(`${failure} delivery shows Investigate without retrying`, async () => {
    const page = await browser.newPage();
    try {
      await fixture(page);
      assert.deepEqual(await page.evaluate(async failure => {
        if (failure === 'network') networkError = true; else reply = { ok: false, error: 'Cannot deliver' };
        await tick(); await tick(600000);
        return { sends: sent.length, warning: bar().includes('could not be confirmed') };
      }, failure), { sends: 1, warning: true });
    } finally { await page.close(); }
  });
}

test('two tabs share one attempt, including while the first request is pending', async () => {
  const a = await browser.newPage(), b = await browser.newPage();
  try {
    await fixture(a); await fixture(b, { reset: false });
    await a.evaluate(() => { pendingReply = true; updateSilentInvestigateBar(document.querySelector('#view')); });
    await a.waitForFunction(() => sent.length === 1);
    await b.evaluate(() => { updateSilentInvestigateBar(document.querySelector('#view')); });
    await a.evaluate(() => resolveReply());
    await b.evaluate(() => tick());
    assert.equal(await a.evaluate(() => sent.length) + await b.evaluate(() => sent.length), 1);
  } finally { await a.close(); await b.close(); }
});

test('queued nudges still lead to Investigate, and a pane switch during lock acquisition sends nothing', async () => {
  const page = await browser.newPage();
  try {
    await fixture(page);
    assert.match(await page.evaluate(async () => { reply = { ok: true, queued: true }; await tick(); await tick(300000); return bar(); }), /has not responded/);
    await fixture(page);
    assert.equal(await page.evaluate(async () => {
      const blocker = navigator.locks.request('ccc-silent-nudge:' + currentSession.id, async () => {
        updateSilentInvestigateBar(document.querySelector('#view'));
        currentSession = { id: 'different-session' };
      });
      await blocker; await tick(); return sent.length;
    }), 0);
  } finally { await page.close(); }
});
