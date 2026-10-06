'use strict';

/**
 * Drive the Moment Zero shell (static/onboarding/onboarding.js) end to end
 * on a fixture page. The shell talks to sibling lanes through a driver
 * object; the test injects a fake one, so the whole welcome -> steps ->
 * free key -> first task -> finale arc runs without any backend.
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
const ONB_JS = path.join(ROOT, 'static', 'onboarding', 'onboarding.js');
const ONB_CSS = path.join(ROOT, 'static', 'onboarding', 'onboarding.css');
const INDEX_HTML = path.join(ROOT, 'static', 'index.html');
const APP_JS = path.join(ROOT, 'static', 'app.js');
const SCRATCH = fs.mkdtempSync(path.join(os.tmpdir(), 'mz-shell-'));

const FIXTURE_HTML = `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CCC moment-zero fixture</title>
<link rel="stylesheet" href="/static/onboarding/onboarding.css">
<style>html, body { margin: 0; height: 100%; background: #0d1117; }</style>
</head>
<body>
<button type="button" id="momentZeroBtn">Open</button>
<button type="button" id="takeTourBtn">Start</button>
<script src="/static/onboarding/onboarding.js"></script>
</body>
</html>`;

// The fake driver: a scripted plan that runs one "install" job, a keyless
// provider, and a first task that finishes through the shared job runner.
const FAKE_DRIVER = `{
  getPlan: async () => {
    const ran = !!window.__mzRan;
    const keyed = !!(window.__mzKeys || []).length;
    const tasked = !!window.__mzTaskDone;
    return { steps: [
      { id: 'clt', label: 'CLT', status: 'ok' },
      { id: 'node', label: 'Node', status: ran ? 'ok' : 'missing', needs_consent: true, est_seconds: 90 },
      { id: 'claude_cli', label: 'Claude', status: ran ? 'ok' : 'missing', needs_consent: false, est_seconds: 45 },
      { id: 'free_router', label: 'Router', status: ran ? 'ok' : 'missing', needs_consent: false, est_seconds: 60 },
      { id: 'free_key', label: 'Key', status: keyed ? 'ok' : 'missing' },
      { id: 'first_task', label: 'Task', status: tasked ? 'ok' : 'missing' },
    ] };
  },
  runSteps: async (ids) => {
    window.__mzRan = ids;
    return { job_id: 'job-1' };
  },
  pollJob: async (id) => {
    window.__mzPolls = (window.__mzPolls || 0) + 1;
    if (window.__mzPolls < 2) {
      return { status: 'running', step: 'node', progress: 0.4,
        lines: ['downloading node-v22.tgz', 'verifying shasum'] };
    }
    return { status: 'done', step: 'free_router', progress: 1, lines: ['router listening on 127.0.0.1:3017'] };
  },
  routerStatus: async () => ({ running: true }),
  detectedRouters: async () => [],
  providers: async () => ([
    { platform: 'kilo', name: 'Kilo', keyless: true, free_no_card: true,
      tos_note: 'Prompts may be logged for training.', coding_score: 7 },
    { platform: 'groq', name: 'Groq', keyless: false, free_no_card: true,
      signup_url: 'https://console.groq.example', key_hint: 'gsk_...', coding_score: 8 },
  ]),
  submitKey: async (platform, key) => {
    window.__mzKeys = (window.__mzKeys || []).concat([[platform, key ? 'set' : 'none']]);
    return { ok: true, validated: true };
  },
  firstTask: async (taskId) => {
    window.__mzTask = taskId;
    return { job_id: 'job-task', saved_usd: 1.42 };
  },
  savings: async () => ({ api_value_usd: 3.5, free_saved_usd: 1.42 }),
  freshInstall: async () => ({ ok: true, fresh_install: true, has_history: false }),
}`;

let browser;
let fixtureServer;
let fixtureUrl;

test.before(async () => {
  fixtureServer = http.createServer((req, res) => {
    const url = String(req.url || '/');
    if (url.includes('onboarding.js')) {
      res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8' });
      res.end(fs.readFileSync(ONB_JS));
      return;
    }
    if (url.includes('onboarding.css')) {
      res.writeHead(200, { 'Content-Type': 'text/css; charset=utf-8' });
      res.end(fs.readFileSync(ONB_CSS));
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(FIXTURE_HTML);
  });
  await new Promise((resolve) => fixtureServer.listen(0, '127.0.0.1', resolve));
  fixtureUrl = 'http://127.0.0.1:' + fixtureServer.address().port + '/';
  browser = await puppeteer.launch({
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });
});

test.after(async () => {
  await browser?.close();
  if (fixtureServer) await new Promise((resolve) => fixtureServer.close(resolve));
  fs.rmSync(SCRATCH, { recursive: true, force: true });
});

async function openFixture(query) {
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', (err) => errors.push(String(err)));
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(fixtureUrl + (query || ''), { waitUntil: 'load' });
  await page.evaluate((driverSrc) => {
    try { localStorage.clear(); } catch (_) {}
    window.cccOnboarding._setDriver(eval('(' + driverSrc + ')'));
  }, FAKE_DRIVER);
  page.__errors = errors;
  return page;
}

test('index.html ships the shell assets and the Settings entry point', () => {
  const index = fs.readFileSync(INDEX_HTML, 'utf8');
  assert.ok(index.includes('/static/onboarding/onboarding.js'), 'script tag missing');
  assert.ok(index.includes('/static/onboarding/onboarding.css'), 'stylesheet missing');
  assert.ok(index.includes('id="momentZeroBtn"'), 'settings row button missing');
  // onboarding.js must evaluate before app.js so claimFirstRun exists when
  // the engines first-run check fires.
  assert.ok(
    index.indexOf('onboarding.js') < index.indexOf('"/static/app.js"'),
    'onboarding.js must load before app.js'
  );
});

test('app.js defers the engines first-run to claimFirstRun', () => {
  const app = fs.readFileSync(APP_JS, 'utf8');
  assert.ok(app.includes('cccOnboarding.claimFirstRun'), 'handoff hook missing');
});

test('welcome -> steps -> key -> task -> finale happy path', async (t) => {
  const page = await openFixture();
  try {
    // ?onboarding=1 is the public route into the shell.
    await page.evaluate(() => window.cccOnboarding.open());
    await page.waitForSelector('#cccMomentZero .mz-welcome', { visible: true });

    const headline = await page.$eval('.mz-headline', (el) => el.getAttribute('aria-label'));
    assert.equal(headline, 'Your own AI dev team. Free.');

    await page.click('.mz-btn-primary'); // "Set up my team"
    await page.waitForSelector('.mz-step-row', { visible: true });

    // clt is already ok; the first actionable step asks for consent.
    const stepIds = await page.$$eval('.mz-step-row', (rows) => rows.map((r) => r.dataset.stepId));
    assert.deepEqual(stepIds, ['clt', 'node', 'claude_cli', 'free_router', 'free_key', 'first_task']);

    // Consent + run: the fake job polls running then done.
    await page.click('.mz-step-cta .mz-btn-primary');
    await page.waitForFunction(() => window.__mzRan && window.__mzRan.length === 3);
    await page.waitForFunction(
      () => !window.cccOnboarding._state.jobId,
      { timeout: 20000 }
    );
    // After the job finishes, free_key is the next actionable step and the
    // fallback provider cards rendered (L03's wizard is absent here).
    await page.waitForSelector('.mz-provider', { visible: true });
    const providerNames = await page.$$eval('.mz-provider-name', (els) => els.map((e) => e.textContent));
    assert.ok(providerNames.includes('Kilo'));

    // Keyless provider: consent line + enable -> key submitted with no value.
    await page.click('.mz-provider .mz-btn-primary');
    await page.waitForFunction(
      () => (window.__mzKeys || []).some((k) => k[0] === 'kilo' && k[1] === 'none'),
      { timeout: 10000 }
    );

    // Next actionable is first_task: pick a card, fake job completes.
    await page.waitForSelector('.mz-task-card', { visible: true });
    await page.click('.mz-task-card');
    await page.waitForSelector('.mz-finale', { visible: true, timeout: 20000 });

    const cost = await page.$eval('.mz-big-cost', (el) => el.textContent.trim());
    assert.equal(cost, '$0');
    const finaleText = await page.$eval('.mz-finale', (el) => el.textContent);
    assert.ok(/cost \$0/i.test(finaleText), 'finale should carry the $0 line');
    assert.ok(/1\.42/.test(finaleText), 'finale should show the saved amount');

    // Closing marks the user onboarded and hides the overlay.
    await page.click('.mz-finale .mz-btn-primary');
    await page.waitForFunction(() => !window.cccOnboarding.isOpen());
    const onboarded = await page.evaluate(() => localStorage.getItem('ccc-onboarded'));
    assert.ok(onboarded, 'ccc-onboarded not persisted');
    assert.deepEqual(page.__errors, []);
  } finally {
    await page.close();
  }
});

test('claimFirstRun opens the shell on a fresh install and suppresses after', async () => {
  const page = await openFixture();
  try {
    const claimed = await page.evaluate(() => window.cccOnboarding.claimFirstRun());
    assert.equal(claimed, true);
    await page.waitForSelector('#cccMomentZero.open', { visible: true });
    // The engines first-run marker is set so the old screen cannot stack.
    const engDone = await page.evaluate(() => localStorage.getItem('ccc-engines-first-run-done'));
    assert.ok(engDone, 'engines first-run marker not set');
    // Second claim: ccc-onboarded now set -> not eligible again.
    const again = await page.evaluate(() => window.cccOnboarding.claimFirstRun());
    assert.equal(again, false);
  } finally {
    await page.close();
  }
});

test('existing install does not auto-claim', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      window.cccOnboarding._setDriver({
        freshInstall: async () => ({ ok: true, fresh_install: false, has_history: true }),
      });
    });
    const claimed = await page.evaluate(() => window.cccOnboarding.claimFirstRun());
    assert.equal(claimed, false);
    const opened = await page.evaluate(() => window.cccOnboarding.isOpen());
    assert.equal(opened, false);
  } finally {
    await page.close();
  }
});

test('?onboarding=1 force-opens even for a returning user', async () => {
  const page = await openFixture('?onboarding=1');
  try {
    await page.evaluate(() => localStorage.setItem('ccc-onboarded', '1'));
    await page.waitForSelector('#cccMomentZero.open', { visible: true });
  } finally {
    await page.close();
  }
});

test('Escape closes and persists onboarded state', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => window.cccOnboarding.open());
    await page.waitForSelector('#cccMomentZero.open', { visible: true });
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !window.cccOnboarding.isOpen());
    const onboarded = await page.evaluate(() => localStorage.getItem('ccc-onboarded'));
    assert.ok(onboarded);
  } finally {
    await page.close();
  }
});

test('plan endpoint missing degrades to a friendly card, not a crash', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      window.cccOnboarding._setDriver({
        getPlan: async () => { const e = new Error('nope'); e.status = 404; throw e; },
        freshInstall: async () => ({ ok: true, fresh_install: true }),
      });
      window.cccOnboarding.open({ scene: 'steps' });
    });
    await page.waitForSelector('.mz-fallback', { visible: true });
    const txt = await page.$eval('.mz-fallback', (el) => el.textContent);
    assert.ok(/not in this build/i.test(txt));
    assert.deepEqual(page.__errors, []);
  } finally {
    await page.close();
  }
});
