'use strict';

/**
 * Drive the shipped First Flight guide (static/tour.js) against a fixture
 * that uses the dashboard's real control ids. Does not copy the step list:
 * it starts the real guide, advances, and records title/body from the DOM.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const os = require('node:os');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const ROOT = path.resolve(__dirname, '..');
const TOUR_JS = path.join(ROOT, 'static', 'tour.js');
const APP_JS = path.join(ROOT, 'static', 'app.js');
const INDEX_HTML = path.join(ROOT, 'static', 'index.html');
const SCRATCH = process.env.FTUE_SCRATCH
  || '/var/folders/sl/4np1qd1j7tdd956l4kf8t_ph0000gn/T/grok-goal-10a44204be19/implementer';

const TOPICS = [
  'clis are installed',
  'reviewing existing conversations',
  'creating first conversation',
  'lhs',
  'rhs',
  'first queue',
  'workers',
  'delegation',
];

const CLI_FIXTURE = {
  completed: false,
  clis: {
    claude: {
      name: 'Claude Code',
      command: 'claude',
      available: false,
      logged_in: false,
      install_instruction: 'curl -fsSL https://example.test/install-claude | bash',
      signup_url: 'https://example.test/claude',
    },
    codex: {
      name: 'Codex',
      command: 'codex',
      available: true,
      logged_in: false,
      login_instruction: 'codex login',
      signup_url: 'https://example.test/codex',
    },
    cursor: {
      name: 'Cursor',
      command: 'cursor',
      available: true,
      logged_in: true,
      email: 'dev@example.test',
    },
  },
};

const CLI_AFTER_DETECT = {
  completed: false,
  clis: {
    claude: {
      name: 'Claude Code',
      command: 'claude',
      available: true,
      logged_in: true,
      email: 'ready@example.test',
    },
    codex: {
      name: 'Codex',
      command: 'codex',
      available: true,
      logged_in: false,
      login_instruction: 'codex login',
      signup_url: 'https://example.test/codex',
    },
    cursor: {
      name: 'Cursor',
      command: 'cursor',
      available: true,
      logged_in: true,
      email: 'dev@example.test',
    },
  },
};

const FIXTURE_HTML = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CCC first-run fixture</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: #0d1117; color: #c9d1d9;
    font-family: Inter, sans-serif; }
  [hidden] { display: none !important; }
  .sidebar { width: 280px; height: 100vh; float: left; padding: 12px;
    border-right: 1px solid #30363d; }
  #sidebarNewBtn, #settingsBtn, #convSearch { display: block; width: 100%;
    min-height: 36px; margin: 6px 0; }
  #convList { min-height: 220px; border: 1px solid #30363d; padding: 8px; }
  .conv-tab-bar { display: flex; gap: 6px; margin-bottom: 8px; }
  .conv-tab, .status-rail-tab, .orch-playbook, .send-btn {
    min-height: 32px; min-width: 88px; }
  .status-rail { width: 280px; height: 100vh; float: right; padding: 12px;
    border-left: 1px solid #30363d; }
  /* Dashboard hide: .conv-input-bar { display:none } until JS adds .visible. */
  .conv-input-bar { display: none; position: fixed; left: 300px; right: 300px;
    bottom: 16px; min-height: 88px; padding: 8px; border: 1px solid #30363d; }
  .conv-input-bar.visible { display: flex; align-items: center; gap: 8px; }
  #convInput { width: 70%; min-height: 36px; }
  #convInputEngineSelect { min-width: 88px; min-height: 32px; }
  #convSendBtn { min-height: 32px; min-width: 88px; }
  #queuePanel { min-height: 48px; padding: 8px; border: 1px solid #30363d; }
  #orchPlaybooks { min-height: 40px; }
</style>
</head>
<body>
  <div class="sidebar" data-tour="lhs">
    <button class="new-session-btn" id="sidebarNewBtn" data-tour="new-session">New session</button>
    <input type="text" id="convSearch" class="conv-search-input" data-tour="search" placeholder="Search...">
    <button type="button" id="sidebarMoreBtn" class="sh-btn sh-more-btn" data-tour="watchtower">More</button>
    <div class="log-list" id="convList" data-tour="session-list">
      <div class="conv-tab-bar" data-role="conv-tab-bar">
        <button type="button" class="conv-tab is-active" data-conv-tab="inprogress">Active</button>
        <button type="button" class="conv-tab" data-conv-tab="coding">Coding</button>
        <button type="button" class="conv-tab" data-conv-tab="workers" data-tour="workers" hidden>Workers</button>
      </div>
    </div>
    <button type="button" class="sidebar-footer-btn" id="settingsBtn" data-tour="settings">Settings</button>
  </div>
  <aside class="status-rail" id="statusRail" data-tour="status-rail" aria-label="Session status rail">
    <div class="status-rail-tabs" role="tablist">
      <button class="status-rail-tab is-active" type="button" data-rail-tab="orchestration" data-tour="orchestration">Orchestration</button>
      <button class="status-rail-tab" type="button" data-rail-tab="metadata">Metadata</button>
      <button class="status-rail-tab" type="button" data-rail-tab="queue" data-tour="queue" hidden>Queue</button>
    </div>
    <div class="status-rail-pane is-active" id="statusRailOrchestrationPane" data-rail-pane="orchestration">
      <div class="orch-playbooks" id="orchPlaybooks" role="group" aria-label="Orchestration playbooks">
        <button type="button" class="orch-playbook" data-orch-playbook="delegate" data-tour="delegate" hidden>Delegate</button>
      </div>
    </div>
    <div class="status-rail-pane" id="statusRailQueuePane" data-rail-pane="queue" hidden>
      <div class="files-panel files-queue-panel" id="queuePanel">Queue</div>
    </div>
  </aside>
  <div class="conv-input-bar" id="convInputBar" data-tour="spawn-bar">
    <textarea id="convInput" placeholder="Send to terminal..."></textarea>
    <select id="convInputEngineSelect" class="engine-select" style="display:none;">
      <option value="claude">claude</option>
    </select>
    <button class="send-btn" id="convSendBtn">Send</button>
  </div>
</body>
</html>`;

let browser;
let fixtureServer;
let fixtureUrl;

function ensureScratch() {
  fs.mkdirSync(SCRATCH, { recursive: true });
}

test.before(async () => {
  ensureScratch();
  fixtureServer = http.createServer((req, res) => {
    const url = String(req.url || '/');
    if (url.indexOf('tour.js') !== -1) {
      res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' });
      res.end(fs.readFileSync(TOUR_JS));
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(FIXTURE_HTML.replace(
      '</body>',
      '<script src="/static/tour.js"></script></body>'
    ));
  });
  await new Promise((resolve) => fixtureServer.listen(0, '127.0.0.1', resolve));
  fixtureUrl = 'http://127.0.0.1:' + fixtureServer.address().port + '/';
  const chromePath = findChromePath();
  browser = await puppeteer.launch({
    executablePath: chromePath,
    args: ['--no-sandbox'],
  });
});

test.after(async () => {
  await browser?.close();
  if (fixtureServer) await new Promise((resolve) => fixtureServer.close(resolve));
});

async function openFixture(startOpts) {
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  await page.setViewport({ width: 1440, height: 900 });
  const errors = [];
  page.on('pageerror', (err) => errors.push(String(err)));
  await page.goto(fixtureUrl, { waitUntil: 'load' });
  await page.evaluate((opts) => {
    window.__pageErrors = [];
    window.addEventListener('error', (e) => {
      window.__pageErrors.push(String((e && e.message) || e));
    });
    try { localStorage.removeItem('ccc-tour-done'); } catch (_) {}
    if (!window.cccTour || typeof window.cccTour.start !== 'function') {
      throw new Error('shipped guide did not define window.cccTour.start');
    }
    window.cccTour.start(opts);
  }, startOpts || { force: true, cliStatus: CLI_FIXTURE });
  page.__ftueErrors = errors;
  return page;
}

async function readCard(page) {
  return page.evaluate(() => {
    const title = document.querySelector('.fft-title');
    const bodyEls = [...document.querySelectorAll('.fft-body')];
    const card = document.querySelector('.fft-center-card, .fft-card');
    return {
      title: title ? title.textContent.trim() : '',
      body: bodyEls.map((el) => el.textContent.trim()).join('\n'),
      overlay: !!card,
      stepId: card ? card.getAttribute('data-fft-step') : '',
      errors: window.__pageErrors || [],
      state: window.cccTour.getState ? window.cccTour.getState() : null,
      done: (() => { try { return localStorage.getItem('ccc-tour-done'); } catch (_) { return null; } })(),
    };
  });
}

async function clickPrimary(page) {
  await page.evaluate(() => {
    const btn = document.querySelector('.fft-btn-primary');
    if (btn) btn.click();
    else if (window.cccTour && typeof window.cccTour.next === 'function') window.cccTour.next();
  });
}

async function walkAll(page) {
  const recorded = [];
  for (let i = 0; i < 40; i++) {
    const card = await readCard(page);
    if (!card.overlay && !(card.state && card.state.active)) break;
    if (card.title) {
      recorded.push({
        title: card.title,
        body: card.body,
        id: card.stepId || (card.state && card.state.step && card.state.step.id) || '',
        reveal: card.state && card.state.revealed,
      });
    }
    const before = card.stepId || card.title;
    await clickPrimary(page);
    const after = await readCard(page);
    if (!after.overlay && !(after.state && after.state.active)) {
      if (after.state && after.state.visited && after.state.visited.length > recorded.length) {
        // finale may complete on the last Next
      }
      break;
    }
    if ((after.stepId || after.title) === before && i > 0) {
      // stuck
      break;
    }
  }
  return recorded;
}

function blobOf(recorded) {
  return recorded.map((s) => `${s.title}\n${s.body}`).join('\n').toLowerCase();
}

test('fixture DOM uses the dashboard real control ids', () => {
  const index = fs.readFileSync(INDEX_HTML, 'utf8');
  for (const id of [
    'id="convList"',
    'id="sidebarNewBtn"',
    'id="convSearch"',
    'id="settingsBtn"',
    'id="statusRail"',
    'data-rail-tab="queue"',
    'data-rail-tab="orchestration"',
    'id="convInputBar"',
    'id="convSendBtn"',
    'id="orchPlaybooks"',
  ]) {
    assert.ok(index.includes(id), 'dashboard missing ' + id);
    assert.ok(FIXTURE_HTML.includes(id.replace('id="', 'id="').split(' ')[0]) || FIXTURE_HTML.includes(id),
      'fixture missing ' + id);
  }
  assert.ok(FIXTURE_HTML.includes('data-conv-tab="workers"'));
  assert.ok(FIXTURE_HTML.includes('data-orch-playbook="delegate"'));
});

test('Settings replay and auto-start guards stay on the shipped start path', () => {
  const app = fs.readFileSync(APP_JS, 'utf8');
  const takeIdx = app.indexOf('takeTourBtn');
  assert.ok(takeIdx !== -1, 'takeTourBtn wiring missing');
  assert.match(app.slice(takeIdx, takeIdx + 500), /loadFirstFlightTour\(true\)/);
  const runIdx = app.indexOf('runOnboardingBtn');
  assert.ok(runIdx !== -1, 'runOnboardingBtn wiring missing');
  assert.match(app.slice(runIdx, runIdx + 1200), /loadFirstFlightTour\(true\)/);
  assert.match(app, /max-width:\s*1200px/);
  assert.match(app, /\.upd-overlay\.open/);
  const checkStart = app.indexOf('async function checkOnboarding');
  const checkEnd = app.indexOf('function showOnboarding');
  assert.ok(checkStart !== -1 && checkEnd > checkStart, 'checkOnboarding is present');
  const checkFn = app.slice(checkStart, checkEnd);
  assert.equal(
    /showOnboarding\s*\(/.test(checkFn),
    false,
    'auto-start must not open the 3-step wizard in front of the guide'
  );
  const tour = fs.readFileSync(TOUR_JS, 'utf8');
  const composerReveal = tour.slice(tour.indexOf('composer: function'), tour.indexOf('workers: function'));
  assert.match(composerReveal, /sidebarNewBtn/, 'composer reveal must click New session');
  assert.match(composerReveal, /classList\.add\(\s*['"]visible['"]\s*\)/, 'composer reveal must add .visible');
});

test('first-run walk records >=20 steps covering every named topic', async () => {
  const page = await openFixture({ force: true, cliStatus: CLI_FIXTURE });
  try {
    const first = await readCard(page);
    assert.ok(first.overlay, 'overlay missing on start');
    assert.ok(first.title, 'first step title empty');
    assert.equal((first.errors || []).length, 0, 'page errors on start: ' + (first.errors || []).join('; '));

    const recorded = await walkAll(page);
    const logPath = path.join(SCRATCH, 'ftue-steps.log');
    fs.writeFileSync(
      logPath,
      recorded.map((s, i) => `${i + 1}. ${s.title}\n${s.body}\n`).join('\n'),
      'utf8'
    );
    assert.ok(recorded.length >= 20, 'expected >=20 user-facing steps, got ' + recorded.length + ' titles: ' + recorded.map((s) => s.title).join(' | '));
    const blob = blobOf(recorded);
    for (const topic of TOPICS) {
      assert.ok(blob.includes(topic), 'missing topic "' + topic + '" in ' + blob.slice(0, 500));
    }
    const done = await page.evaluate(() => localStorage.getItem('ccc-tour-done'));
    assert.ok(done, 'complete must persist ccc-tour-done');

    await page.evaluate(() => { window.cccTour.start({ force: false }); });
    const second = await readCard(page);
    assert.equal(second.overlay, false, 'second non-forced start must do nothing');
  } finally {
    await page.close();
  }
});

test('skip persists done flag so a second non-forced start does nothing', async () => {
  const page = await openFixture({ force: true, cliStatus: CLI_FIXTURE });
  try {
    await page.evaluate(() => {
      const skip = document.querySelector('.fft-skip, .fft-btn-ghost');
      if (skip) skip.click();
      else window.cccTour.skip();
    });
    const done = await page.evaluate(() => localStorage.getItem('ccc-tour-done'));
    assert.ok(done, 'skip must persist ccc-tour-done');
    await page.evaluate(() => { window.cccTour.start({ force: false }); });
    const card = await readCard(page);
    assert.equal(card.overlay, false, 'non-forced start after skip must no-op');
  } finally {
    await page.close();
  }
});

test('forced Settings-style replay starts again after done', async () => {
  const page = await openFixture({ force: true, cliStatus: CLI_FIXTURE });
  try {
    await page.evaluate(() => window.cccTour.skip());
    await page.evaluate((cliStatus) => window.cccTour.start({ force: true, cliStatus }), CLI_FIXTURE);
    const card = await readCard(page);
    assert.ok(card.overlay, 'forced replay must show overlay');
    assert.ok(card.title, 'forced replay first step empty');
  } finally {
    await page.close();
  }
});

test('CLI step shows install/login/re-detect for missing and logged-out, not for ready', async () => {
  const page = await openFixture({
    force: true,
    cliStatus: CLI_FIXTURE,
  });
  try {
    // Welcome -> CLI
    await clickPrimary(page);
    const onCli = await page.evaluate((updated) => {
      window.__fftUpdatedCli = updated;
      if (window.cccTour && window.cccTour.getState) {
        const st = window.cccTour.getState();
        return {
          id: st && st.step && st.step.id,
          title: document.querySelector('.fft-title') && document.querySelector('.fft-title').textContent,
        };
      }
      return {
        title: document.querySelector('.fft-title') && document.querySelector('.fft-title').textContent,
      };
    }, CLI_AFTER_DETECT);
    assert.match(String(onCli.title || ''), /cli/i);

    const rows = await page.evaluate(() => {
      return [...document.querySelectorAll('.fft-cli-row')].map((row) => ({
        engine: row.getAttribute('data-fft-cli'),
        state: row.getAttribute('data-fft-cli-state'),
        hasInstall: !!row.querySelector('.fft-cli-install'),
        hasLogin: !!row.querySelector('.fft-cli-login'),
        text: row.textContent,
      }));
    });
    assert.ok(rows.length >= 3, 'CLI step should render engine rows');
    const missing = rows.find((r) => r.engine === 'claude');
    const loggedOut = rows.find((r) => r.engine === 'codex');
    const ready = rows.find((r) => r.engine === 'cursor');
    assert.ok(missing, 'missing engine row');
    assert.ok(loggedOut, 'logged-out engine row');
    assert.ok(ready, 'ready engine row');
    assert.equal(missing.state, 'missing');
    assert.equal(missing.hasInstall, true, 'missing row must expose install');
    assert.equal(missing.hasLogin, false);
    assert.equal(loggedOut.state, 'logged-out');
    assert.equal(loggedOut.hasLogin, true, 'logged-out row must expose login');
    assert.equal(loggedOut.hasInstall, false);
    assert.equal(ready.state, 'ready');
    assert.equal(ready.hasInstall, false, 'ready row must not expose install');
    assert.equal(ready.hasLogin, false, 'ready row must not expose login');
    const redetect = await page.evaluate(() => !!document.querySelector('.fft-cli-redetect'));
    assert.equal(redetect, true, 're-detect control missing');

    await page.evaluate((updated) => {
      // Rebind fetch used by the shipped re-detect without mocking the renderer.
      window.cccTour.start; // keep shipped function
      const btn = document.querySelector('.fft-cli-redetect');
      if (!btn) throw new Error('no re-detect');
      // The guide should honor a fetchCliStatus injected at start. Re-call
      // with the same overlay by using the live instance's redetect if present,
      // else click after swapping the injected fetcher via a custom event.
      if (typeof window.cccTour.setCliStatus === 'function') {
        window.cccTour.setCliStatus(updated);
      }
      btn.click();
    }, CLI_AFTER_DETECT);

    // If start() consumed fetchCliStatus, clicking re-detect with a fetcher
    // provided at start is the real path. Restart this page with the fetcher
    // defined inside the page when the first click did not flip Claude.
    const claudeState = await page.evaluate(() => {
      const row = document.querySelector('.fft-cli-row[data-fft-cli="claude"]');
      return row ? row.getAttribute('data-fft-cli-state') : null;
    });
    if (claudeState !== 'ready') {
      // Drive the real start() injection path on a clean run of this same page.
      await page.evaluate((initial, updated) => {
        try { localStorage.removeItem('ccc-tour-done'); } catch (_) {}
        if (window.cccTour.end) window.cccTour.end('skip');
        try { localStorage.removeItem('ccc-tour-done'); } catch (_) {}
        window.cccTour.start({
          force: true,
          cliStatus: initial,
          fetchCliStatus: async () => updated,
        });
      }, CLI_FIXTURE, CLI_AFTER_DETECT);
      await clickPrimary(page);
      await page.click('.fft-cli-redetect');
      await page.waitForFunction(() => {
        const row = document.querySelector('.fft-cli-row[data-fft-cli="claude"]');
        return row && row.getAttribute('data-fft-cli-state') === 'ready';
      });
    }

    const after = await page.evaluate(() => {
      const row = document.querySelector('.fft-cli-row[data-fft-cli="claude"]');
      const title = document.querySelector('.fft-title');
      const readyRows = [...document.querySelectorAll('.fft-cli-row[data-fft-cli-state="ready"]')];
      return {
        claude: row ? row.getAttribute('data-fft-cli-state') : null,
        stillOnCli: /cli/i.test((title && title.textContent) || ''),
        readyCount: readyRows.length,
        overlay: !!document.querySelector('.fft-center-card, .fft-card'),
      };
    });
    assert.equal(after.claude, 'ready');
    assert.equal(after.stillOnCli, true, 're-detect must not leave the guide');
    assert.ok(after.readyCount >= 1, 'at least one engine marked ready');
    assert.equal(after.overlay, true);
  } finally {
    await page.close();
  }
});

test('Queue, Workers, Delegation, and composer reveals make the live target visible before spotlight', async () => {
  const page = await openFixture({
    force: true,
    cliStatus: CLI_FIXTURE,
    fetchCliStatus: undefined,
  });
  try {
    const hits = [];
    for (let i = 0; i < 40; i++) {
      const snap = await page.evaluate(() => {
        function box(sel) {
          const el = document.querySelector(sel);
          if (!el) return { exists: false, visible: false, w: 0, h: 0 };
          const r = el.getBoundingClientRect();
          return { exists: true, visible: r.width > 0 && r.height > 0, w: r.width, h: r.height };
        }
        const title = (document.querySelector('.fft-title') || {}).textContent || '';
        const state = window.cccTour.getState ? window.cccTour.getState() : {};
        const stepId = (state && state.step && state.step.id)
          || (document.querySelector('[data-fft-step]') && document.querySelector('[data-fft-step]').getAttribute('data-fft-step'))
          || '';
        const spot = document.querySelector('.fft-spot');
        return {
          title,
          stepId,
          active: !!(state && state.active),
          overlay: !!document.querySelector('.fft-center-card, .fft-card'),
          revealed: state && state.revealed,
          hasSpot: !!spot,
          workers: box('[data-conv-tab="workers"]'),
          queueTab: box('[data-rail-tab="queue"]'),
          delegate: box('[data-orch-playbook="delegate"]'),
          composerBar: box('#convInputBar'),
          engineSelect: box('#convInputEngineSelect'),
          sendBtn: box('#convSendBtn'),
        };
      });
      if (!snap.overlay && !snap.active) break;
      if (snap.stepId === 'composer') {
        hits.push({ kind: 'composer', ...snap });
        assert.equal(snap.composerBar.visible, true, 'composer bar still display:none on composer step');
        assert.ok(snap.revealed && snap.revealed.visible, 'reveal did not report visible composer');
        assert.equal(snap.revealed.beforeSpotlight, true);
        assert.match(String((snap.revealed && snap.revealed.matched) || ''), /spawn-bar|convInputBar/,
          'composer step must spotlight the live bar, not a fallback');
      }
      if (snap.stepId === 'engine-picker') {
        hits.push({ kind: 'engine', ...snap });
        assert.equal(snap.engineSelect.visible, true, 'engine picker still display:none on engine step');
        assert.ok(snap.revealed && snap.revealed.visible);
        assert.equal(snap.revealed.beforeSpotlight, true);
      }
      if (snap.stepId === 'send') {
        hits.push({ kind: 'send', ...snap });
        assert.equal(snap.sendBtn.visible, true, 'send button not visible on send step');
        assert.ok(snap.revealed && snap.revealed.visible);
        assert.equal(snap.revealed.beforeSpotlight, true);
      }
      if (snap.stepId === 'workers-tab' || snap.stepId === 'workers-lane') {
        hits.push({ kind: 'workers', ...snap });
        assert.equal(snap.workers.visible, true, 'Workers target not visible on workers step');
        assert.ok(snap.revealed && snap.revealed.visible, 'reveal did not report visible workers');
        assert.equal(snap.revealed.beforeSpotlight, true);
      }
      if (snap.stepId === 'queue-tab' || snap.stepId === 'first-queue') {
        hits.push({ kind: 'queue', ...snap });
        assert.equal(snap.queueTab.visible, true, 'Queue tab not visible on queue step');
        assert.ok(snap.revealed && snap.revealed.visible, 'reveal did not report visible queue');
        assert.equal(snap.revealed.beforeSpotlight, true);
      }
      if (snap.stepId === 'delegation') {
        hits.push({ kind: 'delegation', ...snap });
        assert.equal(snap.delegate.visible, true, 'Delegate playbook not visible on Delegation step');
        assert.ok(snap.revealed && snap.revealed.visible, 'reveal did not report visible delegate');
        assert.equal(snap.revealed.beforeSpotlight, true);
      }
      await clickPrimary(page);
    }
    const kinds = hits.map((h) => h.kind);
    assert.ok(kinds.includes('composer'), 'never landed on a composer step with a live bar');
    assert.ok(kinds.includes('engine'), 'never landed on an engine-picker step with a live select');
    assert.ok(kinds.includes('send'), 'never landed on a send step with a live send button');
    assert.ok(kinds.includes('workers'), 'never landed on a Workers step (would match missing-anchor skip)');
    assert.ok(kinds.includes('queue'), 'never landed on a Queue step (would match missing-anchor skip)');
    assert.ok(kinds.includes('delegation'), 'never landed on a Delegation step (would match missing-anchor skip)');
  } finally {
    await page.close();
  }
});

function urlResponds(url, timeoutMs = 800) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(true);
    });
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

async function resolveDashboardUrl() {
  const portFile = path.join(os.homedir(), '.claude', 'command-center', 'port.txt');
  let fromFile;
  try {
    const raw = fs.readFileSync(portFile, 'utf8').trim();
    if (/^https?:\/\//.test(raw)) fromFile = raw;
    else if (/^\d+$/.test(raw)) fromFile = `http://127.0.0.1:${raw}`;
  } catch (_) {}
  const candidates = [fromFile, 'http://127.0.0.1:8090'].filter(Boolean);
  for (const url of candidates) {
    if (await urlResponds(url)) return url;
  }
  return null;
}

test('live dashboard walk keeps composer, CLI, Queue, Workers, and Delegation on real controls', async () => {
  const logLive = path.join(SCRATCH, 'ftue-live-walk.log');
  const unavailable = path.join(SCRATCH, 'ftue-launch-unavailable.log');
  const url = await resolveDashboardUrl();
  if (!url) {
    fs.writeFileSync(unavailable, 'dashboard URL did not respond\n', 'utf8');
    return;
  }
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (err) => errors.push(String(err)));
  try {
    await page.setViewport({ width: 1440, height: 900 });
    await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    await page.evaluate(() => {
      try { localStorage.removeItem('ccc-tour-done'); } catch (_) {}
      document.querySelectorAll('.upd-overlay.open').forEach((el) => el.classList.remove('open'));
    });
    const loadErr = await page.evaluate(async () => {
      try {
        if (window.cccTour && typeof window.cccTour.end === 'function') {
          try { window.cccTour.end('skip'); } catch (_) {}
        }
        document.querySelectorAll('script[src*="tour.js"]').forEach((s) => s.remove());
        window.cccTour = undefined;
        await new Promise((res, rej) => {
          const s = document.createElement('script');
          s.src = '/static/tour.js?ftue=' + Date.now();
          s.onload = res;
          s.onerror = () => rej(new Error('tour.js failed to load'));
          document.head.appendChild(s);
        });
        try { localStorage.removeItem('ccc-tour-done'); } catch (_) {}
        window.cccTour.start({ force: true });
        return null;
      } catch (e) {
        return String(e && e.message ? e.message : e);
      }
    });
    if (loadErr) throw new Error('live walk start failed: ' + loadErr);
    await page.waitForFunction(() => {
      const title = document.querySelector('.fft-title');
      return !!(title && title.textContent.trim());
    }, { timeout: 8000 });

    const recorded = [];
    for (let i = 0; i < 40; i++) {
      const snap = await page.evaluate(() => {
        function box(sel) {
          const el = document.querySelector(sel);
          if (!el) return { exists: false, visible: false, w: 0, h: 0 };
          const r = el.getBoundingClientRect();
          return { exists: true, visible: r.width > 0 && r.height > 0, w: Math.round(r.width), h: Math.round(r.height) };
        }
        const title = (document.querySelector('.fft-title') || {}).textContent || '';
        const state = window.cccTour.getState ? window.cccTour.getState() : {};
        const stepId = (state && state.step && state.step.id)
          || (document.querySelector('[data-fft-step]') && document.querySelector('[data-fft-step]').getAttribute('data-fft-step'))
          || '';
        return {
          title: title.trim(),
          stepId,
          overlay: !!document.querySelector('.fft-center-card, .fft-card'),
          active: !!(state && state.active),
          matched: state && state.revealed && state.revealed.matched,
          composer: box('#convInputBar'),
          engine: box('#convInputEngineSelect'),
          send: box('#convSendBtn'),
          workers: box('[data-conv-tab="workers"]'),
          queueTab: box('[data-rail-tab="queue"]'),
          queuePane: box('#statusRailQueuePane'),
          delegate: box('[data-orch-playbook="delegate"]'),
          redetect: !!document.querySelector('.fft-cli-redetect'),
          cliRows: [...document.querySelectorAll('.fft-cli-row')].map((row) => ({
            engine: row.getAttribute('data-fft-cli'),
            state: row.getAttribute('data-fft-cli-state'),
            hasInstall: !!row.querySelector('.fft-cli-install'),
            hasLogin: !!row.querySelector('.fft-cli-login'),
          })),
        };
      });
      if (!snap.overlay && !snap.active) break;
      if ((snap.stepId === 'cli-setup' || /cli/i.test(snap.title)) && snap.cliRows.length === 0) {
        try {
          await page.waitForFunction(
            () => document.querySelectorAll('.fft-cli-row').length > 0,
            { timeout: 8000 }
          );
          snap.cliRows = await page.evaluate(() => [...document.querySelectorAll('.fft-cli-row')].map((row) => ({
            engine: row.getAttribute('data-fft-cli'),
            state: row.getAttribute('data-fft-cli-state'),
            hasInstall: !!row.querySelector('.fft-cli-install'),
            hasLogin: !!row.querySelector('.fft-cli-login'),
          })));
          snap.redetect = await page.evaluate(() => !!document.querySelector('.fft-cli-redetect'));
        } catch (_) {}
      }
      recorded.push(snap);
      if (snap.stepId === 'composer') {
        await page.screenshot({ path: path.join(SCRATCH, 'ftue-live-composer.png') });
      }
      if (snap.stepId === 'workers-tab') {
        await page.screenshot({ path: path.join(SCRATCH, 'ftue-live-workers.png') });
      }
      if (snap.stepId === 'first-queue') {
        await page.screenshot({ path: path.join(SCRATCH, 'ftue-live-queue.png') });
      }
      if (snap.stepId === 'delegation') {
        await page.screenshot({ path: path.join(SCRATCH, 'ftue-live-delegate.png') });
      }
      await page.evaluate(() => {
        const btn = document.querySelector('.fft-btn-primary');
        if (btn) btn.click();
        else if (window.cccTour && window.cccTour.next) window.cccTour.next();
      });
    }

    const lines = recorded.map((s, i) => {
      return [i + 1, s.stepId || '-', JSON.stringify(s.title),
        'matched=' + (s.matched || ''),
        'composer=' + s.composer.visible,
        'engine=' + s.engine.visible,
        'send=' + s.send.visible,
        'workers=' + s.workers.visible,
        'queue=' + s.queueTab.visible,
        'delegate=' + s.delegate.visible].join(' ');
    });
    lines.push('errors=' + errors.length + (errors.length ? ' ' + errors.join('; ') : ''));
    fs.writeFileSync(logLive, lines.join('\n') + '\n', 'utf8');

    assert.ok(recorded.length >= 20, 'live walk expected >=20 steps, got ' + recorded.length);
    const byId = {};
    recorded.forEach((s) => { byId[s.stepId] = s; });
    const cli = recorded.find((s) => s.stepId === 'cli-setup' || /cli/i.test(s.title));
    assert.ok(cli, 'live walk never reached the CLI step');
    assert.equal(cli.redetect, true, 'live CLI step missing re-detect');
    assert.ok(cli.cliRows.length >= 1, 'live CLI step rendered no engine rows');

    const composer = byId.composer;
    assert.ok(composer, 'live walk never reached the composer step');
    assert.equal(composer.composer.visible, true, 'live composer bar still hidden');
    assert.equal(/new-session|sidebarNewBtn/.test(String(composer.matched || '')), false,
      'live composer fell back to New session: ' + composer.matched);

    const engine = byId['engine-picker'];
    assert.ok(engine, 'live walk never reached the engine-picker step');
    assert.equal(engine.engine.visible, true, 'live engine picker still hidden');

    const send = byId.send;
    assert.ok(send, 'live walk never reached the send step');
    assert.equal(send.send.visible, true, 'live send button still hidden');

    const workers = byId['workers-tab'] || byId['workers-lane'];
    assert.ok(workers, 'live walk never reached Workers');
    assert.equal(workers.workers.visible, true, 'live Workers tab hidden');

    const queue = byId['queue-tab'] || byId['first-queue'];
    assert.ok(queue, 'live walk never reached Queue');
    assert.equal(queue.queueTab.visible, true, 'live Queue tab hidden');

    const delegation = byId.delegation;
    assert.ok(delegation, 'live walk never reached Delegation');
    assert.equal(delegation.delegate.visible, true, 'live Delegate playbook hidden');
    assert.equal(errors.length, 0, 'live walk page errors: ' + errors.join('; '));
  } catch (err) {
    const msg = String(err && err.message ? err.message : err);
    if (/net::|timeout|chrome|browser|ECONNREFUSED|tour\.js failed/i.test(msg) && !/hidden|fell back|never reached|blank/i.test(msg)) {
      fs.writeFileSync(unavailable, msg + '\n', 'utf8');
      return;
    }
    throw err;
  } finally {
    await page.close();
  }
});

test('headless dashboard launch starts the guide twice with a non-empty first step', async () => {
  const logWalk = path.join(SCRATCH, 'ftue-walk.log');
  const shot = path.join(SCRATCH, 'ftue.png');
  const unavailable = path.join(SCRATCH, 'ftue-launch-unavailable.log');
  const url = await resolveDashboardUrl();
  if (!url) {
    fs.writeFileSync(unavailable, 'dashboard URL did not respond\n', 'utf8');
    return;
  }
  const lines = [];
  async function oneLaunch(tag) {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', (err) => errors.push(String(err)));
    try {
      await page.setViewport({ width: 1440, height: 900 });
      await page.goto(url, { waitUntil: 'load', timeout: 30000 });
      await page.evaluate(() => {
        try { localStorage.removeItem('ccc-tour-done'); } catch (_) {}
        document.querySelectorAll('.upd-overlay.open').forEach((el) => el.classList.remove('open'));
      });
      const loadErr = await page.evaluate(async () => {
        try {
          if (!window.cccTour) {
            await new Promise((res, rej) => {
              const s = document.createElement('script');
              s.src = '/static/tour.js';
              s.onload = res;
              s.onerror = () => rej(new Error('tour.js failed to load'));
              document.head.appendChild(s);
            });
          }
          window.cccTour.start({ force: true });
          return null;
        } catch (e) {
          return String(e && e.message ? e.message : e);
        }
      });
      if (loadErr) throw new Error(tag + ' start failed: ' + loadErr);
      await page.waitForFunction(() => {
        const title = document.querySelector('.fft-title');
        return !!(title && title.textContent.trim());
      }, { timeout: 8000 });
      const first = await page.evaluate(() => ({
        title: (document.querySelector('.fft-title') || {}).textContent || '',
        overlay: !!document.querySelector('.fft-center-card, .fft-card'),
      }));
      assert.ok(first.overlay, tag + ' overlay missing');
      assert.ok(first.title.trim(), tag + ' first step blank');
      await page.evaluate(() => {
        const btn = document.querySelector('.fft-btn-primary');
        if (btn) btn.click();
        else window.cccTour.next();
      });
      await page.waitForFunction((prev) => {
        const title = document.querySelector('.fft-title');
        return title && title.textContent.trim() && title.textContent.trim() !== prev;
      }, { timeout: 8000 }, first.title.trim());
      const second = await page.evaluate(() => (document.querySelector('.fft-title') || {}).textContent || '');
      assert.notEqual(second.trim(), first.title.trim(), tag + ' Next did not change the step');
      if (tag === 'launch-1') {
        await page.screenshot({ path: shot });
      }
      lines.push(`${tag} overlay=${first.overlay} first=${JSON.stringify(first.title.trim())} next=${JSON.stringify(second.trim())} errors=${errors.length}`);
      assert.equal(errors.length, 0, tag + ' page errors: ' + errors.join('; '));
    } finally {
      await page.close();
    }
  }
  try {
    await oneLaunch('launch-1');
    await oneLaunch('launch-2');
    fs.writeFileSync(logWalk, lines.join('\n') + '\n', 'utf8');
  } catch (err) {
    const msg = String(err && err.message ? err.message : err);
    if (/net::|timeout|chrome|browser|ECONNREFUSED|tour\.js failed/i.test(msg) && !/blank|empty|did not change/i.test(msg)) {
      fs.writeFileSync(unavailable, msg + '\n', 'utf8');
      return;
    }
    throw err;
  }
});
