const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const base = process.env.LEFTOVER_URL || 'http://127.0.0.1:9206';
const folder = process.env.LEFTOVER_REPO;
const output = process.env.LEFTOVER_OUT;
if (!folder || !output) throw new Error('Set LEFTOVER_REPO to a scratch Git repo with three TODO notes and LEFTOVER_OUT to an existing evidence directory.');

(async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  let timer;
  try {
    await Promise.race([new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Leftover verification exceeded 90 seconds')), 90000);
    }), (async () => {
      const page = await browser.newPage();
      await page.evaluateOnNewDocument(() => {
        localStorage.setItem('ccc-engines-first-run-done', '1');
        localStorage.setItem('ccc-tour-done', '1');
        localStorage.setItem('ccc-tailscale-step-done', '1');
      });
      await page.setViewport({ width: 1280, height: 900 });
      await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let mode = 'ready', dollars = 12.5, spawnCount = 0, headroomCount = 0, spawnMode = 'ok';
      const bodies = [];
      await page.setRequestInterception(true);
      page.on('request', request => {
        const url = new URL(request.url());
        if (url.pathname === '/api/headroom') {
          headroomCount++;
          const accounts = ['claude', 'codex'].map(engine => ({
            id: engine + ':current', engine, account_id: 'current', label: engine === 'claude' ? 'Claude' : 'Codex',
            available: true, stale: false, forecast_available: true, leftover_candidate: true,
            percent_left: 60, expires_unused_pct: 40, expires_unused_usd: dollars,
            hours_to_reset: 9, resets_at: new Date(Date.now() + 9 * 3600000).toISOString(),
          }));
          request.respond({ status: mode === 'missing' ? 404 : 200, contentType: 'application/json', body: JSON.stringify(mode === 'missing' ? { error: 'not_merged' } : { ok: true, accounts, updated_at: new Date().toISOString() }) });
        } else if (url.pathname === '/api/sessions/spawn' && request.method() === 'POST') {
          spawnCount++;
          bodies.push(JSON.parse(request.postData()));
          request.respond({ status: spawnMode === 'error' ? 400 : 200, contentType: 'application/json', body: JSON.stringify(spawnMode === 'error' ? { ok: false, error: 'Test start failure. Try again.' } : { ok: true, session_id: 'test-leftover-session' }) });
        } else request.continue();
      });
      await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(() => !!window.cccLeftover);
      assert.equal(headroomCount, 0, 'Unapproved automatic mode must not poll headroom');
      assert.equal(spawnCount, 0);
      await page.click('#settingsBtn');
      await page.locator('#settingsRailTab-leftover').click();
      await page.screenshot({ path: path.join(output, 'leftover-open.png') });
      console.log(JSON.stringify(await page.evaluate(() => ({ modal: document.querySelector('#settingsModal').className, panel: document.querySelector('#settingsSection-leftover').className, headline: document.querySelector('#loHeadline').textContent }))), errors);
      await page.waitForFunction(() => document.querySelector('#settingsSection-leftover').classList.contains('is-active-section'));
      await page.waitForFunction(() => document.querySelector('#loHeadline').textContent.includes('$12.50'));
      await page.evaluate(folder => {
        const input = document.querySelector('#loRepoInput'); input.value = folder; input.dispatchEvent(new Event('change', { bubbles: true }));
      }, folder);
      await page.waitForFunction(() => document.querySelectorAll('.lo-task').length >= 3, { timeout: 25000 });
      assert.equal(spawnCount, 0, 'Reading proposals is not approval');
      assert.equal(await page.$eval('#cccLeftoverOffer', node => !node.hidden).catch(() => false), false);
      assert.match(await page.$eval('#loSources', node => node.textContent), /TODO and FIXME/);
      await page.screenshot({ path: path.join(output, 'leftover-desktop.png') });
      await page.evaluate(() => {
        const select = document.querySelector('#loAccount'); select.value = 'codex:current'; select.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await page.waitForFunction(() => document.querySelector('[data-lo-start]').textContent === 'Start with Codex');
      await page.evaluate(() => { const button = document.querySelector('[data-lo-start]'); button.click(); button.click(); });
      await page.waitForFunction(() => document.querySelector('.lo-outcome')?.textContent.includes('Started with Codex'));
      assert.equal(spawnCount, 1, 'Double approval click must collapse while pending');
      assert.equal(bodies[0].engine, 'codex');
      assert.equal(bodies[0].cwd, folder);
      assert.equal(bodies[0].repo_path, folder);
      assert.equal(bodies[0].runtime, undefined);
      assert.equal(bodies[0].worktree, true);
      assert.match(bodies[0].task_key, /^leftover:[a-f0-9]+:codex$/);
      spawnMode = 'error';
      await page.evaluate(() => document.querySelectorAll('[data-lo-start]')[1].click());
      await page.waitForFunction(() => Array.from(document.querySelectorAll('.lo-outcome')).some(node => node.textContent.includes('Test start failure')));
      spawnMode = 'ok';
      await page.evaluate(() => document.querySelectorAll('[data-lo-start]')[1].click());
      await page.waitForFunction(() => document.querySelectorAll('[data-lo-start]:disabled').length === 2);
      assert.equal(bodies[1].task_key, bodies[2].task_key, 'Retry must use the same dedupe key');
      await page.click('#loEnabled');
      assert.equal(await page.$eval('#loEnabled', node => node.getAttribute('aria-checked')), 'false');
      assert.equal(await page.evaluate(() => localStorage.getItem('ccc-leftover-enabled')), '0');
      dollars = null;
      await page.evaluate(() => window.cccLeftover.refresh());
      await page.waitForFunction(() => document.querySelector('#loHeadline').textContent.includes('40%'));
      assert.doesNotMatch(await page.$eval('#loHeadline', node => node.textContent), /\$/);
      await page.setViewport({ width: 390, height: 844 });
      await page.evaluate(() => { document.querySelector('#settingsPane').scrollTop = 0; });
      await page.screenshot({ path: path.join(output, 'leftover-mobile.png') });
      assert.equal(await page.$eval('#settingsPane', node => node.scrollWidth > node.clientWidth + 2), false, 'Leftover panel must not overflow horizontally');
      mode = 'missing';
      await page.evaluate(() => window.cccLeftover.refresh());
      await page.waitForFunction(() => document.querySelector('#loHeadline').textContent.includes('not available in this build'));
      assert.equal(await page.$eval('[data-lo-start]', node => node.disabled), true);
      mode = 'ready';
      await page.evaluate(() => window.cccLeftover.refresh());
      await page.waitForFunction(() => document.querySelector('#loHeadline').textContent.includes('40%'));
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.click('#settingsBtn');
      await page.locator('#settingsRailTab-leftover').click();
      await page.screenshot({ path: path.join(output, 'leftover-open.png') });
      console.log(JSON.stringify(await page.evaluate(() => ({ modal: document.querySelector('#settingsModal').className, panel: document.querySelector('#settingsSection-leftover').className, headline: document.querySelector('#loHeadline').textContent }))), errors);
      await page.waitForFunction(() => document.querySelector('#settingsSection-leftover').classList.contains('is-active-section'));
      assert.equal(await page.$eval('#loEnabled', node => node.getAttribute('aria-checked')), 'false');
      const result = { verdict: 'VERIFIED', headroom: 'exact M05 fixture, not merged in this branch', proposals: 'real HTTP backend and scratch Git repo', spawn: 'intercepted approval boundary; no paid agent launched', spawnCount, screenshots: ['leftover-desktop.png', 'leftover-mobile.png'], pageErrors: errors };
      fs.writeFileSync(path.join(output, 'browser-result.json'), JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
    })()]);
  } finally {
    clearTimeout(timer);
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
