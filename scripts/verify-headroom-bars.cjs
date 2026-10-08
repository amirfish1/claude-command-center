const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const url = process.env.HEADROOM_URL;
const out = process.env.HEADROOM_OUT;
assert.ok(url && out, 'Set HEADROOM_URL and HEADROOM_OUT to the isolated dashboard and evidence directory');
assert.ok(fs.statSync(out).isDirectory(), 'Evidence directory must already exist');
const errors = [];
const now = Date.now();
const fixture = {
  ok: true,
  generated_at: new Date(now).toISOString(),
  rows: [
    { id: 'claude:default', engine: 'claude', label: 'Claude', account: 'default', available: true, stale: false, unlimited: false, percent_left: 64, resets_at: (now + 9 * 3600000) / 1000, hours_to_reset: 9, burn_pct_per_hour: 2.1, projected_expiring_pct: 20, expiring_usd_estimate: 12.5, expiring_tokens_estimate: 120000, source: 'calibration' },
    { id: 'codex:default', engine: 'codex', label: 'Codex', account: 'default', available: true, stale: false, unlimited: false, percent_left: 22, resets_at: (now + 80 * 60000) / 1000, hours_to_reset: 80 / 60, burn_pct_per_hour: null, projected_expiring_pct: null, expiring_usd_estimate: null, expiring_tokens_estimate: null, source: 'quota' },
    { id: 'kimi:default', engine: 'kimi', label: 'Kimi', account: 'default', available: true, stale: false, unlimited: false, percent_left: 4, resets_at: (now + 49 * 3600000) / 1000, hours_to_reset: 49, burn_pct_per_hour: null, projected_expiring_pct: null, expiring_usd_estimate: null, expiring_tokens_estimate: null, source: 'quota' },
    { id: 'devin:default', engine: 'devin', label: 'Devin', account: 'default', available: false, stale: false, unlimited: false, percent_left: null, resets_at: null, hours_to_reset: null, burn_pct_per_hour: null, projected_expiring_pct: null, expiring_usd_estimate: null, expiring_tokens_estimate: null, source: 'quota' },
    { id: 'free_router:default', engine: 'free_router', label: 'Free models', account: 'default', available: true, stale: false, unlimited: true, percent_left: null, resets_at: null, hours_to_reset: null, burn_pct_per_hour: null, projected_expiring_pct: null, expiring_usd_estimate: null, expiring_tokens_estimate: null, source: 'free_router' },
  ],
};

(async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  const deadline = setTimeout(() => { browser.close(); }, 90000);
  const evidence = { url, fixture: 'M05 contract fixture, not live provider usage', screenshots: [], errors };
  try {
    async function open({ intercepted = false, theme = 'dark' } = {}) {
      const page = await browser.newPage();
      await page.setViewport({ width: 1440, height: 1000 });
      await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
      await page.evaluateOnNewDocument(theme => {
        localStorage.setItem('ccc-tour-done', '1');
        localStorage.setItem('ccc-ui-mode', 'advanced');
        localStorage.setItem('ccc-session-view', 'list');
        localStorage.setItem('ccc-theme', theme);
        localStorage.setItem('ccc-sounds-enabled', 'false');
      }, theme);
      let headroomRequests = 0;
      let phase = 200;
      if (intercepted) await page.setRequestInterception(true);
      page.on('request', request => {
        if (new URL(request.url()).pathname === '/api/headroom') {
          headroomRequests++;
          if (intercepted) {
            request.respond({ status: phase, contentType: 'application/json', body: JSON.stringify(phase === 200 ? fixture : { error: 'test response' }) });
            return;
          }
        }
        if (intercepted) request.continue();
      });
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(url, { waitUntil: 'load', timeout: 30000 });
      await page.waitForFunction(() => !!window.cccHeadroom, { timeout: 15000 });
      await page.waitForFunction(() => {
        const el = document.getElementById('cccLoadingOverlay');
        return !el || getComputedStyle(el).display === 'none';
      }, { timeout: 30000 });
      const rail = await page.$('[data-app-id="sessions"]');
      if (rail) await rail.click();
      await page.evaluate(() => {
        for (const dialog of document.querySelectorAll('[role="dialog"]')) {
          if (!dialog.getClientRects().length) continue;
          const close = Array.from(dialog.querySelectorAll('button')).find(button => /skip|close|continue|×/i.test(button.textContent + ' ' + (button.getAttribute('aria-label') || '')));
          if (close) close.click();
        }
      });
      return { page, count: () => headroomRequests, setPhase: value => { phase = value; } };
    }
    async function screenshot(page, name) {
      const file = path.join(out, name);
      await page.screenshot({ path: file });
      evidence.screenshots.push(file);
    }
    const missing = await open();
    await missing.page.waitForFunction(() => document.getElementById('headroomBars').hidden);
    await missing.page.evaluate(() => window.cccHeadroom.poll());
    assert.equal(missing.count(), 1, '404 must stop repeated headroom requests');
    await screenshot(missing.page, 'headroom-404.png');
    evidence.missingBackendHidden = true;
    await missing.page.close();

    const live = await open({ intercepted: true });
    await live.page.waitForSelector('#headroomBars .hb-chip', { visible: true, timeout: 10000 });
    const state = await live.page.evaluate(() => Array.from(document.querySelectorAll('#headroomBars .hb-chip')).map(el => ({
      id: el.dataset.accountId, text: el.textContent, className: el.className,
      role: el.getAttribute('role'), value: el.getAttribute('aria-valuenow'),
      title: el.title, fill: el.querySelector('.hb-fill').style.width,
    })));
    assert.equal(state.length, 5);
    for (const [index, risk, percent] of [[0, 'hb-ok', '64'], [1, 'hb-warn', '22'], [2, 'hb-low', '4']]) {
      assert.ok(state[index].className.includes(risk));
      assert.equal(state[index].value, percent);
      assert.ok(state[index].text.includes(percent + '% left'));
    }
    for (const index of [3, 4]) {
      assert.equal(state[index].value, null);
      assert.equal(state[index].fill, '0%');
      assert.equal(state[index].role, 'group');
      assert.ok(state[index].text.includes('Not available'));
    }
    assert.ok(state[0].title.includes('API-priced work') && state[0].title.includes('not money or a refund'));
    assert.ok(state[0].title.includes('120,000 tokens'));
    evidence.state = state;
    await screenshot(live.page, 'headroom-dark.png');

    await live.page.evaluate(() => {
      window.headroomFocusedNode = document.querySelector('#headroomBars .hb-chip');
      window.headroomFocusedNode.focus();
    });
    await live.page.evaluate(() => window.cccHeadroom.poll());
    assert.equal(await live.page.evaluate(() => document.activeElement === window.headroomFocusedNode), true);
    assert.equal(await live.page.$eval('.headroom-bars .hb-fill', el => getComputedStyle(el).transitionDuration), '0s');
    const handle = await live.page.$('#sidebarResizer');
    const box = await handle.boundingBox();
    assert.ok(box, 'Sidebar resize handle must be visible');
    await live.page.mouse.move(box.x + box.width / 2, box.y + 100);
    await live.page.mouse.down();
    await live.page.mouse.move(box.x - 140, box.y + 100, { steps: 8 });
    await live.page.mouse.up();
    await live.page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().width <= 260);
    const overflow = await live.page.evaluate(() => Array.from(document.querySelectorAll('#headroomBars, #headroomBars .hb-chip')).some(el => el.scrollWidth > el.clientWidth + 1));
    assert.equal(overflow, false, 'Panel must fit the minimum-width sidebar');
    await screenshot(live.page, 'headroom-narrow.png');
    evidence.keyboardFocusRetained = true;
    evidence.reducedMotion = true;
    evidence.minimumWidthFits = true;
    live.setPhase(500);
    await live.page.evaluate(() => window.cccHeadroom.poll());
    await live.page.waitForSelector('#headroomBars .hb-stale');
    assert.equal(await live.page.$$eval('#headroomBars .hb-chip', els => els.every(el => el.classList.contains('hb-off'))), true);
    await screenshot(live.page, 'headroom-stale.png');
    evidence.networkErrorsMuteCachedReadings = true;
    await live.page.close();

    const light = await open({ intercepted: true, theme: 'light' });
    await light.page.waitForSelector('#headroomBars .hb-chip', { visible: true, timeout: 10000 });
    await screenshot(light.page, 'headroom-light.png');
    light.setPhase(404);
    await light.page.evaluate(() => window.cccHeadroom.poll());
    assert.equal(await light.page.$eval('#headroomBars', el => el.hidden && !el.querySelector('.hb-chip')), true);
    const count = light.count();
    await light.page.evaluate(() => window.cccHeadroom.poll());
    assert.equal(light.count(), count);
    evidence.later404ClearsCachedReadings = true;
    await light.page.close();
    evidence.verdict = 'VERIFIED';
    fs.writeFileSync(path.join(out, 'browser-results.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence, null, 2));
  } finally {
    clearTimeout(deadline);
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
