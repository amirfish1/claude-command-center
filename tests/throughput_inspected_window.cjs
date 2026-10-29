// Run with: node tests/throughput_inspected_window.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const html = fs.readFileSync(require('node:path').join(__dirname, '../static/throughput.html'), 'utf8');
const helperStart = html.indexOf('    function chartRowTimeMs(');
const helpers = html.slice(helperStart, html.indexOf('    function updateChartZoomButton(', helperStart));
const zoomStart = html.indexOf('  // ── W87 zoom ladder:');
const zoomScript = html.slice(zoomStart, html.indexOf('  </script>', zoomStart));
(async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.emulateTimezone('UTC');
    await page.setContent('<div class="chart-container" style="position:relative;width:900px;height:350px"></div><section id="inspected-throughput-section" hidden></section>');
    await page.addScriptTag({content: `
      let activeThroughputEngine = 'codex';
      let lastRender = {summary: {hourly: [{hour:'2026-10-18 07:00', effective_input_tokens:100, output_tokens:0, turns:7}]}};
      Date.now = () => Date.parse('2026-10-18T16:00:00Z');
      window.fetch = async url => {
        const query = new URL(url, 'http://localhost').searchParams;
        const isHour = Number(query.get('end')) - Number(query.get('start')) === 3600;
        window.lastInspectionQuery = url;
        return {ok:true, json:async () => ({ok:true, session_count:1,
          totals:{turns:isHour ? 7 : 100, cache_adjusted_tokens:isHour ? 200 : 9000},
          sessions:[{session_id:'example-session', session_name:'Example task', models:['gpt-test'],
            turns:isHour ? 7 : 100, raw_context_tokens:isHour ? 1000 : 90000,
            cache_adjusted_tokens:isHour ? 200 : 9000, cost_usd:1}]})};
      };
      ${helpers}
      ${zoomScript}
    `});
    await page.evaluate(() => enterTputZoom({hour:'2026-10-18T07:00:00Z'}));
    await page.waitForSelector('.inspected-window-table');
    assert.match(await page.$eval('#inspected-throughput-section', el => el.textContent), /100 turns \(model calls\)/);
    assert.match(await page.$eval('#tput-zoom-overlay', el => el.textContent), /7 turns/);
    // Queue a resize while in the bar view, then enter an hour before its debounce fires.
    await page.evaluate(() => window.dispatchEvent(new Event('resize')));
    await page.click('.tz-bar[data-ms="1792306800000"]');
    await page.waitForFunction(() => document.querySelector('#inspected-throughput-section').textContent.includes('7 turns (model calls)'));
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 300)));
    assert.equal(await page.$eval('.tz-title', el => el.textContent.includes('sessions')), true);
    const content = await page.$eval('#inspected-throughput-section', el => el.textContent);
    assert.match(content, /07:00–08:00 \(UTC\)/);
    assert.match(content, /only turns completed in this window/);
    assert.doesNotMatch(content, /100 turns/);
    const cells = await page.$$eval('.inspected-window-table tbody td', cells => cells.map(cell => cell.textContent));
    assert.deepEqual(cells.slice(1, 5), ['7', '1.0k', '200', '100.0%']);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    assert.equal(await page.$eval('#inspected-throughput-section', el => el.hidden), true);
    assert.equal(await page.evaluate(() => document.body.classList.contains('throughput-inspecting')), false);
    console.log('PASS: inspected sessions narrow from12h to1h, show scoped turns/tokens, and restore on exit');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
