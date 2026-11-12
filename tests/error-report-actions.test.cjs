const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const html = fs.readFileSync(require.resolve('../static/index.html'), 'utf8');
function between(text, start, end) {
  const a = text.indexOf(start), b = text.indexOf(end, a + start.length);
  assert(a >= 0 && b > a, start);
  return text.slice(a, b);
}
const report = between(app, '  // ── In-app problem reporting', '  // Legacy default-control adapters.');
const toast = between(app, '  function showOpToast(', '  // Soft-block dialog:');
const wake = between(app, '  function renderInlineWakeError(', '  // Render the wake OUTCOME');
const modal = between(html, '  <div id="bugReportModal"', '  <!-- Share modal:');
for (const width of [1280, 390]) test('error report review works without a local queue at ' + width, async () => {
  const browser = await puppeteer.launch({executablePath: findChromePath(), args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width, height: 900});
    await page.setRequestInterception(true);
    page.on('request', r => r.respond({status: 200, contentType: 'text/html', body: '<!doctype html><title>Report fixture</title>'}));
    await page.goto('http://ccc.test/');
    await page.setContent('<style>' + css + '</style><div id="view"></div>' + modal);
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addScriptTag({content: `
      const currentSession = null;
      const debugModeEnabled = () => false;
      const isImeKey = () => false;
      const clearOptimisticAgentIndicator = () => {};
      const _removePriorWakeStatus = view => view.replaceChildren();
      const scrollConversationToEnd = () => {};
      window.requests = [];
      window.fetch = async (url, opts = {}) => {
        requests.push({url, method: opts.method || 'GET'});
        if (url === '/api/version') return {ok: true, json: async () => ({version: 'test'})};
        if (url.startsWith('/api/telemetry/health-report')) return {ok: true, json: async () => ({counts: 1})};
        throw Error('Unexpected request: ' + url);
      };
      ${toast}
      ${wake}
      ${report}
      renderInlineWakeError(document.getElementById('view'), 'Resume failed: repo_not_allowed: /example/private-project is not a known repo.');
    `});
    await page.waitForSelector('#view .error-report-btn');
    assert.equal(await page.evaluate(() => requests.length), 0);
    assert.equal(await page.$eval('#view .error-report-btn', b => {const r=b.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth;}), true);
    if (width === 390) assert(await page.$eval('#view .error-report-btn', b => b.getBoundingClientRect().height) >= 44);
    await page.screenshot({path: '/tmp/ccc-error-action-' + width + '.png'});
    await page.click('#view .error-report-btn');
    await page.waitForSelector('#bugReportModal.open');
    assert.match(await page.$eval('#bugReportDescInput', e => e.value), /Resume failed:.*private-project/);
    assert.match(await page.$eval('#bugReportPrivacyCopy', e => e.textContent), /private paths/);
    await page.waitForFunction(() => document.getElementById('bugPreview').value.includes('counts'));
    assert.match(await page.$eval('#bugPreview', e => e.value), /Resume failed:/);
    assert.equal(await page.$eval('input[name="bugDest"]:checked', e => e.value), 'private');
    await page.click('input[name="bugDest"][value="github"]');
    assert.match(await page.$eval('#bugReportDestinationSection', e => e.textContent), /Anyone on the internet/);
    await page.$eval('#bugReportDescInput', e => {e.value='Sanitized error'; e.dispatchEvent(new Event('input'));});
    assert.match(await page.$eval('#bugPreview', e => e.value), /Sanitized error/);
    assert.doesNotMatch(await page.$eval('#bugPreview', e => e.value), /private-project/);
    assert.equal(await page.evaluate(() => requests.some(r => r.method === 'POST' || r.url.includes('queue'))), false);
    await page.screenshot({path: '/tmp/ccc-error-report-' + width + '.png'});
    await page.click('#bugReportCancelBtn');
    await page.evaluate(() => showOpToast('Open failed: fixture error', 'error', {label: 'Retry', onClick: () => {window.retried=true;}}));
    assert.equal(await page.$eval('.op-toast', e => {const r=e.getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth;}), true);
    await page.click('.op-toast .error-report-btn');
    assert.match(await page.$eval('#bugReportDescInput', e => e.value), /^Open failed: fixture error/);
    await page.click('#bugReportCancelBtn');
    await page.evaluate(() => [...document.querySelectorAll('.op-toast button')].find(b => b.textContent === 'Retry').click());
    assert.equal(await page.evaluate(() => window.retried), true);
    await page.evaluate(() => showOpToast('Saved', 'success'));
    assert.equal(await page.$('.op-toast .error-report-btn'), null);
    assert.deepEqual(errors, []);
  } finally {await browser.close();}
});
