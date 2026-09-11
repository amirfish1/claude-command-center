// Run with: node tests/throughput_zoom_timezone.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const html = fs.readFileSync(require('node:path').join(__dirname, '../static/throughput.html'), 'utf8');
const helpers = html.slice(html.indexOf('    function chartRowTimeMs('), html.indexOf('    function updateChartZoomButton('));
const zoomScript = html.slice(html.indexOf('  // ── W87 zoom ladder:'), html.lastIndexOf('  </script>'));

(async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.emulateTimezone('America/Los_Angeles');
    await page.setContent('<div class="chart-container" style="position:relative;width:900px;height:350px"></div>');
    await page.addScriptTag({ content: `
      let activeThroughputEngine = 'codex';
      let lastRender = {summary: {hourly: [
        {hour: '2026-09-04 13:00', effective_input_tokens: 15554160, output_tokens: 330730, turns: 724, cost_usd: 87.63},
        {hour: '2026-09-04 20:00', effective_input_tokens: 1234, output_tokens: 0}
      ]}};
      Date.now = () => Date.parse('2026-09-06T12:00:00Z');
      window.fetch = async url => { window.drillUrl = url; return {json: async () => ({ok:true,sessions:[]})}; };
      ${helpers}
      ${zoomScript}
    ` });
    // Both the 48h hourly chart and the 3h overview emit unzoned UTC keys.
    for (const key of ['2026-09-04T20:00:00', '2026-09-04T20:00:00Z', '2026-09-04T20:00:00+00:00']) {
      await page.evaluate(hour => window.enterTputZoom({hour}), key);
      const title = await page.$eval('.tz-title', el => el.textContent);
      assert.match(title, /09:00–20:59/, `Wrong window for ${key}: ${title}`);
      const selected = `[data-ms="${Date.parse('2026-09-04T20:00:00Z')}"]`;
      const tooltip = await page.$eval(selected + ' title', el => el.textContent);
      assert.match(tooltip, /13:00: 15.9M cache-adj tokens · 724 calls · \$87.63/);
      await page.click(selected);
      const url = new URL(await page.evaluate(() => window.drillUrl), 'http://localhost');
      assert.equal(Number(url.searchParams.get('start')), Date.parse('2026-09-04T20:00:00Z') / 1000);
      assert.equal(Number(url.searchParams.get('end')), Date.parse('2026-09-04T21:00:00Z') / 1000);
      assert.equal(url.searchParams.get('engine'), 'codex');
    }
    console.log('PASS: UTC chart selections retain local hour, token total, and session API window');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
