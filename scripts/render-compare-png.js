// One-off: render docs/compare-ccc-vs-multica.html to docs/images/compare-ccc-vs-multica.png
const path = require('path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

(async () => {
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    args: ['--no-sandbox'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 960, height: 800, deviceScaleFactor: 2 });
    const fileUrl = 'file://' + path.resolve(__dirname, '..', 'docs/compare-ccc-vs-multica.html');
    await page.goto(fileUrl, { waitUntil: 'load' });
    await page.waitForNetworkIdle({ idleTime: 500, timeout: 3000 }).catch(() => {});
    const out = path.resolve(__dirname, '..', 'docs/images/compare-ccc-vs-multica.png');
    await page.screenshot({ path: out, fullPage: true });
    console.log('wrote', out);
  } finally {
    await browser.close();
  }
})();
