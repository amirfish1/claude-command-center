// Simple-UI click-count verification harness.
//
// Verifies the "grandma test" acceptance contract: every core task must be
// completable in <=3 clicks/taps from the Simple-mode home screen.
//
// The script drives the real UI against the live local CCC server, but stubs
// the two mutating POSTs (spawn + answer) so a verification run never
// actually launches an agent session or injects input into one.
//
// DOM contract this script verifies (implemented in static/app.js):
//   #simpleHome                        Simple-mode home screen container
//   [data-simple-section="composer"]   composer's own draggable/collapsible
//                                      section — starts COLLAPSED on first
//                                      load; its .simple-section-toggle must
//                                      be tapped open before the fields below
//                                      are usable (counts toward the 3-click
//                                      budget for Task 1)
//   #simpleComposerInput               big "what do you want done" textarea
//   #simpleAgentRow [data-simple-agent]   one-tap agent chips (chip shows the
//                                      plain-language model that comes with it)
//   #simpleEffortRow [data-simple-effort] one-tap effort chips
//   #simpleStartBtn                    start button
//   #simpleNeedsYou .simple-nya-card   cards for workers waiting on the user,
//     .simple-nya-answer-input         each with an inline answer box
//     [data-answer-send]               and send button (and/or option buttons
//     [data-answer-option]             when the worker offered choices)
//   #simpleTasks .simple-task-card     "Your conversations" — working +
//                                      finished merged into one list ordered
//                                      by last activity; .is-running marks a
//                                      card still working, .is-unseen-finished
//                                      marks a finished card not opened yet
//     .simple-status-line              plain-language status and a
//     .simple-memory-line              plain-language memory/context line
//   #simpleSeeAllFinished              "see all" link into full history
//   #simpleUsageLine                   plain-language usage block inside an
//                                      open conversation ("Memory: 71% ...")
//   #simpleBackHomeBtn                 back-to-home button in conversation view
// Depth 2 (open conversation = simple chat surface, not the advanced UI):
//   body.ccc-simple-conv-open          conversation-open body class
//   #simpleConvTitle                   plain-language conversation title
//   #convSendBtn .send-btn-label       labeled Send button
//   advanced chrome hidden: #statusRail, .conv-pane-header, #convInputContext,
//   #convEscBtn/#convCompactBtn/#convSteerBtn, .tool-call-group, .event.system
// Depth 3 (full-screen simple surfaces, one tap from home / bottom nav):
//   #simpleHistory  #simpleHistorySearch  #simpleHistoryList .simple-task-card
//   #simpleAutomations  #simpleAutomationsList [data-simple-queue]
//   #simpleAutomationDetail  #simpleAutomationDetailBody
//   #simpleSettings  #simpleAdvancedBtn  #simpleThemeRow [data-simple-theme]
// The "What's new" modal must never open in Simple mode.
//
// Usage:  node simple-ui-clicks.js
// Env:    SIMPLE_UI_URL   default: port.txt-resolved URL, else 127.0.0.1:8090
// Results are printed as a table and written to
// scratch/simple-ui-clicks-results.json (scratch/ is gitignored).

const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const puppeteer = require('./require-puppeteer.js');
const { findChromePath } = require('./puppeteer-browser-config.js');

const DEFAULT_URL = 'http://127.0.0.1:8090';
const MAX_CLICKS = 3;

function urlResponds(url, timeoutMs = 500) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => { res.resume(); resolve(true); });
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

async function resolveBaseUrl() {
  const portFile = path.join(os.homedir(), '.claude', 'command-center', 'port.txt');
  let fromFile;
  try {
    const raw = fs.readFileSync(portFile, 'utf8').trim();
    if (/^https?:\/\//.test(raw)) fromFile = raw;
    else if (/^\d+$/.test(raw)) fromFile = `http://127.0.0.1:${raw}`;
  } catch (_) { /* fall through */ }
  if (fromFile && await urlResponds(fromFile)) return fromFile;
  return DEFAULT_URL;
}

(async () => {
  const url = process.env.SIMPLE_UI_URL || await resolveBaseUrl();
  const results = [];
  const intercepted = { spawn: null, answer: null };

  const chromePath = findChromePath();
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    args: ['--no-sandbox'],
  });

  const record = (task, clicks, status, note) => {
    results.push({ task, clicks, status, note: note || '' });
    const mark = status === 'PASS' ? 'PASS' : status;
    console.log(`  [${mark}] ${task}: ${clicks === null ? '-' : clicks} click(s)${note ? ' — ' + note : ''}`);
  };

  try {
    const page = await browser.newPage();
    // Narrow viewport so the mobile/Simple chrome gate (max-width: 1200px)
    // is active; localStorage opts into Simple interface mode explicitly.
    await page.setViewport({ width: 1000, height: 800 });
    // The "What's New" version modal and the first-run product tour overlay
    // the whole page and eat every real click. Pre-seed the seen-version and
    // tour-done markers so neither opens during verification (same effect as
    // a returning user).
    let seenVersion = '';
    try {
      seenVersion = await new Promise((resolve) => {
        http.get(url + '/api/version', { timeout: 3000 }, (res) => {
          let body = '';
          res.on('data', (c) => { body += c; });
          res.on('end', () => {
            try { resolve(String(JSON.parse(body).version || '')); } catch (_) { resolve(''); }
          });
        }).on('error', () => resolve('')).on('timeout', function () { this.destroy(); resolve(''); });
      });
    } catch (_) { /* fall through — modal dismissal is best-effort */ }
    await page.evaluateOnNewDocument((version) => {
      try {
        localStorage.setItem('ccc-ui-mode', 'simple');
        localStorage.setItem('ccc-tour-done', '1');
        if (version) {
          localStorage.setItem('ccc-last-seen-version', version);
          localStorage.setItem('ccc-whats-new-dismissed-version', version);
        }
      } catch (_) {}
    }, seenVersion);

    // Stub mutating POSTs so verification has no side effects; capture their
    // payloads to prove the UI sent the right thing.
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      const u = req.url();
      const m = req.method();
      if (m === 'POST' && /\/api\/sessions\/spawn/.test(u)) {
        try { intercepted.spawn = JSON.parse(req.postData() || '{}'); } catch (_) { intercepted.spawn = {}; }
        req.respond({ status: 200, contentType: 'application/json',
          body: JSON.stringify({ ok: true, session_id: 'verify-ui-spawn', pid: 0 }) });
        return;
      }
      if (m === 'POST' && /\/api\/(inject-input|answer-question)/.test(u)) {
        try { intercepted.answer = JSON.parse(req.postData() || '{}'); } catch (_) { intercepted.answer = {}; }
        req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
        return;
      }
      req.continue();
    });

    await page.goto(url, { waitUntil: 'load', timeout: 60000 });
    await page.waitForNetworkIdle({ idleTime: 750, timeout: 5000 }).catch(() => {});

    // Counted click helper: wait for visible, scroll into view, click.
    // A transient app toast/overlay can cover the target at click time
    // ("Node is either not clickable"); a real user would just tap again,
    // so retry once after a short pause.
    let n = 0;
    const tap = async (sel, opts = {}) => {
      await page.waitForSelector(sel, { visible: true, timeout: opts.timeout || 8000 });
      await page.evaluate((s) => {
        const el = document.querySelector(s);
        if (el) el.scrollIntoView({ block: 'center' });
      }, sel);
      try {
        await page.click(sel);
      } catch (e) {
        if (opts.noRetry) throw e;
        await new Promise((resolve) => setTimeout(resolve, 700));
        await page.click(sel);
        n += 1;
      }
      n += 1;
    };
    const visible = (sel) => page.evaluate((s) => {
      const el = document.querySelector(s);
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    }, sel);
    const textOf = (sel) => page.evaluate((s) => {
      const el = document.querySelector(s);
      return el ? el.textContent.trim() : '';
    }, sel);
    const goHome = async () => {
      if (await visible('#simpleHome')) return;
      if (await visible('#simpleBackHomeBtn')) { await tap('#simpleBackHomeBtn'); return; }
      const homeNav = '[data-mobile-nav="home"]';
      if (await visible(homeNav)) { await tap(homeNav); }
    };

    // ── Home screen must be the landing surface in Simple mode ──
    await page.waitForSelector('#simpleHome', { visible: true, timeout: 15000 })
      .catch(() => {});

    // ── Task 1: give a new job, choosing agent/model/effort ──
    n = 0;
    try {
      if (!(await visible('#simpleHome'))) throw new Error('home screen not visible');
      // Composer starts collapsed on first load — expand it first if needed.
      // This tap counts toward the task's click budget.
      if (!(await visible('#simpleComposerInput'))) {
        await tap('[data-simple-section="composer"] .simple-section-toggle');
      }
      await tap('#simpleComposerInput');
      await page.type('#simpleComposerInput', 'Water the plants every morning at 8');
      // Choose an agent (the chip also fixes the model that comes with it);
      // effort defaults to the recommended level, changeable with one more tap.
      const agentChips = await page.$$('#simpleAgentRow [data-simple-agent]');
      if (!agentChips.length) throw new Error('no agent chips rendered');
      const chipSel = '#simpleAgentRow [data-simple-agent]:last-child';
      await tap(chipSel);
      const effortVisible = await visible('#simpleEffortRow [data-simple-effort]');
      if (effortVisible) await tap('#simpleEffortRow [data-simple-effort]:first-child');
      await tap('#simpleStartBtn');
      await page.waitForFunction(() => {
        const el = document.querySelector('#simpleComposerInput');
        return !el || !el.value || el.value.trim() === ''
          || document.querySelector('#simpleHome [data-simple-toast]')
          || !document.querySelector('#simpleHome');
      }, { timeout: 5000 }).catch(() => {});
      if (!intercepted.spawn) throw new Error('no spawn POST was sent');
      const p = intercepted.spawn;
      if (!p.prompt || !String(p.prompt).includes('Water the plants')) throw new Error('spawn payload missing prompt');
      if (!p.engine) throw new Error('spawn payload missing engine');
      const modelNote = p.model ? `engine=${p.engine} model=${p.model} effort=${p.reasoning_effort || p.effort || 'default'}` : `engine=${p.engine}`;
      record('1. Start a new job (agent/model/effort)', n, n <= MAX_CLICKS ? 'PASS' : 'FAIL', modelNote);
    } catch (e) {
      record('1. Start a new job (agent/model/effort)', n, 'FAIL', e.message);
    }

    // ── Task 2: answer a worker that is waiting for input ──
    await goHome().catch(() => {});
    n = 0;
    try {
      const hasCard = await visible('#simpleNeedsYou .simple-nya-card');
      if (!hasCard) {
        record('2. Answer a waiting worker', null, 'SKIP', 'no worker waiting right now (live data dependent)');
      } else {
        const optSel = '#simpleNeedsYou .simple-nya-card [data-answer-option]';
        if (await visible(optSel)) {
          await tap(optSel); // one tap on a suggested answer
        } else {
          await tap('#simpleNeedsYou .simple-nya-card .simple-nya-answer-input');
          await page.type('#simpleNeedsYou .simple-nya-card .simple-nya-answer-input', 'Yes, go ahead');
          await tap('#simpleNeedsYou .simple-nya-card [data-answer-send]');
        }
        await new Promise((r) => setTimeout(r, 600));
        if (!intercepted.answer) throw new Error('no answer POST was sent');
        record('2. Answer a waiting worker', n, n <= MAX_CLICKS ? 'PASS' : 'FAIL');
      }
    } catch (e) {
      record('2. Answer a waiting worker', n, 'FAIL', e.message);
    }

    // ── Task 3: see what's running right now + status ──
    await goHome().catch(() => {});
    n = 0;
    try {
      const statusSel = '#simpleTasks .simple-task-card.is-running .simple-status-line';
      const has = await visible(statusSel);
      if (!has) {
        record('3. See what is running + status', 0, 'SKIP', 'nothing running right now (live data dependent)');
      } else {
        const t = await textOf(statusSel);
        if (!t) throw new Error('status line is empty');
        record('3. See what is running + status', 0, 'PASS', `visible on home: "${t.slice(0, 60)}"`);
      }
    } catch (e) {
      record('3. See what is running + status', 0, 'FAIL', e.message);
    }

    // ── Task 4: find a past/finished task and open it ──
    await goHome().catch(() => {});
    n = 0;
    let openedConv = false;
    try {
      const finishedSel = '#simpleTasks .simple-task-card:not(.is-running)';
      if (await visible(finishedSel)) {
        await tap(finishedSel);
        // A background home refresh can swap the card node mid-tap (or a
        // transient toast can eat it); a real user would tap again.
        try {
          await page.waitForSelector('#convSplit', { visible: true, timeout: 8000 });
        } catch (_) {
          await goHome().catch(() => {});
          await tap(finishedSel);
          await page.waitForSelector('#convSplit', { visible: true, timeout: 8000 });
        }
      } else if (await visible('#simpleSeeAllFinished')) {
        await tap('#simpleSeeAllFinished');
        await page.waitForSelector('#simpleHistory:not([hidden])', { visible: true, timeout: 8000 });
        // History fetches/renders every conversation (hundreds on a live
        // install); give it more room than the default 8s before treating a
        // slow-but-working render as a failure.
        await tap('#simpleHistoryList .simple-task-card', { timeout: 15000 });
      } else {
        throw new Error('no finished section and no see-all link');
      }
      await page.waitForSelector('#convSplit', { visible: true, timeout: 8000 });
      openedConv = true;
      record('4. Find a past task and open it', n, n <= MAX_CLICKS ? 'PASS' : 'FAIL');
    } catch (e) {
      record('4. Find a past task and open it', n, 'FAIL', e.message);
    }

    // ── Task 5: token usage / context in plain language (per conversation) ──
    n = 0;
    try {
      if (!openedConv) {
        await goHome();
        await tap('#simpleTasks .simple-task-card');
        await page.waitForSelector('#convSplit', { visible: true, timeout: 8000 });
      }
      // already inside a conversation; usage line must be visible there
      await page.waitForSelector('#simpleUsageLine', { visible: true, timeout: 8000 });
      const t = await textOf('#simpleUsageLine');
      if (!/%/.test(t)) throw new Error('usage line has no percentage: ' + t.slice(0, 60));
      record('5. Usage/context in plain language', n, n <= MAX_CLICKS ? 'PASS' : 'FAIL', `"${t.slice(0, 70)}"`);
    } catch (e) {
      record('5. Usage/context in plain language', n, 'FAIL', e.message);
    }

    // ── Task 6: read a conversation without leaving the simple UI ──
    // Depth-2 contract: the open conversation is a SIMPLE chat surface —
    // simple header (back + plain title + usage line), chat bubbles, labeled
    // Send — and every piece of advanced chrome is hidden.
    n = 0;
    try {
      const inConv = await visible('#convSplit');
      if (!inConv) {
        await goHome();
        await tap('#simpleTasks .simple-task-card');
        await page.waitForSelector('#convSplit', { visible: true, timeout: 8000 });
      }
      const stillSimple = await page.evaluate(() => document.body.classList.contains('ccc-simple-mode'));
      if (!stillSimple) throw new Error('conversation opened outside Simple mode');
      const hasMessages = await page.evaluate(() => {
        const el = document.querySelector('#convSplit');
        return el && el.textContent.trim().length > 40;
      });
      if (!hasMessages) throw new Error('conversation pane rendered empty');
      const convOpenClass = await page.evaluate(() =>
        document.body.classList.contains('ccc-simple-conv-open'));
      if (!convOpenClass) throw new Error('body.ccc-simple-conv-open not set');
      if (!(await visible('#simpleBackHomeBtn'))) throw new Error('back-to-home button not visible');
      if (!(await visible('#simpleConvTitle'))) throw new Error('plain-language title not visible');
      // The composer re-renders asynchronously after a conversation switch
      // (updateInputBar runs off the poll loop) — wait for it to settle.
      await page.waitForSelector('#convSendBtn', { visible: true, timeout: 8000 })
        .catch(() => { throw new Error('simple Send button not visible'); });
      const advancedVisible = await page.evaluate(() => {
        const sels = ['#statusRail', '.conv-pane-header', '#convInputContext',
          '#convEscBtn', '#convCompactBtn', '#convSteerBtn', '#convSubmitPlusBtn',
          '.conv-presentation-toolbar'];
        return sels.filter((s) => {
          const el = document.querySelector(s);
          if (!el) return false;
          const r = el.getBoundingClientRect();
          return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
        });
      });
      if (advancedVisible.length) {
        throw new Error('advanced chrome still visible: ' + advancedVisible.join(', '));
      }
      const toolNoise = await page.evaluate(() => {
        const el = document.querySelector('#conversationsView .tool-call-group, #conversationsView .event.system');
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && getComputedStyle(el).display !== 'none';
      });
      if (toolNoise) throw new Error('tool-call/system noise visible in the simple transcript');
      record('6. Read a conversation in the simple UI', n, n <= MAX_CLICKS ? 'PASS' : 'FAIL');
    } catch (e) {
      record('6. Read a conversation in the simple UI', n, 'FAIL', e.message);
    }

    // ── Task 7: further capabilities, each ≤3 clicks ──
    const extras = [];
    // 7a. Back home from a conversation
    n = 0;
    try {
      await goHome();
      if (!(await visible('#simpleHome'))) throw new Error('did not land back on home');
      extras.push({ task: '7a. Back to home', clicks: n, status: n <= MAX_CLICKS ? 'PASS' : 'FAIL', note: '' });
    } catch (e) { extras.push({ task: '7a. Back to home', clicks: n, status: 'FAIL', note: e.message }); }
    // 7b. Open Automations (depth-3 simple screen; a queue card, when any
    // exist, drills into the automation detail — still ≤3 clicks)
    n = 0;
    try {
      await tap('[data-mobile-nav="automations"]');
      await page.waitForSelector('#simpleAutomations:not([hidden])', { visible: true, timeout: 8000 });
      const queueCard = '#simpleAutomationsList [data-simple-queue]';
      if (await visible(queueCard)) {
        await tap(queueCard);
        await page.waitForSelector('#simpleAutomationDetail:not([hidden])', { visible: true, timeout: 8000 });
        const body = await textOf('#simpleAutomationDetailBody');
        if (!body) throw new Error('automation detail rendered empty');
      }
      extras.push({ task: '7b. Open Automations', clicks: n, status: n <= MAX_CLICKS ? 'PASS' : 'FAIL', note: '' });
    } catch (e) { extras.push({ task: '7b. Open Automations', clicks: n, status: 'FAIL', note: e.message }); }
    // 7c. Open Settings ("More") — the simple settings screen, not the
    // advanced settings modal
    n = 0;
    try {
      await tap('[data-mobile-nav="more"]');
      await page.waitForSelector('#simpleSettings:not([hidden])', { visible: true, timeout: 8000 });
      if (!(await visible('#simpleAdvancedBtn'))) throw new Error('advanced-mode switch not visible');
      if (!(await visible('#simpleThemeRow [data-simple-theme]'))) throw new Error('theme chips not visible');
      extras.push({ task: '7c. Open settings', clicks: n, status: n <= MAX_CLICKS ? 'PASS' : 'FAIL', note: '' });
    } catch (e) { extras.push({ task: '7c. Open settings', clicks: n, status: 'FAIL', note: e.message }); }
    // 7d. Search past work — the header search button opens the simple
    // history screen; typing live-filters; tapping a card opens the simple
    // conversation view
    n = 0;
    try {
      await goHome().catch(() => {});
      await tap('#mshSearchBtn');
      await page.waitForSelector('#simpleHistory:not([hidden])', { visible: true, timeout: 8000 });
      await page.waitForSelector('#simpleHistorySearch', { visible: true, timeout: 8000 });
      const histCard = '#simpleHistoryList .simple-task-card';
      await page.waitForSelector(histCard, { visible: true, timeout: 8000 });
      const before = await page.$$eval(histCard, (els) => els.length);
      await page.type('#simpleHistorySearch', 'zzz-no-such-task');
      await page.waitForFunction((sel, n0) => document.querySelectorAll(sel).length !== n0,
        { timeout: 4000 }, histCard, before).catch(() => {
          throw new Error('typing in history search did not filter the list');
        });
      await page.evaluate(() => {
        const i = document.getElementById('simpleHistorySearch');
        if (i) { i.value = ''; i.dispatchEvent(new Event('input', { bubbles: true })); }
      });
      await page.waitForSelector(histCard, { visible: true, timeout: 8000 });
      await tap(histCard);
      await page.waitForSelector('#convSplit', { visible: true, timeout: 8000 });
      const convOpen = await page.evaluate(() => document.body.classList.contains('ccc-simple-conv-open'));
      if (!convOpen) throw new Error('history card did not open the simple conversation view');
      extras.push({ task: '7d. Search past work', clicks: n, status: n <= MAX_CLICKS ? 'PASS' : 'FAIL', note: '' });
    } catch (e) { extras.push({ task: '7d. Search past work', clicks: n, status: 'FAIL', note: e.message }); }

    // 7e. The What's New modal must never cover the home screen in Simple
    //     mode — checked on a fresh page WITHOUT the seen-version marker.
    try {
      const page2 = await browser.newPage();
      await page2.setViewport({ width: 1000, height: 800 });
      await page2.evaluateOnNewDocument(() => {
        try {
          // Simulate a fresh install: wipe everything page 1 stored (last
          // conversation restore keys etc.), then opt into Simple mode only.
          localStorage.clear();
          localStorage.setItem('ccc-ui-mode', 'simple');
          localStorage.setItem('ccc-tour-done', '1');
        } catch (_) {}
      });
      await page2.goto(url, { waitUntil: 'load', timeout: 60000 });
      await new Promise((r) => setTimeout(r, 2500)); // let /api/version resolve
      const modalOpen = await page2.evaluate(() => {
        const m = document.getElementById('whatsNewModal');
        return !!(m && m.classList.contains('open'));
      });
      const homeVisible = await page2.evaluate(() => {
        const el = document.getElementById('simpleHome');
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      await page2.close();
      if (modalOpen) throw new Error("What's New modal opened in Simple mode");
      if (!homeVisible) throw new Error('home screen not visible on fresh load');
      extras.push({ task: "7e. What's New stays closed in Simple mode", clicks: 0, status: 'PASS', note: '' });
    } catch (e) { extras.push({ task: "7e. What's New stays closed in Simple mode", clicks: 0, status: 'FAIL', note: e.message }); }

    for (const x of extras) record(x.task, x.clicks, x.status, x.note);
  } finally {
    await browser.close();
  }

  // ── Summary ──
  const fails = results.filter((r) => r.status === 'FAIL');
  const skips = results.filter((r) => r.status === 'SKIP');
  console.log('\n────────────────────────────────────────');
  for (const r of results) {
    console.log(`${r.status.padEnd(4)}  ${String(r.clicks === null ? '-' : r.clicks).padStart(2)} clicks  ${r.task}`);
  }
  console.log('────────────────────────────────────────');
  console.log(`${results.length - fails.length - skips.length} pass, ${fails.length} fail, ${skips.length} skip (max ${MAX_CLICKS} clicks)`);

  try {
    fs.mkdirSync(path.join(__dirname, 'scratch'), { recursive: true });
    fs.writeFileSync(path.join(__dirname, 'scratch', 'simple-ui-clicks-results.json'),
      JSON.stringify({ when: new Date().toISOString(), url, maxClicks: MAX_CLICKS, results }, null, 2));
  } catch (_) {}

  process.exit(fails.length ? 1 : 0);
})().catch((e) => { console.error('[simple-ui-clicks] fatal:', e); process.exit(2); });
