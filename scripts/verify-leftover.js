// Leftover Mode browser check (M06). Runs against a CCC server started from
// this checkout with an isolated HOME. /api/headroom is served from a fixture
// in the exact GET /api/headroom contract, and every POST /api/sessions/spawn
// is intercepted, so no real agent is ever launched. Proposals come from the
// real /api/leftover/proposals backend reading a scratch Git repo.
//
//   LEFTOVER_REPO=/path/to/scratch-repo-with-3-TODOs LEFTOVER_OUT=/path/to/evidence \
//     LEFTOVER_URL=http://127.0.0.1:9206 node scripts/verify-leftover.js
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
  const browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || findChromePath(), args: ['--no-sandbox'] });
  let timer;
  try {
    await Promise.race([new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Leftover verification exceeded 120 seconds')), 120000);
    }), (async () => {
      const page = await browser.newPage();
      await page.evaluateOnNewDocument(() => {
        localStorage.setItem('ccc-engines-first-run-done', '1');
        localStorage.setItem('ccc-tour-done', '1');
        localStorage.setItem('ccc-tailscale-step-done', '1');
        localStorage.setItem('ccc-onboarding-done', '1');
        localStorage.setItem('ccc-pwa-install-dismissed', String(Date.now()));
      });
      await page.setViewport({ width: 1280, height: 900 });
      await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let mode = 'ready', dollars = 12.5, spawnCount = 0, headroomCount = 0, spawnMode = 'ok';
      const bodies = [];
      const resets = Math.floor(Date.now() / 1000) + 9 * 3600 - 60;
      await page.setRequestInterception(true);
      page.on('request', request => {
        const url = new URL(request.url());
        if (url.pathname === '/api/config-consent' && request.method() === 'GET') {
          // Fresh isolated HOME: keep the first-run agent config prompt out of the way.
          request.respond({ status: 404, contentType: 'application/json', body: '{}' });
        } else if (url.pathname === '/api/headroom') {
          headroomCount++;
          const rows = [['claude', 'Claude Max'], ['codex', 'Codex']].map(([engine, label]) => ({
            id: engine + ':default', engine, account: 'default', label, available: true, stale: false, unlimited: false,
            percent_left: 60, resets_at: resets, hours_to_reset: 9, burn_pct_per_hour: 2.2, projected_expiring_pct: 40,
            expiring_usd_estimate: dollars, expiring_tokens_estimate: null, source: 'quota',
          }));
          request.respond({ status: mode === 'missing' ? 404 : 200, contentType: 'application/json',
            body: JSON.stringify(mode === 'missing' ? { error: 'not_found' } : { ok: true, generated_at: new Date().toISOString(), rows }) });
        } else if (url.pathname === '/api/sessions/spawn' && request.method() === 'POST') {
          spawnCount++;
          bodies.push(JSON.parse(request.postData()));
          request.respond({ status: spawnMode === 'error' ? 400 : 200, contentType: 'application/json',
            body: JSON.stringify(spawnMode === 'error' ? { ok: false, error: 'Test start failure. Try again.' } : { ok: true, session_id: 'test-leftover-session' }) });
        } else request.continue();
      });

      // 1. Unapproved build: no background polling, no offer card, no spawn.
      await page.goto(base, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForFunction(() => !!window.cccLeftover);
      await new Promise(resolve => setTimeout(resolve, 1500));
      assert.equal(headroomCount, 0, 'Unapproved automatic mode must not poll headroom');
      assert.equal(await page.$('#cccLeftoverOffer'), null, 'Unapproved offer card must not render');
      assert.equal(spawnCount, 0);

      // 2. Settings panel works without approval (the user opened it).
      // DOM clicks: an unrelated update-check backdrop can sit over the sidebar footer.
      await page.evaluate(() => document.querySelector('#settingsBtn').click());
      await page.waitForFunction(() => !document.querySelector('#settingsModal').hidden);
      await page.evaluate(() => document.querySelector('#settingsRailTab-leftover').click());
      await page.waitForFunction(() => document.querySelector('#settingsSection-leftover').classList.contains('is-active-section'));
      await page.waitForFunction(() => document.querySelector('#loHeadline').textContent.includes('$12.50'));
      await page.evaluate(folder => {
        const input = document.querySelector('#loRepoInput'); input.value = folder; input.dispatchEvent(new Event('change', { bubbles: true }));
      }, folder);
      await page.waitForFunction(() => document.querySelectorAll('.lo-task').length >= 3, { timeout: 25000 });
      assert.equal(spawnCount, 0, 'Reading proposals is not approval');
      assert.match(await page.$eval('#loSources', node => node.textContent), /TODO and FIXME/);
      await page.screenshot({ path: path.join(output, 'leftover-desktop.png') });
      await page.evaluate(() => {
        const select = document.querySelector('#loAccount'); select.value = 'codex:default'; select.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await page.waitForFunction(() => document.querySelector('#loTasks [data-lo-start]').textContent === 'Start with Codex');
      await page.evaluate(() => { const button = document.querySelector('#loTasks [data-lo-start]'); button.click(); button.click(); });
      await page.waitForFunction(() => document.querySelector('#loTasks .lo-outcome')?.textContent.includes('Started with Codex'));
      assert.equal(spawnCount, 1, 'Double approval click must collapse while pending');
      assert.equal(bodies[0].engine, 'codex');
      assert.equal(bodies[0].cwd, folder);
      assert.equal(bodies[0].repo_path, folder);
      assert.equal(bodies[0].runtime, undefined);
      assert.equal(bodies[0].worktree, true);
      assert.match(bodies[0].task_key, /^leftover:[a-f0-9]+:codex$/);
      spawnMode = 'error';
      await page.evaluate(() => document.querySelectorAll('#loTasks [data-lo-start]')[1].click());
      await page.waitForFunction(() => Array.from(document.querySelectorAll('#loTasks .lo-outcome')).some(node => node.textContent.includes('Test start failure')));
      spawnMode = 'ok';
      await page.evaluate(() => document.querySelectorAll('#loTasks [data-lo-start]')[1].click());
      await page.waitForFunction(() => document.querySelectorAll('#loTasks [data-lo-start]:disabled').length === 2);
      assert.equal(bodies[1].task_key, bodies[2].task_key, 'Retry must use the same dedupe key');
      await page.evaluate(() => document.querySelector('#loEnabled').click());
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
      assert.equal(await page.$eval('#loTasks [data-lo-start]', node => node.disabled), true);
      mode = 'ready';

      // 3. Toggle off persists across reload and keeps the offer away even when approved.
      await page.evaluate(() => localStorage.setItem('ccc-popups-preview', 'leftover-offer,leftover-notification'));
      await page.setViewport({ width: 1280, height: 900 });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => !!window.cccLeftover);
      await page.evaluate(() => window.cccLeftover.refresh());
      assert.equal(await page.$('#cccLeftoverOffer:not([hidden])'), null, 'Toggle off must hide the offer');

      // 4. Approved (test harness only, via the popups.js preview key): offer card with proposals.
      dollars = 12.5;
      const spawnsBefore = spawnCount;
      await page.evaluate(() => { localStorage.setItem('ccc-leftover-enabled', '1'); localStorage.removeItem('ccc-leftover-notified-at'); });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => !!window.cccLeftover);
      await page.waitForFunction(() => { const card = document.querySelector('#cccLeftoverOffer'); return card && !card.hidden; }, { timeout: 15000 });
      await page.waitForFunction(() => document.querySelectorAll('#cccLeftoverOffer .lo-offer-task').length >= 3, { timeout: 25000 });
      const offerText = await page.$eval('#loOfferTitle', node => node.textContent);
      assert.equal(offerText, 'You have about $12.50 of Claude left that resets in 9h. Put it to work?');
      const offerCount = await page.$$eval('#cccLeftoverOffer .lo-offer-task', nodes => nodes.length);
      assert.ok(offerCount >= 3 && offerCount <= 5, 'Offer shows 3 to 5 tasks');
      assert.equal(spawnCount, spawnsBefore, 'Showing the offer never spawns');
      assert.equal(await page.evaluate(() => localStorage.getItem('ccc-leftover-notified-at')), null, 'No duplicate reminder while the offer is on screen');
      const card = await page.$('#cccLeftoverOffer');
      await card.screenshot({ path: path.join(output, 'leftover-offer-card.png') });
      await page.screenshot({ path: path.join(output, 'leftover-offer.png') });
      await page.evaluate(() => document.querySelector('#cccLeftoverOffer [data-lo-start]').click());
      await page.waitForFunction(() => document.querySelector('#cccLeftoverOffer .lo-outcome')?.textContent.includes('Started with Claude'));
      assert.equal(spawnCount, spawnsBefore + 1, 'One click starts exactly one task');
      const offerBody = bodies[bodies.length - 1];
      assert.equal(offerBody.engine, 'claude');
      assert.equal(offerBody.repo_path, folder);
      assert.match(offerBody.task_key, /^leftover:[a-f0-9]+:claude$/);
      await card.screenshot({ path: path.join(output, 'leftover-offer-started.png') });

      // 5. "Not now" hides the offer until the next reset.
      await page.click('#cccLeftoverOffer .lo-offer-close');
      assert.equal(await page.$eval('#cccLeftoverOffer', node => node.hidden), true);
      await page.evaluate(() => window.cccLeftover.refresh());
      assert.equal(await page.$eval('#cccLeftoverOffer', node => node.hidden), true);
      assert.equal(await page.evaluate(() => localStorage.getItem('ccc-leftover-notified-at')), null, '"Not now" also quiets the reminder');

      // 6. Reminder only (offer not approved): one cccNotify reminder, then nothing more that day.
      await page.evaluate(() => { localStorage.setItem('ccc-popups-preview', 'leftover-notification'); localStorage.removeItem('ccc-leftover-snooze'); });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => !!window.cccLeftover);
      await page.waitForFunction(() => !!localStorage.getItem('ccc-leftover-notified-at'), { timeout: 15000 });
      const toast = await page.waitForFunction(() => Array.from(document.querySelectorAll('#cccNotifyStack .ccc-toast')).find(node => node.textContent.includes('Put your plan to work')), { timeout: 10000 });
      assert.ok(toast, 'Reminder toast shows');
      assert.equal(await page.$('#cccLeftoverOffer:not([hidden])'), null, 'Reminder alone never shows the offer card');
      await page.screenshot({ path: path.join(output, 'leftover-reminder.png') });
      const notified = await page.evaluate(() => localStorage.getItem('ccc-leftover-notified-at'));
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => !!window.cccLeftover);
      await page.evaluate(() => window.cccLeftover.refresh());
      await new Promise(resolve => setTimeout(resolve, 1000));
      assert.equal(await page.evaluate(() => localStorage.getItem('ccc-leftover-notified-at')), notified, 'At most one reminder per day');
      assert.equal(await page.$$eval('#cccNotifyStack .ccc-toast', nodes => nodes.filter(node => node.textContent.includes('Put your plan to work')).length), 0);
      assert.equal(spawnCount, spawnsBefore + 1, 'Reminders never spawn');

      const result = { verdict: 'VERIFIED', headroom: 'GET /api/headroom contract fixture (rows)', proposals: 'real HTTP backend and scratch Git repo',
        spawn: 'intercepted approval boundary; no agent launched', spawnCount, offerTasks: offerCount, notifiedAt: notified,
        screenshots: ['leftover-offer-card.png', 'leftover-offer.png', 'leftover-offer-started.png', 'leftover-reminder.png', 'leftover-desktop.png', 'leftover-mobile.png'], pageErrors: errors };
      fs.writeFileSync(path.join(output, 'browser-result.json'), JSON.stringify(result, null, 2));
      console.log(JSON.stringify(result, null, 2));
    })()]);
  } finally {
    clearTimeout(timer);
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
