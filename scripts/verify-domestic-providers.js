const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const base = process.env.CCC_DOMESTIC_URL || 'http://127.0.0.1:9203';
const out = process.env.CCC_DOMESTIC_OUT;
if (!out) throw new Error('Set CCC_DOMESTIC_OUT to an evidence directory.');
const writes = process.env.CCC_DOMESTIC_TEST_WRITES === '1';
const key = 'sk-test-XXXX-domestic-XXXX';

(async () => {
  fs.mkdirSync(out, { recursive: true });
  // Headless Chrome reports a touch-like device (hover: none), which turns on
  // CCC's 16px phone field sizing. Report a mouse so desktop shots are real.
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    args: ['--no-sandbox', '--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4'],
  });
  const deadline = setTimeout(() => { browser.close(); process.exitCode = 1; }, 120000);
  const evidence = { url: base, checks: [], screenshots: [] };
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 900 });
    // A fresh isolated HOME has pending agent-config consent, whose modal
    // would cover the cards. Snooze it with the same signature the page uses
    // (static/config-consent.js) so nothing is written to the test HOME.
    const consent = await fetch(base + '/api/config-consent').then((r) => r.json()).catch(() => ({}));
    const due = (i) => (i.auto_review === undefined ? i.needs_review : i.auto_review);
    const consentSig = (consent.items || []).filter(due).map((i) => i.id + ':' + i.status).sort().join('|')
      + (consent.notice && consent.notice.pending ? '|notice' : '');
    await page.evaluateOnNewDocument((sig) => {
      localStorage.setItem('ccc-onboarded', '1');
      localStorage.setItem('ccc-tour-done', '1');
      localStorage.setItem('ccc-sounds-enabled', '0');
      localStorage.setItem('ccc-config-consent-snooze', JSON.stringify({ sig, until: Date.now() + 864e5 }));
    }, consentSig);
    await page.goto(base + '/?ccc_settings=free', { waitUntil: 'load', timeout: 60000 });
    await page.waitForSelector('#fsDomesticProviders .dp-card', { timeout: 30000 });
    const count = await page.$$eval('#fsDomesticProviders .dp-card', (cards) => cards.length);
    assert.equal(count, 5);
    assert.equal(await page.$$eval('#fsDomesticProviders .dp-card .dp-label:first-of-type :is(option, .dp-region-fixed)', (rows) => rows.length), 9);
    evidence.checks.push('Settings renders five paid provider cards and nine account-region options');
    const scope = '#fsDomesticProviders [data-family="kimi"]';
    await page.select(scope + ' select', 'kimi-cn');
    assert.equal(await page.$eval(scope + ' .dp-endpoint', (el) => el.textContent), 'https://api.moonshot.cn/anthropic');
    assert.equal(await page.$eval(scope + ' [data-dp-signup]', (el) => el.href), 'https://platform.kimi.com/console/api-keys');
    await page.type(scope + ' input', 'bad key');
    await page.click(scope + ' [data-dp-save]');
    await page.waitForFunction((sel) => document.querySelector(sel + ' .dp-message').textContent.includes('full API key'), {}, scope);
    evidence.checks.push('Region switch changes endpoint and signup link; invalid paste shows a clear error');
    await page.select(scope + ' select', 'kimi-intl');
    assert.equal(await page.$eval(scope + ' input', (el) => el.value), '');
    if (writes) {
      const initial = await page.evaluate(async () => (await fetch('/api/domestic-providers')).json());
      assert.equal(initial.backend, 'encrypted-file', 'Test writes require an isolated server with Keychain disabled');
      assert.equal(initial.presets.find((p) => p.id === 'kimi-intl').configured, false, 'Refusing to replace an existing key');
      await page.type(scope + ' input', key);
      await page.click(scope + ' [data-dp-save]');
      await page.waitForFunction((sel) => document.querySelector(sel + ' .dp-message').textContent.includes('first run'), {}, scope);
      assert.equal(await page.$eval(scope + ' input', (el) => el.value), '');
      const models = await page.evaluate(async () => (await fetch('/api/engines/models')).json());
      assert.equal(models.catalog.claude.models.find((m) => m.id === 'byok/kimi-intl/kimi-k3').available, true);
      assert(!JSON.stringify(models).includes(key));
      const rejection = await page.evaluate(async () => (await fetch('/api/sessions/spawn', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ engine: 'claude', model: 'byok/kimi-intl/kimi-k3', runtime: 'free', prompt: 'Say hello.' }),
      })).json());
      assert.equal(rejection.code, 'paid_preset_not_free');
      evidence.checks.push('Fake key saves only in isolated encrypted storage, clears input, updates model catalogue, and refuses free+paid');
    }
    await page.$eval('#fsDomesticProviders', (el) => el.scrollIntoView({ block: 'start' }));
    const settingsShot = path.join(out, 'domestic-settings.png');
    await page.screenshot({ path: settingsShot });
    evidence.screenshots.push(settingsShot);
    if (writes) {
      await page.click(scope + ' [data-dp-remove]');
      assert.equal(await page.$eval(scope + ' [data-dp-remove]', (el) => el.textContent), 'Remove saved key');
      await page.click(scope + ' [data-dp-remove]');
      await page.waitForFunction((sel) => document.querySelector(sel + ' .dp-message').textContent === 'Key removed.', {}, scope);
      evidence.checks.push('Removing the newly created fake key requires a second confirmation');
    }
    await page.evaluate(() => {
      document.querySelectorAll('button').forEach((el) => {
        if (el.getAttribute('aria-label') === 'Close settings') el.click();
      });
      window.cccFreeKeyWizard.open();
    });
    await page.waitForSelector('.ccc-fkw-modal .dp-wizard-details summary');
    await page.click('.ccc-fkw-modal .dp-wizard-details summary');
    await page.waitForSelector('.ccc-fkw-modal .dp-card');
    assert.equal(await page.$$eval('.ccc-fkw-modal .dp-card', (cards) => cards.length), 5);
    assert.equal(await page.$eval('.ccc-fkw-modal .fkw-count', (el) => el.textContent), 'Connect at least one to go fully free');
    await page.$eval('.ccc-fkw-modal .dp-wizard-details', (el) => el.scrollIntoView({ block: 'start' }));
    const wizardShot = path.join(out, 'domestic-wizard.png');
    await page.screenshot({ path: wizardShot });
    evidence.screenshots.push(wizardShot);
    evidence.checks.push('Key wizard exposes the paid section without counting a paid key as a free connection');
    await page.setViewport({ width: 390, height: 844 });
    assert(await page.$eval('.ccc-fkw-modal .dp-root', (el) => el.scrollWidth <= el.clientWidth + 1));
    await page.$eval('.ccc-fkw-modal .dp-wizard-details', (el) => el.scrollIntoView({ block: 'start' }));
    const mobileShot = path.join(out, 'domestic-wizard-mobile.png');
    await page.screenshot({ path: mobileShot });
    evidence.screenshots.push(mobileShot);
    evidence.checks.push('Paid-key cards fit a mobile viewport without horizontal overflow');
    evidence.verdict = 'VERIFIED';
    fs.writeFileSync(path.join(out, 'domestic-browser-result.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence, null, 2));
  } finally {
    clearTimeout(deadline);
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
