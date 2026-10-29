// Ad-hoc verification for the free-key wizard: opens the dashboard on the
// test port, invokes cccFreeKeyWizard.open(), exercises the paste/submit
// flow, and screenshots the modal. Dev verification only — requires the
// managed free router (or none; the catalog still renders) on 127.0.0.1:3017.
// Usage: node scripts/verify-fkw.js   (env: WIZ_URL, WIZ_OUT)
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

(async () => {
  const url = process.env.WIZ_URL || 'http://127.0.0.1:9003';
  const out = process.env.WIZ_OUT || 'wizard.png';
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    headless: 'new',
    args: ['--window-size=1280,900'],
    defaultViewport: { width: 1280, height: 900 },
  });
  const page = await browser.newPage();
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForFunction('!!window.cccFreeKeyWizard', { timeout: 30000 });
    // Dismiss first-load overlays if present so they don't cover the modal.
    await page.evaluate(() => {
      document.querySelectorAll('button').forEach((b) => {
        const t = (b.textContent || '').toLowerCase();
        if (t.includes('skip') || t.includes('continue')) b.click();
      });
    });
    await page.evaluate(() => window.cccFreeKeyWizard.open({}));
    await page.waitForSelector('.ccc-fkw .fkw-card', { timeout: 20000 });
    await new Promise((r) => setTimeout(r, 1200));
    const cards = await page.evaluate(() =>
      Array.from(document.querySelectorAll('.fkw-card')).map((c) => c.dataset.platform));
    console.log('cards:', cards.join(','));

    // Interaction: bad format on the groq card -> inline error, no submit.
    await page.evaluate(() => {
      const card = document.querySelector('.fkw-card[data-platform="groq"]');
      card.querySelector('input').value = 'not a key';
      card.querySelector('.fkw-btn.primary').click();
    });
    await new Promise((r) => setTimeout(r, 400));
    const badMsg = await page.evaluate(() =>
      document.querySelector('.fkw-card[data-platform="groq"] .fkw-msg').textContent);
    console.log('bad-format msg:', JSON.stringify(badMsg));

    // Well-formed but fake key -> real provider probe -> rejected msg.
    await page.evaluate(() => {
      const card = document.querySelector('.fkw-card[data-platform="groq"]');
      card.querySelector('input').value = 'gsk_' + 'x'.repeat(48);
      card.querySelector('.fkw-btn.primary').click();
    });
    await page.waitForFunction(() => {
      const c = document.querySelector('.fkw-card[data-platform="groq"]');
      const m = c && c.querySelector('.fkw-msg');
      // busy text is cleared/replaced once the verdict lands (err/ok/warn class)
      return m && !c.classList.contains('is-busy') &&
             (m.classList.contains('err') || m.classList.contains('ok') ||
              m.classList.contains('warn'));
    }, { timeout: 60000 });
    const rejMsg = await page.evaluate(() =>
      document.querySelector('.fkw-card[data-platform="groq"] .fkw-msg').textContent);
    console.log('submit msg:', JSON.stringify(rejMsg));

    await page.screenshot({ path: out });
    console.log('shot:', out);
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error('FAIL', e.message); process.exit(1); });
