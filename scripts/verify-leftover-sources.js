// Leftover Mode browser check for the WatchTower and GitHub proposal sources.
// Self-contained: builds isolated fixture state, starts a CCC server from this
// checkout on it, drives the real UI with Puppeteer, then tears it all down.
//
//   LEFTOVER_OUT=/path/to/evidence node scripts/verify-leftover-sources.js
//
// Fixture state (all under one temp dir, deleted afterwards unless KEEP=1):
// - HOME is a fresh directory, so the server sees no real sessions, keys or
//   WatchTower queues.
// - WatchTower is the real `wt` CLI on a file-backed queue in that HOME
//   (auto-drain off, 0 workers). A PATH shim forwards only read/queue verbs
//   and adds --no-dispatch to `wt run`, so approving records the run request
//   but no worker can start. Any other verb is refused and logged.
// - GitHub is a PATH shim for `gh` that answers only `gh issue list` from a
//   fixture list; any other call (edit, comment, close, api...) is refused and
//   logged, and the check fails if one happens. No network call is made.
// - /api/headroom is answered from a fixture in its exact contract and every
//   POST /api/sessions/spawn is intercepted, so no agent is ever launched.
// The Leftover offer card and reminder stay unapproved (popups.js); this
// check uses only the Settings panel the user opens by hand.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { execFileSync, spawn } = require('node:child_process');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const output = process.env.LEFTOVER_OUT;
if (!output) throw new Error('Set LEFTOVER_OUT to an existing evidence directory.');
const root = path.resolve(__dirname, '..');
const realWt = execFileSync('sh', ['-c', 'command -v wt'], { encoding: 'utf8' }).trim();
if (!realWt) throw new Error('The WatchTower CLI (wt) is required for this check.');

const QUEUE = 'LOFIX';
const ISSUES = [
  { number: 11, title: 'Add retry to the webhook client', body: 'Fixture issue from the owner.', labels: [], assignees: [], authorAssociation: 'OWNER' },
  { number: 12, title: 'Outsider feature request', body: 'Must not be offered.', labels: [], assignees: [], authorAssociation: 'NONE' },
  { number: 13, title: 'Blocked member issue', body: 'Must not be offered.', labels: [{ name: 'blocked' }], assignees: [], authorAssociation: 'MEMBER' },
  { number: 14, title: 'Assigned collaborator issue', body: 'Must not be offered.', labels: [], assignees: [{ login: 'someone' }], authorAssociation: 'COLLABORATOR' },
];

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = http.createServer().listen(0, '127.0.0.1', () => { const { port } = srv.address(); srv.close(() => resolve(port)); });
    srv.on('error', reject);
  });
}

function writeShim(file, body) { fs.writeFileSync(file, body, { mode: 0o755 }); }

function buildFixture(dir) {
  const home = path.join(dir, 'home'), bin = path.join(dir, 'bin'), repo = path.join(dir, 'leftover-fixture');
  for (const d of [home, bin, repo]) fs.mkdirSync(d, { recursive: true });
  const env = { ...process.env, HOME: home, PATH: bin + path.delimiter + process.env.PATH, GIT_CONFIG_NOSYSTEM: '1' };
  delete env.WATCHTOWER_HOME; delete env.WATCHTOWER_CONFIG_FILE;
  const git = (...args) => execFileSync('git', args, { cwd: repo, env, stdio: 'pipe' });
  git('init', '-q'); git('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-q', '--allow-empty', '-m', 'init');
  git('remote', 'add', 'origin', 'https://github.com/example-owner/leftover-fixture.git');
  fs.writeFileSync(path.join(dir, 'issues.json'), JSON.stringify(ISSUES));
  const log = path.join(dir, 'cli-calls.log');
  writeShim(path.join(bin, 'gh'), `#!/bin/sh
printf 'gh %s\\n' "$*" >> '${log}'
if [ "$1" = issue ] && [ "$2" = list ]; then cat '${path.join(dir, 'issues.json')}'; exit 0; fi
echo "fixture gh: refused $*" >&2; exit 1
`);
  writeShim(path.join(bin, 'wt'), `#!/bin/sh
printf 'wt %s\\n' "$*" >> '${log}'
case "$1" in
  run) shift; exec '${realWt}' run --no-dispatch "$@" ;;
  ls|find|status|config|add|take|--version|version) exec '${realWt}' "$@" ;;
esac
echo "fixture wt: refused $*" >&2; exit 1
`);
  const wt = (...args) => execFileSync(path.join(bin, 'wt'), args, { cwd: repo, env, encoding: 'utf8' });
  wt('config', '-q', QUEUE, '--backend', 'file', '--repo-path', repo, '--auto-drain', 'off', '--workers', '0');
  wt('add', '-q', QUEUE, '--title', 'Fix the flaky export retry', '--text', 'Fixture WatchTower ticket one.');
  wt('take', '-q', QUEUE, '--title', 'Claimed ticket must not be offered', '--worker', 'fixture-worker');
  wt('add', '-q', QUEUE, '--title', 'Document the import flags', '--text', 'Fixture WatchTower ticket three.');
  fs.writeFileSync(log, '');
  return { home, bin, repo, env, log, wt };
}

async function waitForServer(base, child) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error('CCC server exited early with ' + child.exitCode);
    const ok = await new Promise(resolve => http.get(base + '/api/version', res => { res.resume(); resolve(res.statusCode < 500); }).on('error', () => resolve(false)));
    if (ok) return;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error('CCC server did not answer within 60 seconds');
}

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ccc-leftover-sources-'));
  const fx = buildFixture(dir);
  const port = await freePort();
  const base = 'http://127.0.0.1:' + port;
  const serverLog = fs.openSync(path.join(output, 'server.log'), 'w');
  const server = spawn('python3', [path.join(root, 'server.py'), '--port', String(port), '--host', '127.0.0.1'], {
    cwd: fx.repo, env: { ...fx.env, PORT: String(port), CCC_TELEMETRY_DISABLED: '1' }, stdio: ['ignore', serverLog, serverLog],
  });
  let browser, timer;
  try {
    await waitForServer(base, server);
    browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || findChromePath(), args: ['--no-sandbox'] });
    await Promise.race([new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Leftover sources verification exceeded 120 seconds')), 120000);
    }), (async () => {
      const page = await browser.newPage();
      await page.evaluateOnNewDocument(() => {
        for (const key of ['ccc-engines-first-run-done', 'ccc-tour-done', 'ccc-tailscale-step-done', 'ccc-onboarding-done']) localStorage.setItem(key, '1');
        localStorage.setItem('ccc-pwa-install-dismissed', String(Date.now()));
      });
      await page.setViewport({ width: 1280, height: 900 });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const spawns = [];
      const resets = Math.floor(Date.now() / 1000) + 9 * 3600 - 60;
      await page.setRequestInterception(true);
      page.on('request', request => {
        const url = new URL(request.url());
        if (url.pathname === '/api/config-consent' && request.method() === 'GET') {
          request.respond({ status: 404, contentType: 'application/json', body: '{}' });
        } else if (url.pathname === '/api/headroom') {
          const rows = [{ id: 'claude:default', engine: 'claude', account: 'default', label: 'Claude Max', available: true, stale: false, unlimited: false,
            percent_left: 60, resets_at: resets, hours_to_reset: 9, burn_pct_per_hour: 2.2, projected_expiring_pct: 40,
            expiring_usd_estimate: 12.5, expiring_tokens_estimate: null, source: 'quota' }];
          request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, generated_at: new Date().toISOString(), rows }) });
        } else if (url.pathname === '/api/sessions/spawn' && request.method() === 'POST') {
          spawns.push(JSON.parse(request.postData()));
          request.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, session_id: 'test-leftover-session' }) });
        } else request.continue();
      });

      await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(() => !!window.cccLeftover);
      assert.equal(await page.$('#cccLeftoverOffer:not([hidden])'), null, 'Unapproved offer card must not render');
      await page.evaluate(() => document.querySelector('#settingsBtn').click());
      await page.waitForFunction(() => !document.querySelector('#settingsModal').hidden);
      await page.evaluate(() => document.querySelector('#settingsRailTab-leftover').click());
      await page.waitForFunction(() => document.querySelector('#loHeadline').textContent.includes('$12.50'));
      await page.evaluate(folder => {
        const input = document.querySelector('#loRepoInput'); input.value = folder; input.dispatchEvent(new Event('change', { bubbles: true }));
      }, fx.repo);
      await page.waitForFunction(() => document.querySelectorAll('.lo-task').length >= 3, { timeout: 30000 });
      const tasks = await page.$$eval('.lo-task', nodes => nodes.map(node => ({
        source: node.querySelector('.lo-task-source').firstChild.textContent,
        refs: Array.from(node.querySelectorAll('.lo-task-ref')).map(ref => ref.textContent),
        title: node.querySelector('h3').textContent,
        button: node.querySelector('[data-lo-start]').textContent,
        id: node.querySelector('[data-lo-start]').getAttribute('data-lo-start'),
      })));
      const wtTasks = tasks.filter(task => task.source === 'WatchTower task');
      const ghTasks = tasks.filter(task => task.source === 'GitHub issue');
      assert.deepEqual(wtTasks.map(task => task.refs[0]).sort(), [QUEUE + '-1', QUEUE + '-3'], 'Only the open, unclaimed WatchTower tickets are offered');
      assert.deepEqual(ghTasks.map(task => task.refs[0]), ['#11'], 'Only the trusted, unassigned, unblocked GitHub issue is offered');
      assert.equal(tasks.length, 3);
      for (const task of wtTasks) assert.equal(task.button, 'Queue in WatchTower');
      assert.equal(ghTasks[0].button, 'Start with Claude');
      const sources = await page.$eval('#loSources', node => node.textContent);
      assert.match(sources, /Open tasks from your WatchTower queues\./);
      assert.match(sources, /Open, unassigned GitHub issues\./);
      assert.equal(spawns.length, 0, 'Reading proposals is not approval');
      await page.screenshot({ path: path.join(output, 'leftover-sources.png') });

      // One click queues the WatchTower ticket back to WatchTower; no session spawns.
      const first = wtTasks.find(task => task.refs[0] === QUEUE + '-1');
      await page.evaluate(id => { const button = document.querySelector('[data-lo-start="' + id + '"]'); button.click(); button.click(); }, first.id);
      await page.waitForFunction(id => {
        const node = document.querySelector('[data-lo-start="' + id + '"]').closest('.lo-task');
        return node.querySelector('.lo-outcome')?.textContent.includes('in WatchTower');
      }, { timeout: 30000 }, first.id);
      const queuedText = await page.evaluate(id => document.querySelector('[data-lo-start="' + id + '"]').closest('.lo-task').querySelector('.lo-outcome').textContent, first.id);
      assert.equal(queuedText, `Queued ${QUEUE}-1 in WatchTower (${QUEUE}). A worker will pick it up.`);
      assert.equal(spawns.length, 0, 'A WatchTower task must not spawn a loose session');
      const listed = JSON.parse(fx.wt('ls', '-q', QUEUE, '--json'));
      const byRef = Object.fromEntries(listed.map(item => [item.ref, item]));
      assert.equal(byRef[QUEUE + '-1'].run_requested, true, 'Approval records a WatchTower run request');
      assert.ok(!byRef[QUEUE + '-3'].run_requested, 'Other tickets are untouched');
      assert.equal(byRef[QUEUE + '-1'].claimed_by || null, null, 'No worker claimed it (dispatch off)');

      // One click starts the GitHub issue as a session (intercepted here).
      await page.evaluate(id => document.querySelector('[data-lo-start="' + id + '"]').click(), ghTasks[0].id);
      await page.waitForFunction(id => document.querySelector('[data-lo-start="' + id + '"]').closest('.lo-task').querySelector('.lo-outcome')?.textContent.includes('Started with'), {}, ghTasks[0].id);
      assert.equal(spawns.length, 1, 'One click starts exactly one session');
      assert.equal(spawns[0].engine, 'claude');
      assert.equal(spawns[0].repo_path, fx.repo);
      assert.match(spawns[0].prompt, /"reference": "#11"/);
      assert.match(spawns[0].prompt, /Do not publish, deploy, merge, or change ticket state\./);
      await page.screenshot({ path: path.join(output, 'leftover-sources-after.png') });

      const calls = fs.readFileSync(fx.log, 'utf8').trim().split('\n').filter(Boolean);
      // Other CCC panels probe gh/wt read-only (auth status, api user, skills
      // status); the shims refuse those anyway. What matters: GitHub is only
      // read, and the only WatchTower write is the one approved `wt run`.
      const ghCalls = calls.filter(line => line.startsWith('gh '));
      assert.ok(ghCalls.some(line => line.startsWith('gh issue list ')), 'gh issue list was consulted');
      const ghWrites = ghCalls.filter(line => /^gh (issue|pr) (create|edit|comment|close|reopen|delete|lock|unlock|transfer|pin|develop|merge|review)\b|^gh api .*(-X|--method|-f |-F |--field|--raw-field|--input)/.test(line));
      assert.deepEqual(ghWrites, [], 'GitHub is never mutated');
      const wtRuns = calls.filter(line => line.startsWith('wt run'));
      assert.deepEqual(wtRuns, ['wt run ' + QUEUE + '-1'], 'Double click queues the ticket once');
      const wtWrites = calls.filter(line => /^wt (add|take|claim|close|block|edit|ready|drain|set|start|stop|import\s+(?!--help)|config -q)/.test(line));
      assert.deepEqual(wtWrites, [], 'No other WatchTower writes after fixture setup');
      assert.equal(await page.$('#cccLeftoverOffer:not([hidden])'), null, 'Offer card stays unapproved');

      const result = { verdict: 'VERIFIED', watchtower: 'real wt CLI, isolated file-backed queue, run --no-dispatch', github: 'gh shim: issue list fixture only',
        tasks, queuedText, spawnBodies: spawns.map(body => ({ engine: body.engine, repo_path: body.repo_path, task_key: body.task_key })),
        cliCalls: calls, screenshots: ['leftover-sources.png', 'leftover-sources-after.png'], pageErrors: errors };
      fs.writeFileSync(path.join(output, 'sources-result.json'), JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
    })()]);
  } finally {
    clearTimeout(timer);
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve => { if (server.exitCode !== null) resolve(); else { server.once('exit', resolve); setTimeout(() => { server.kill('SIGKILL'); resolve(); }, 5000); } });
    if (!process.env.KEEP) fs.rmSync(dir, { recursive: true, force: true });
    else console.error('Fixture kept at ' + dir);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
