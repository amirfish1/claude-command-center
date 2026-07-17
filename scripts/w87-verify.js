// W87 verification: zoom ladder (3h -> 1h -> sessions), main-view strip,
// daily report page. Headless screenshots at 1440 and 390 wide.
// Usage: node scripts/w87-verify.js [outDir]
const puppeteer = require('puppeteer');
const path = require('path');
const fs = require('fs');

const BASE = process.env.W87_BASE || 'http://127.0.0.1:8199';
const OUT = process.argv[2] || '/tmp/w87-shots';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, name + '.png') });
  console.log('shot:', name);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({ headless: 'new' });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  // ── Throughput zoom ladder @1440 ──────────────────────────────────────────
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(BASE + '/throughput.html', { waitUntil: 'networkidle2', timeout: 120000 });
  await page.waitForSelector('#throughput-chart rect[data-tz="1"]', { timeout: 120000 });
  await sleep(1200);
  await shot(page, 'zoom-L0-3h-1440');

  // Click a mid bar -> L1 (1h view)
  const nBars = await page.$$eval('#throughput-chart rect[data-tz="1"]', (rs) => rs.length);
  if (!nBars) throw new Error('no zoomable 3h bars');
  console.log('zoomable 3h bars:', nBars);
  // dispatchEvent instead of coordinate click: overlay circles/paths can sit
  // above a bar's center and swallow a real click at that exact point.
  await page.evaluate(() => {
    const rs = document.querySelectorAll('#throughput-chart rect[data-tz="1"]');
    rs[Math.max(0, rs.length - 3)].dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await page.waitForSelector('#tput-zoom-overlay .tz-bar', { timeout: 15000 });
  await sleep(300);
  await shot(page, 'zoom-L1-1h-1440');

  // Click the tallest hour bar -> L2 (session drill)
  await page.evaluate(() => {
    const bars = Array.from(document.querySelectorAll('#tput-zoom-overlay .tz-bar'));
    bars.sort((a, b) => Number(b.getAttribute('height')) - Number(a.getAttribute('height')));
    if (bars[0]) bars[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await page.waitForSelector('#tput-zoom-overlay .tz-table tr.tz-row', { timeout: 60000 });
  await sleep(300);
  const drillRows = await page.$$eval('#tput-zoom-overlay tr.tz-row', (rs) => rs.length);
  console.log('drill rows:', drillRows);
  await shot(page, 'zoom-L2-sessions-1440');

  // Session row click-through target
  const sid = await page.$eval('#tput-zoom-overlay tr.tz-row', (tr) => tr.getAttribute('data-sid'));
  if (!sid) throw new Error('drill row has no session id');
  console.log('first drill session:', sid.slice(0, 8));

  // Escape pops back to L1 then L0
  await page.keyboard.press('Escape');
  await sleep(250);
  const l1Back = await page.$('#tput-zoom-overlay .tz-bar');
  if (!l1Back) throw new Error('Escape did not return to L1');
  await page.keyboard.press('Escape');
  await sleep(250);
  const gone = await page.$('#tput-zoom-overlay');
  if (gone) throw new Error('Escape did not close overlay');
  console.log('escape ladder OK');

  // ── Daily report @1440 ───────────────────────────────────────────────────
  await page.goto(BASE + '/throughput-daily.html?date=yesterday', { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(800);
  await shot(page, 'daily-report-1440');

  // ── Main view strip @1440 ────────────────────────────────────────────────
  await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 120000 });
  await page.waitForSelector('#cccThroughputStrip', { timeout: 60000 });
  await page.waitForFunction(
    () => !/…/.test(document.querySelector('#cccThroughputStrip .ts-burn').textContent),
    { timeout: 120000 }
  );
  const stripText = await page.$eval('#cccThroughputStrip', (el) => el.innerText.replace(/\s+/g, ' '));
  console.log('strip:', stripText);
  await shot(page, 'strip-main-1440');

  // ── 390px versions ───────────────────────────────────────────────────────
  await page.setViewport({ width: 390, height: 844 });
  await page.goto(BASE + '/throughput-daily.html?date=yesterday', { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(800);
  await shot(page, 'daily-report-390');

  await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 120000 });
  await page.waitForSelector('#cccThroughputStrip', { timeout: 60000 });
  await sleep(1500);
  await shot(page, 'strip-main-390');

  await page.goto(BASE + '/throughput.html', { waitUntil: 'networkidle2', timeout: 120000 });
  await page.waitForSelector('#throughput-chart rect[data-tz="1"]', { timeout: 120000 });
  await sleep(800);
  await shot(page, 'zoom-L0-3h-390');
  await page.evaluate(() => {
    const rs = document.querySelectorAll('#throughput-chart rect[data-tz="1"]');
    rs[Math.max(0, rs.length - 3)].dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await page.waitForSelector('#tput-zoom-overlay .tz-bar', { timeout: 15000 });
  await sleep(300);
  await shot(page, 'zoom-L1-1h-390');
  await page.evaluate(() => {
    const bars = Array.from(document.querySelectorAll('#tput-zoom-overlay .tz-bar'));
    bars.sort((a, b) => Number(b.getAttribute('height')) - Number(a.getAttribute('height')));
    if (bars[0]) bars[0].dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
  await page.waitForSelector('#tput-zoom-overlay .tz-table tr.tz-row', { timeout: 60000 });
  await sleep(300);
  await shot(page, 'zoom-L2-sessions-390');

  await browser.close();
  const fatal = errors.filter((e) => !/favicon|net::ERR_ABORTED/.test(e));
  console.log(fatal.length ? 'ERRORS:\n' + fatal.join('\n') : 'no console/page errors');
  process.exit(fatal.length ? 1 : 0);
})().catch((e) => { console.error('FAIL:', e.message); process.exit(2); });
