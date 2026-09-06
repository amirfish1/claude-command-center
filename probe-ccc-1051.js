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
    await new Promise(r => setTimeout(r, 3000));
    const info = await page.evaluate(() => {
      const el = document.getElementById('convSessionId');
      const sid = el && el.dataset.copySessionId;
      const rows = (Array.isArray(conversationsData) ? conversationsData : []).filter(c => c && (c.session_id === sid || c.id === sid));
      return {
        sid,
        matches: rows.length,
        first: rows[0] ? {
          id: rows[0].id, session_id: rows[0].session_id,
          jsonl_path: rows[0].jsonl_path || null,
          pathishKeys: Object.keys(rows[0]).filter(k => /path|file|log|jsonl|transcript/i.test(k)),
        } : null,
      };
    });
    console.log(JSON.stringify(info, null, 2));
  } finally {
    await browser.close();
  }
})();
