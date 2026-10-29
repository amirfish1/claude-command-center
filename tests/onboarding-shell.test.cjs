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
    return { job_id: 'job-task', runtime: 'free' };
  },
  pollFirstTask: async () => ({ status: 'done', runtime: 'free',
    result: { verified: true }, usage: { api_value_usd: 1.42 }, lines: [] }),
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
    await page.waitForFunction(() => window.__mzRan && window.__mzRan.length === 2);
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
    // The auto-open is a gated pop-up (static/popups.js): approve it here.
    await page.evaluate(() => { window.cccPopups = { allowed: (id) => id === 'moment-zero' }; });
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

test('embedded key wizard completion advances to the task picker', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      window.__mzRan = true;
      window.cccFreeKeyWizard = { mount: (host, opts) => {
        const b = document.createElement('button');
        b.id = 'completeKeyWizard';
        b.textContent = 'Connect provider';
        b.onclick = () => opts.onComplete({ platform: 'kilo' });
        host.appendChild(b);
      } };
      window.cccOnboarding.open({ scene: 'steps' });
    });
    await page.waitForSelector('#completeKeyWizard');
    await page.click('#completeKeyWizard');
    await page.waitForSelector('.mz-task-card', { visible: true, timeout: 2000 });
    assert.deepEqual(page.__errors, []);
  } finally { await page.close(); }
});

test('failed first task stays retryable instead of celebrating', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      window.cccOnboarding._setDriver({
        getPlan: async () => ({ steps: [{ id: 'first_task', status: 'missing' }] }),
        firstTask: async () => { throw new Error('Free models are not ready.'); },
      });
      window.cccOnboarding.open({ scene: 'steps' });
    });
    await page.waitForSelector('.mz-task-card');
    await page.click('.mz-task-card');
    await page.waitForSelector('.mz-task-error', { visible: true, timeout: 2000 });
    assert.equal(await page.$('.mz-finale'), null);
    assert.equal(await page.$('.mz-taskrun'), null);
  } finally { await page.close(); }
});

test('real driver uses the first-task HTTP contract and run-specific savings', async () => {
  const page = await openFixture();
  const calls = [];
  try {
    await page.setRequestInterception(true);
    page.on('request', async (req) => {
      const route = new URL(req.url()).pathname;
      if (!route.startsWith('/api/')) return req.continue();
      calls.push({ route, method: req.method(), body: req.postData() });
      let body;
      if (route === '/api/setup/plan') body = { steps: [{ id: 'first_task', status: 'missing' }] };
      else if (route === '/api/onboarding/first-task') body = { ok: true, job: { job_id: 'first-real', status: 'running' } };
      else if (route === '/api/onboarding/first-task/first-real') body = { ok: true, job: {
        job_id: 'first-real', status: 'done', runtime: 'free', result: { verified: true },
        lines: [{ text: 'Everything checks out.' }], usage: { api_value_usd: 0.42 },
      } };
      else body = { error: 'unexpected endpoint' };
      await req.respond({ status: body.error ? 404 : 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.evaluate(() => {
      window.cccOnboarding._setDriver(null);
      window.cccOnboarding.open({ scene: 'steps' });
    });
    await page.waitForSelector('.mz-task-card');
    await page.click('.mz-task-card');
    await page.waitForSelector('.mz-finale', { visible: true });
    assert.deepEqual(JSON.parse(calls.find((c) => c.method === 'POST').body), {
      task_id: 'hello-3-langs', runtime: 'free',
    });
    assert.ok(calls.some((c) => c.route === '/api/onboarding/first-task/first-real'));
    assert.ok(!calls.some((c) => c.route.startsWith('/api/setup/jobs/') || c.route === '/api/savings'));
    await page.waitForFunction(() => document.querySelector('.mz-saved-num')?.textContent === '$0.42');
    assert.deepEqual(page.__errors, []);
  } finally { await page.close(); }
});

test('router consent starts the router installer and clears finished progress', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      window.cccOnboarding._setDriver({
        getPlan: async () => ({ steps: [
          { id: 'free_router', status: window.__routerInstalled ? 'ok' : 'missing', needs_consent: true, external: true },
          { id: 'first_task', status: 'missing' },
        ] }),
        routerStatus: async () => ({ installed: false }),
        detectedRouters: async () => [],
        installRouter: async () => { window.__routerInstalled = true; return { job_id: 'router-install' }; },
        runSteps: async () => { throw new Error('Router sent to the wrong runner'); },
        pollJob: async () => ({ status: 'done', step: 'router', progress: 1, lines: ['ready'] }),
      });
      window.cccOnboarding.open({ scene: 'steps' });
    });
    await page.waitForSelector('.mz-step-cta .mz-btn-primary');
    await page.click('.mz-step-cta .mz-btn-primary');
    await page.waitForSelector('.mz-task-card', { visible: true });
    assert.equal(await page.$('#mzLive .mz-progress'), null);
    assert.equal(await page.evaluate(() => window.__routerInstalled), true);
    assert.deepEqual(page.__errors, []);
  } finally { await page.close(); }
});

for (const outcome of [
  { status: 'error', error: 'Try another free model.' },
  { status: 'done', runtime: 'free', result: { verified: false } },
  { status: 'done', runtime: 'standard', result: { verified: true } },
]) {
  test('first task never celebrates an unsuccessful free run: ' + JSON.stringify(outcome), async () => {
    const page = await openFixture();
    try {
      await page.evaluate((done) => {
        window.cccOnboarding._setDriver({
          getPlan: async () => ({ steps: [{ id: 'first_task', status: 'missing' }] }),
          firstTask: async () => ({ job_id: 'test-task' }),
          pollFirstTask: async () => done,
        });
        window.cccOnboarding.open({ scene: 'steps' });
      }, outcome);
      await page.waitForSelector('.mz-task-card');
      await page.click('.mz-task-card');
      await page.waitForSelector('.mz-task-error', { visible: true });
      assert.equal(await page.$('.mz-finale'), null);
      assert.equal(await page.evaluate(() => window.cccOnboarding._state.firstTaskDone), false);
    } finally { await page.close(); }
  });
}

test('a first task finished while closed resumes to the finale with its savings', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      localStorage.setItem('ccc-onboarding-first-task', 'saved-job-1');
      window.cccOnboarding._setDriver({
        getPlan: async () => ({ steps: [
          { id: 'free_router', status: 'ok' },
          { id: 'first_task', status: 'ok' },
        ] }),
        firstTask: async () => { window.__mzTaskStarted = true; return { job_id: 'nope' }; },
        pollFirstTask: async (id) => ({ job_id: id, status: 'done', runtime: 'free',
          result: { verified: true }, usage: { api_value_usd: 0.42 }, lines: [] }),
      });
      window.cccOnboarding.open({ scene: 'steps' });
    });
    await page.waitForSelector('.mz-finale', { visible: true });
    assert.equal(await page.evaluate(() => window.__mzTaskStarted), undefined);
    assert.equal(await page.evaluate(() => window.cccOnboarding._state.firstTaskDone), true);
    await page.waitForFunction(() => document.querySelector('.mz-saved-num')?.textContent === '$0.42');
    assert.equal(await page.evaluate(() => localStorage.getItem('ccc-onboarding-first-task')), null);
    assert.deepEqual(page.__errors, []);
  } finally { await page.close(); }
});

test('reopening the finale never replays a stale savings number', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      window.cccOnboarding._state.firstTaskDone = true;
      window.cccOnboarding._state.savedUsd = 100;
      window.cccOnboarding.open({ scene: 'finale' });
    });
    await page.waitForSelector('.mz-finale', { visible: true });
    assert.equal(await page.evaluate(() => window.cccOnboarding._state.firstTaskDone), false);
    assert.equal(await page.$('.mz-saved-num'), null);
    const txt = await page.$eval('.mz-finale', (el) => el.textContent);
    assert.ok(!/This run cost/.test(txt));
    assert.deepEqual(page.__errors, []);
  } finally { await page.close(); }
});

test('fresh install does not auto-claim while the pop-up is not approved', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => { window.cccPopups = { allowed: () => false }; });
    const claimed = await page.evaluate(() => window.cccOnboarding.claimFirstRun());
    assert.equal(claimed, false);
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

test('hostNoun adapts copy for Darwin and Linux (local and remote)', async () => {
  const page = await openFixture();
  try {
    const nouns = await page.evaluate(() => {
      const fn = window.cccOnboarding._hostNoun;
      window.__cccHostOverride = '127.0.0.1';
      const darwinLocal = fn({ os: 'Darwin', hostname: 'my-mac' });
      const linuxLocal = fn({ os: 'Linux', hostname: 'my-linux' });
      const winLocal = fn({ os: 'Windows', hostname: 'my-pc' });

      window.__cccHostOverride = 'remote.example.com';
      const darwinRemote = fn({ os: 'Darwin', hostname: 'mac-server' });
      const linuxRemote = fn({ os: 'Linux', hostname: 'hetzner-vps' });
      const winRemote = fn({ os: 'Windows', hostname: 'azure-pc' });

      delete window.__cccHostOverride;
      return { darwinLocal, linuxLocal, winLocal, darwinRemote, linuxRemote, winRemote };
    });

    assert.equal(nouns.darwinLocal, 'this Mac');
    assert.equal(nouns.linuxLocal, 'this server');
    assert.equal(nouns.winLocal, 'this PC');
    assert.equal(nouns.darwinRemote, 'your Mac (mac-server)');
    assert.equal(nouns.linuxRemote, 'the server hetzner-vps');
    assert.equal(nouns.winRemote, 'the PC azure-pc');
  } finally {
    await page.close();
  }
});

test('setup page wording and CLT step visibility with stubbed Darwin vs Linux getPlan', async () => {
  const page = await openFixture();
  try {
    // 1. Stub Darwin plan
    await page.evaluate(() => {
      delete window.__cccHostOverride;
      window.cccOnboarding._setDriver({
        getPlan: async () => ({
          steps: [
            { id: 'clt', label: 'Mac tools from Apple', mac_only: true, status: 'ok' },
            { id: 'node', label: 'Node.js', status: 'missing', needs_consent: true, est_seconds: 60 },
          ],
          machine: { os: 'Darwin', arch: 'arm64', hostname: 'local-mac' },
        }),
      });
      window.cccOnboarding.open({ scene: 'steps' });
    });
    await page.waitForSelector('.mz-steps-card', { visible: true });
    const darwinHeader = await page.$eval('.mz-steps-head p', (el) => el.textContent);
    assert.ok(darwinHeader.includes('this Mac'), `expected "this Mac" in darwin header, got: ${darwinHeader}`);
    const darwinStepIds = await page.$$eval('.mz-step-row', (rows) => rows.map((r) => r.dataset.stepId));
    assert.ok(darwinStepIds.includes('clt'), 'expected clt step to be visible on Darwin');
    const darwinNodeBlurb = await page.$eval('.mz-step-row[data-step-id="node"] .mz-step-desc', (el) => el.textContent);
    assert.ok(darwinNodeBlurb.includes('this Mac'), `expected "this Mac" in node blurb, got: ${darwinNodeBlurb}`);

    // 2. Stub remote Linux plan
    await page.evaluate(() => {
      window.cccOnboarding.close();
      window.__cccHostOverride = 'remote-hetzner.lan';
      window.cccOnboarding._setDriver({
        getPlan: async () => ({
          steps: [
            { id: 'clt', label: 'Mac tools from Apple', mac_only: true, applies: false, status: 'not_applicable' },
            { id: 'node', label: 'Node.js', status: 'missing', needs_consent: true, est_seconds: 60 },
          ],
          machine: { os: 'Linux', arch: 'x86_64', hostname: 'hetzner-srv' },
        }),
      });
      window.cccOnboarding.open({ scene: 'steps' });
    });
    await page.waitForSelector('.mz-step-row[data-step-id="node"]', { visible: true });
    const linuxHeader = await page.$eval('.mz-steps-head p', (el) => el.textContent);
    assert.ok(linuxHeader.includes('the server hetzner-srv'), `expected "the server hetzner-srv" in linux header, got: ${linuxHeader}`);
    const linuxStepIds = await page.$$eval('.mz-step-row', (rows) => rows.map((r) => r.dataset.stepId));
    assert.ok(!linuxStepIds.includes('clt'), 'expected clt step to be hidden on Linux');
    const linuxNodeBlurb = await page.$eval('.mz-step-row[data-step-id="node"] .mz-step-desc', (el) => el.textContent);
    assert.ok(linuxNodeBlurb.includes('the server hetzner-srv'), `expected "the server hetzner-srv" in node blurb, got: ${linuxNodeBlurb}`);

    // Verify no "Mac" anywhere on the setup page on remote Linux
    const setupCardText = await page.$eval('.mz-steps-card', (el) => el.textContent);
    assert.equal(/Mac/i.test(setupCardText), false, `expected no "Mac" in setup card on Linux, got: ${setupCardText}`);
    assert.deepEqual(page.__errors, []);
  } finally {
    await page.close();
  }
});

test('step still pending after done job shows visible reason instead of silent loop', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      window.cccOnboarding._setDriver({
        getPlan: async () => ({
          steps: [
            { id: 'gh', label: 'GitHub CLI', status: 'missing', needs_consent: false, optional: true },
          ],
        }),
        runSteps: async () => ({ job_id: 'job-gh-failed' }),
        pollJob: async () => ({
          status: 'done',
          step: 'gh',
          progress: 1,
          lines: ['GitHub CLI is optional · install failed'],
        }),
      });
      window.cccOnboarding.open({ scene: 'steps' });
    });

    await page.waitForSelector('.mz-step-cta .mz-btn-primary', { visible: true });
    await page.click('.mz-step-cta .mz-btn-primary');

    await page.waitForFunction(() => {
      const live = document.getElementById('mzLive');
      return live && live.textContent.includes("Couldn't install automatically");
    });

    const liveText = await page.$eval('#mzLive', (el) => el.textContent);
    assert.ok(liveText.includes("Couldn't install automatically"), `expected failure note in mzLive, got: ${liveText}`);
    assert.ok(liveText.includes("Skip this step"), `expected skip prompt in mzLive, got: ${liveText}`);

    // Click "Skip this step"
    await page.click('.mz-step-cta .mz-linklike');

    const sideText = await page.$eval('.mz-step-row[data-step-id="gh"] .mz-step-side', (el) => el.textContent);
    assert.ok(sideText.includes('Skipped') || sideText.includes('Done'), `expected Skipped or Done, got: ${sideText}`);

    // Reopen/reload plan
    await page.evaluate(() => {
      window.cccOnboarding.close();
      window.cccOnboarding.open({ scene: 'steps' });
    });
    const sideTextAfter = await page.$eval('.mz-step-row[data-step-id="gh"] .mz-step-side', (el) => el.textContent);
    assert.ok(sideTextAfter.includes('Skipped') || sideTextAfter.includes('Done'));

    assert.deepEqual(page.__errors, []);
  } finally {
    await page.close();
  }
});

test('skipped optional step stays skipped across plan reload', async () => {
  const page = await openFixture();
  try {
    await page.evaluate(() => {
      window.cccOnboarding._setDriver({
        getPlan: async () => ({
          steps: [
            { id: 'gh', label: 'GitHub CLI', status: 'missing', needs_consent: false, optional: true },
            { id: 'node', label: 'Node.js', status: 'missing', needs_consent: false },
          ],
        }),
      });
      window.cccOnboarding.open({ scene: 'steps' });
    });

    await page.waitForSelector('.mz-step-row[data-step-id="gh"]', { visible: true });
    await page.click('.mz-step-cta .mz-linklike');

    const skipped = await page.evaluate(() => localStorage.getItem('ccc-onboarding-skipped-steps'));
    assert.ok(skipped && skipped.includes('gh'), `expected gh in skipped localStorage, got ${skipped}`);

    await page.evaluate(() => {
      window.cccOnboarding.close();
      window.cccOnboarding.open({ scene: 'steps' });
    });

    await page.waitForSelector('.mz-step-row[data-step-id="node"]', { visible: true });
    const ctaText = await page.$eval('.mz-step-cta .mz-btn-primary', (el) => el.textContent);
    assert.ok(ctaText.includes('Continue setup') || ctaText.includes('Node'), `expected cta for node, got ${ctaText}`);
    assert.deepEqual(page.__errors, []);
  } finally {
    await page.close();
  }
});
