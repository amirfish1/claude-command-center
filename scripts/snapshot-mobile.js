// Mobile-viewport variant of snapshot.js for verifying phone layout changes.
// Usage: SNAPSHOT_OUT=before-mobile.png node snapshot-mobile.js
const path = require('path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

(async () => {
  const url = process.env.SNAPSHOT_URL || 'http://127.0.0.1:8090';
  const out = process.env.SNAPSHOT_OUT || 'snapshot-mobile.png';
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
    await page.goto(url, { waitUntil: 'load', timeout: 60000 });
    await page.waitForNetworkIdle({ idleTime: 750, timeout: 4000 }).catch(() => {});
    // The PWA install banner overlays the footer in a fresh headless profile.
    await page.evaluate(() => {
      const b = document.getElementById('pwaInstallBanner');
      if (b) b.hidden = true;
    });
    // Let the session list render a few rows before capturing.
    await page.waitForFunction(
      () => document.querySelectorAll('#convList .log-item, #convList [class*="conv-row"]').length > 0,
      { timeout: 15000 }
    ).catch(() => {});
    await new Promise((resolve) => setTimeout(resolve, 800));
    await page.screenshot({ path: out });
    // Report how much vertical space the chrome above/below the list occupies.
    const metrics = await page.evaluate(() => {
      const list = document.getElementById('convList');
      const r = list.getBoundingClientRect();
      return {
        viewportH: window.innerHeight,
        listTop: Math.round(r.top),
        listHeight: Math.round(r.height),
        chromeAbove: Math.round(r.top),
        chromeBelow: Math.round(window.innerHeight - r.bottom),
      };
    });
    console.log('[snapshot-mobile] wrote', out, JSON.stringify(metrics));
  } finally {
    await browser.close();
  }
})();
