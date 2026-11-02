const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = app.indexOf('  async function _loadCompleteToolOutput(');
const loader = app.slice(start, app.indexOf('  // Rebuild the tool grouping', start));

test('expanding a tool loads its full output once and allows retry after failure', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    let reads = 0;
    const full = 'A complete brief\n'.repeat(200) + 'END OF BRIEF';
    await page.setRequestInterception(true);
    page.on('request', request => {
      if (request.url().endsWith('/tool-output')) {
        reads++;
        request.respond({ status: reads === 1 ? 503 : 200, contentType: 'application/json',
          body: JSON.stringify(reads === 1 ? { ok: false, error: 'Try again' } : { ok: true, output: full }) });
      } else request.respond({ status: 200, contentType: 'text/html', body: `<div class="kimi-tool">
        <button class="kimi-tool-head" onclick="this.parentElement.classList.toggle('open')">Bash</button>
        <div><pre class="kimi-tool-io is-output">Short preview…</pre>
        <button data-output-url="/tool-output">Show full output</button></div></div>` });
    });
    await page.goto('http://fixture.test/');
    await page.evaluate(code => { (0, eval)(code); }, loader);
    await page.click('.kimi-tool-head');
    await page.waitForFunction(() => document.querySelector('[data-output-url]')?.textContent === 'Retry loading full output');
    await page.click('[data-output-url]');
    await page.waitForFunction(() => !document.querySelector('[data-output-url]'));
    assert.equal(await page.$eval('.is-output', el => el.textContent), full);
    await page.click('.kimi-tool-head');
    await page.click('.kimi-tool-head');
    assert.equal(reads, 2);
  } finally { await browser.close(); }
});
