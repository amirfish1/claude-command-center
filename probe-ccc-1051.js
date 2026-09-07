const puppeteer = require('./require-puppeteer.js');
const { findChromePath } = require('./puppeteer-browser-config.js');
(async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.evaluateOnNewDocument(() => { localStorage.setItem('ccc-tour-done', '1'); });
    await page.goto('http://127.0.0.1:8090', { waitUntil: 'load', timeout: 120000 });
    await page.waitForFunction(() => document.querySelectorAll('.conv-item').length > 0, { timeout: 300000, polling: 1000 });
    await page.evaluate(() => document.querySelector('.conv-item').click());
    await page.waitForFunction(() => {
      const el = document.getElementById('convSessionId');
      return el && el.dataset.copySessionId;
    }, { timeout: 30000, polling: 500 });
    const info = await page.evaluate(() => {
      const el = document.getElementById('convSessionId');
      return {
        sid: el.dataset.copySessionId,
        transcriptPath: el.dataset.copyTranscriptPath || null,
        title: el.title,
      };
    });
    console.log(JSON.stringify(info, null, 2));
  } finally {
    await browser.close();
  }
})();
