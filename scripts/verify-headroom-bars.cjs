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
    { id: 'claude:default', engine: 'claude', label: 'Claude', account: 'default', available: true, stale: false, unlimited: false, percent_left: 38.0, resets_at: Math.round((now + 9 * 3600000 + 12 * 60000) / 1000), hours_to_reset: 9.2, burn_pct_per_hour: 2.1, projected_expiring_pct: 18.7, expiring_usd_estimate: 42.5, expiring_tokens_estimate: 9300000, source: 'quota' },
    { id: 'codex:default', engine: 'codex', label: 'Codex', account: 'default', available: true, stale: false, unlimited: false, percent_left: 72.4, resets_at: Math.round((now + 3 * 86400000 + 5 * 3600000) / 1000), hours_to_reset: 77, burn_pct_per_hour: null, projected_expiring_pct: null, expiring_usd_estimate: null, expiring_tokens_estimate: null, source: 'quota' },
    { id: 'devin:default', engine: 'devin', label: 'Devin', account: 'default', available: false, stale: false, unlimited: false, percent_left: null, resets_at: null, hours_to_reset: null, burn_pct_per_hour: null, projected_expiring_pct: null, expiring_usd_estimate: null, expiring_tokens_estimate: null, source: 'quota' },
    { id: 'free_router:default', engine: 'free_router', label: 'Free models', account: 'default', available: true, stale: false, unlimited: true, percent_left: null, resets_at: null, hours_to_reset: null, burn_pct_per_hour: null, projected_expiring_pct: null, expiring_usd_estimate: null, expiring_tokens_estimate: null, source: 'free_router' },
  ],
};
const lowFixture = JSON.parse(JSON.stringify(fixture));
lowFixture.rows[0].percent_left = 7.6;
lowFixture.rows[1].percent_left = 24;


(async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  const deadline = setTimeout(() => { browser.close(); }, 90000);
  const evidence = { url, fixture: 'M05 contract fixture, not live provider usage', screenshots: [], errors };
  try {
    async function open({ intercepted = false, theme = 'dark', initialPhase = 200, viewport = { width: 1440, height: 1000 }, body = fixture } = {}) {
      const page = await browser.newPage();
      await page.setViewport(viewport);
      await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
      await page.evaluateOnNewDocument(theme => {
        localStorage.setItem('ccc-tour-done', '1');
        localStorage.setItem('ccc-ui-mode', 'advanced');
        localStorage.setItem('ccc-session-view', 'list');
        localStorage.setItem('ccc-theme', theme);
        localStorage.setItem('ccc-sounds-enabled', 'false');
      }, theme);
      let headroomRequests = 0;
      let phase = initialPhase;
      if (intercepted) await page.setRequestInterception(true);
      page.on('request', request => {
        if (new URL(request.url()).pathname === '/api/headroom') {
          headroomRequests++;
          if (intercepted) {
            request.respond({ status: phase, contentType: 'application/json', body: JSON.stringify(phase === 200 ? body : { error: 'test response' }) });
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
      if (rail) await rail.click().catch(() => {}); // the rail can re-render under us
      // First-run dialogs (config consent, tours) can open a few seconds
      // after load; give them time, then dismiss.
      await new Promise(resolve => setTimeout(resolve, 2500));
      await dismiss(page);
      return { page, count: () => headroomRequests, setPhase: value => { phase = value; } };
    }
    async function dismiss(page) {
      await page.evaluate(() => {
        for (const dialog of document.querySelectorAll('[role="dialog"], .upd-overlay')) {
          if (!dialog.getClientRects().length) continue;
          const close = Array.from(dialog.querySelectorAll('button')).find(button => /skip|close|continue|not now|×/i.test(button.textContent + ' ' + (button.getAttribute('aria-label') || '')));
          if (close) close.click();
        }
      });
    }
    async function screenshot(page, name, clipSelector) {
      await dismiss(page);
      const file = path.join(out, name);
      let clip;
      if (clipSelector) {
        const box = await page.$eval(clipSelector, el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; });
        clip = { x: Math.max(0, box.x), y: Math.max(0, box.y), width: box.width, height: Math.max(box.height, 260) };
      }
      await page.screenshot({ path: file, clip });
      evidence.screenshots.push(file);
    }
    // 404 run. A real server without the engine is simulated by answering
    // 404 (this branch includes the engine, so the route itself exists).
    const missing = await open({ intercepted: true, initialPhase: 404 });
    await missing.page.waitForFunction(() => document.getElementById('headroomBars').hidden);
    await missing.page.evaluate(() => window.cccHeadroom.poll());
    assert.equal(missing.count(), 1, '404 must stop repeated headroom requests');
    assert.equal(await missing.page.$$eval('#headroomBars .hb-chip', els => els.length), 0);
    await screenshot(missing.page, 'headroom-404.png');
    evidence.missingBackendHidden = true;
    await missing.page.close();

    const live = await open({ intercepted: true });
    await live.page.waitForSelector('#headroomBars .hb-chip', { visible: true, timeout: 10000 });
    const state = await live.page.evaluate(() => Array.from(document.querySelectorAll('#headroomBars .hb-chip')).map(el => ({
      id: el.dataset.accountId, text: el.textContent, className: el.className,
      role: el.getAttribute('role'), value: el.getAttribute('aria-valuenow'),
      tip: el.dataset.tip, fill: el.querySelector('.hb-fill').style.width,
    })));
    assert.equal(state.length, 3, 'Devin (available:false) must be hidden');
    assert.deepEqual(state.map(s => s.id), ['claude:default', 'codex:default', 'free_router:default']);
    for (const [index, risk, percent, shown] of [[0, 'hb-ok', '38', '38'], [1, 'hb-ok', '72.4', '72']]) {
      assert.ok(state[index].className.includes(risk), state[index].className);
      assert.equal(state[index].role, 'progressbar');
      assert.equal(state[index].value, percent);
      assert.ok(state[index].text.includes(shown + '% left'), state[index].text);
    }
    assert.ok(state[0].text.includes('Resets in 9h'), state[0].text);
    assert.equal(state[2].role, 'group');
    assert.equal(state[2].value, null);
    assert.ok(state[2].text.includes('Free models') && state[2].text.includes('Varies by provider') && state[2].className.includes('hb-unknown'));
    assert.ok(state[0].tip.includes('API-priced work') && state[0].tip.includes('not money or a refund'));
    assert.ok(state[0].tip.includes('9,300,000 tokens'));
    evidence.state = state;
    await screenshot(live.page, 'headroom-dark.png');
    await screenshot(live.page, 'headroom-header-dark.png', '.sidebar-header');

    // Tooltip on hover, then on keyboard focus.
    await dismiss(live.page);
    await live.page.hover('#headroomBars .hb-chip');
    try {
      await live.page.waitForSelector('#headroomTip:not([hidden])', { timeout: 5000 });
    } catch (error) {
      await live.page.screenshot({ path: path.join(out, 'debug-hover.png') });
      const hit = await live.page.evaluate(() => { const r = document.querySelector('#headroomBars .hb-chip').getBoundingClientRect(); const el = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return el && (el.id || el.className); });
      throw new Error('Tooltip did not open on hover; element at chip center: ' + hit);
    }
    const tipText = await live.page.$eval('#headroomTip', el => el.textContent);
    assert.ok(tipText.includes('38% left.') && tipText.includes('$42.50'), tipText);
    const tipBox = await live.page.$eval('#headroomTip', el => { const r = el.getBoundingClientRect(); return { top: r.top, left: r.left, right: r.right, bottom: r.bottom }; });
    assert.ok(tipBox.left >= 0 && tipBox.right <= 1440 && tipBox.bottom <= 1000, 'Tooltip must stay on screen');
    {
      const file = path.join(out, 'headroom-tooltip.png');
      const hdr = await live.page.$eval('.sidebar-header', el => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width }; });
      await live.page.screenshot({ path: file, clip: { x: 0, y: hdr.y, width: Math.max(hdr.width, tipBox.right) + 24, height: tipBox.bottom - hdr.y + 16 } });
      evidence.screenshots.push(file);
    }
    await live.page.mouse.move(900, 900);
    await live.page.waitForSelector('#headroomTip[hidden]');
    evidence.tooltip = tipText;

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
    await live.page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().width <= 300);
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
    await screenshot(light.page, 'headroom-header-light.png', '.sidebar-header');
    light.setPhase(404);
    await light.page.evaluate(() => window.cccHeadroom.poll());
    assert.equal(await light.page.$eval('#headroomBars', el => el.hidden && !el.querySelector('.hb-chip')), true);
    const count = light.count();
    await light.page.evaluate(() => window.cccHeadroom.poll());
    assert.equal(light.count(), count);
    evidence.later404ClearsCachedReadings = true;
    await light.page.close();

    // Risk colors: low readings go amber and red.
    const low = await open({ intercepted: true, body: lowFixture });
    await low.page.waitForSelector('#headroomBars .hb-chip', { visible: true, timeout: 10000 });
    const lowClasses = await low.page.$$eval('#headroomBars .hb-chip', els => els.map(el => el.className));
    assert.ok(lowClasses[0].includes('hb-low') && lowClasses[1].includes('hb-warn'), lowClasses.join(' | '));
    await screenshot(low.page, 'headroom-header-risk.png', '.sidebar-header');
    await low.page.close();

    // Phone width (the regular sidebar header, not Simple mode).
    const phone = await open({ intercepted: true, viewport: { width: 390, height: 844, isMobile: true, hasTouch: true } });
    const phoneVisible = await phone.page.$eval('#headroomBars', el => !el.hidden && el.getClientRects().length > 0).catch(() => false);
    evidence.phoneStripVisible = phoneVisible;
    if (phoneVisible) {
      const phoneOverflow = await phone.page.evaluate(() => Array.from(document.querySelectorAll('#headroomBars, #headroomBars .hb-chip')).some(el => el.scrollWidth > el.clientWidth + 1));
      assert.equal(phoneOverflow, false, 'Strip must fit a phone-width header');
    }
    await screenshot(phone.page, 'headroom-phone.png');
    await phone.page.close();
    evidence.verdict = 'VERIFIED';
    fs.writeFileSync(path.join(out, 'browser-results.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence, null, 2));
  } finally {
    clearTimeout(deadline);
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
