const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = fs.readFileSync('static/app.js', 'utf8');
const html = fs.readFileSync('static/index.html', 'utf8');
const renderer = app.slice(app.indexOf('  function renderDrainPausedBanner()'), app.indexOf('  async function refreshControlPlaneStatus()'));
const markup = html.slice(html.indexOf('  <div id="drainPausedBanner"'), html.indexOf('  <!-- PWA install banner'));
const css = fs.readFileSync('static/app.css', 'utf8');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'restart-banner-'));
let browser;
test.before(async () => { browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] }); });
test.after(async () => { await browser?.close(); console.log('Restart banner screenshots:', scratch); });
for (const width of [1440, 390]) test('temporary restart pauses need no Resume click at ' + width, async t => {
  const page = await browser.newPage(); t.after(() => page.close());
  await page.setViewport({width, height: 900});
  await page.setContent('<meta name="viewport" content="width=device-width,initial-scale=1"><style>' + css + '</style>' + markup);
  await page.evaluate(renderer => {
    window.renderWorkerBadge = () => {};
    for (const id of ['drainPausedBanner', 'drainPausedBannerText', 'drainPausedResumeBtn']) window['$' + id] = document.getElementById(id);
    window.$controlPlaneStatus = window.$controlPlaneDrainBtn = window.$controlPlaneReconcileBtn = null;
    window.controlPlaneDraining = false; window.controlPlaneDrainReason = ''; window.controlPlaneDrainQueued = 0;
    (0, eval)(renderer);
    window.setWorkerStatus = (reason, enabled = true) => renderControlPlaneStatus({ ok: true, worker: { capabilities: ['engine-execution-v1'] }, queued: 3, drain: { enabled, reason } });
  }, renderer);
  for (const reason of ['dashboard-restart:test', 'worker-restart:test', 'worker-compat-selfheal:test']) {
    await page.evaluate(reason => window.setWorkerStatus(reason), reason);
    assert.equal(await page.$eval('#drainPausedBanner', n => n.checkVisibility()), true);
    assert.match(await page.$eval('#drainPausedBannerText', n => n.textContent), /CCC is restarting\. Please wait.*messages will send automatically.*No action is needed.*3 message/);
    assert.equal(await page.$eval('#drainPausedResumeBtn', n => n.checkVisibility()), false);
  }
  await page.screenshot({path: path.join(scratch, width + '-automatic.png')});
  await page.evaluate(() => window.setWorkerStatus('operator paused dispatch'));
  assert.equal(await page.$eval('#drainPausedResumeBtn', n => n.checkVisibility()), true);
  assert.doesNotMatch(await page.$eval('#drainPausedBannerText', n => n.textContent), /automatically/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({path: path.join(scratch, width + '-manual.png')});
  await page.evaluate(() => window.setWorkerStatus('worker online', false));
  assert.equal(await page.$eval('#drainPausedBanner', n => n.checkVisibility()), false);
  await page.evaluate(() => { window.setWorkerStatus('worker-restart:test'); renderControlPlaneStatus(null); });
  assert.equal(await page.$eval('#drainPausedBanner', n => n.checkVisibility()), false);
});
