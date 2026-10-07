const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const url = process.env.LEADERBOARD_URL || 'http://127.0.0.1:9208/leaderboard/';
const out = process.env.LEADERBOARD_OUT_DIR;
const dataFile = process.env.LEADERBOARD_DATA;
if (!out || !dataFile) {
  console.error('Set LEADERBOARD_OUT_DIR to a scratch directory and LEADERBOARD_DATA to an exported populated test snapshot.');
  process.exit(1);
}
const snapshot = JSON.parse(fs.readFileSync(dataFile, 'utf8'));
assert.ok(snapshot.models.length > 0, 'Verification needs a populated test snapshot, not published seed data.');
fs.mkdirSync(out, { recursive: true });

(async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  const errors = [];
  let deadline;
  const screenshots = [];
  try {
    await Promise.race([
      new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error('Browser verification exceeded 75 seconds.')), 75000); }),
      (async () => {
        const page = await browser.newPage();
        page.setDefaultTimeout(10000);
        page.on('pageerror', error => errors.push(error.message));
        await page.setViewport({ width: 1280, height: 900 });
        await page.goto(url, { waitUntil: 'load', timeout: 15000 });
        await page.waitForFunction(() => !document.querySelector('#board-empty').hidden);
        assert.equal(await page.$eval('#board-loading', e => getComputedStyle(e).display), 'none');
        assert.equal(await page.$eval('#board', e => getComputedStyle(e).display), 'none');
        assert.equal(await page.$eval('#stat-models', e => e.textContent), '0');
        assert.equal(await page.$$eval('#task-list li', e => e.length), 5);
        await page.screenshot({ path: path.join(out, 'leaderboard-empty-desktop.png'), fullPage: true });
        screenshots.push('leaderboard-empty-desktop.png');

        let response = snapshot;
        let status = 200;
        await page.setRequestInterception(true);
        page.on('request', request => {
          if (new URL(request.url()).pathname.endsWith('/data.json')) {
            request.respond({ status, contentType: 'application/json', body: JSON.stringify(response) }).catch(() => {});
          } else { request.continue().catch(() => {}); }
        });
        await page.reload({ waitUntil: 'load' });
        await page.waitForFunction(() => !document.querySelector('#board').hidden);
        assert.equal(await page.$eval('#board-empty', e => getComputedStyle(e).display), 'none');
        const rows = await page.$$eval('#lb-rows tr', rows => rows.map(row => ({
          id: row.querySelector('.model-name').textContent,
          rank: row.querySelector('.rank').textContent,
          score: row.querySelector('.score-num').textContent,
          pass: row.querySelector('.pass').textContent,
          median: row.querySelectorAll('.date')[0].textContent,
          best: !!row.querySelector('.badge-best'),
          datetime: row.querySelector('time').dateTime,
        })));
        assert.equal(rows.length, snapshot.models.length);
        rows.forEach((row, index) => {
          const expected = snapshot.models[index];
          assert.equal(row.id, expected.id);
          assert.equal(row.rank, String(expected.rank));
          assert.equal(row.score, String(expected.score));
          assert.ok(row.pass.startsWith(`${Math.round(expected.pass_rate * 100)}%`));
          assert.ok(row.pass.includes(`${expected.passed} of ${expected.tasks} tasks`));
          assert.equal(row.best, expected.id === snapshot.best);
          assert.equal(row.datetime, expected.evaluated_at);
          if (expected.median_request_ms === 0) assert.equal(row.median, '0 ms');
          if (expected.median_request_ms === null) assert.equal(row.median, 'Not measured');
        });
        assert.equal(await page.$eval('#stat-models', e => e.textContent), String(snapshot.models.length));
        await page.screenshot({ path: path.join(out, 'leaderboard-populated-desktop.png'), fullPage: true });
        screenshots.push('leaderboard-populated-desktop.png');

        await page.browserContext().overridePermissions(new URL(url).origin, ['clipboard-read', 'clipboard-write']);
        await page.click('#copy-install');
        await page.waitForFunction(() => document.querySelector('#copy-status').textContent === 'Install command copied.');
        assert.equal(await page.evaluate(() => navigator.clipboard.readText()), await page.$eval('#install-command', e => e.textContent));
        await page.evaluate(() => {
          Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: () => Promise.reject(new Error('Denied')) } });
          document.execCommand = () => false;
        });
        await page.click('#copy-install');
        await page.waitForFunction(() => document.querySelector('#copy-status').textContent === 'Select and copy the install line.');

        await page.setViewport({ width: 390, height: 844 });
        await page.emulateMediaFeatures([{ name: 'prefers-reduced-motion', value: 'reduce' }]);
        await page.reload({ waitUntil: 'load' });
        await page.waitForFunction(() => !document.querySelector('#board').hidden);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'Mobile page must not overflow; only the table scrolls.');
        assert.equal(await page.$eval('.board-scroll', e => e.scrollWidth > e.clientWidth), true);
        await page.focus('.board-scroll');
        await page.keyboard.press('ArrowRight');
        await page.waitForFunction(() => document.querySelector('.board-scroll').scrollLeft > 0);
        await page.$eval('.board-scroll', e => { e.scrollLeft = 0; });
        await page.screenshot({ path: path.join(out, 'leaderboard-populated-mobile.png'), fullPage: true });
        screenshots.push('leaderboard-populated-mobile.png');

        response = { ...snapshot, models: snapshot.models.map((row, index) => index ? row : { ...row, id: '<img src=x onerror=alert(1)>' }) };
        await page.reload({ waitUntil: 'load' });
        await page.waitForFunction(() => !document.querySelector('#board').hidden);
        assert.equal(await page.$$eval('#lb-rows img', images => images.length), 0);
        assert.equal(await page.$eval('.model-name', e => e.textContent), '<img src=x onerror=alert(1)>');

        response = {};
        await page.reload({ waitUntil: 'load' });
        await page.waitForFunction(() => !document.querySelector('#board-error').hidden);
        assert.equal(await page.$eval('#board-empty', e => getComputedStyle(e).display), 'none');
        assert.equal(await page.$$eval('#task-list li', e => e.length), 5);
        status = 404;
        await page.reload({ waitUntil: 'load' });
        await page.waitForFunction(() => !document.querySelector('#board-error').hidden);
        await page.screenshot({ path: path.join(out, 'leaderboard-unavailable-mobile.png'), fullPage: true });
        screenshots.push('leaderboard-unavailable-mobile.png');

        await page.setJavaScriptEnabled(false);
        await page.reload({ waitUntil: 'load' });
        assert.equal(await page.$eval('#board-loading', e => getComputedStyle(e).display), 'none');
        assert.ok(await page.$eval('noscript', e => e.textContent.includes('read the result file')));
        assert.equal(await page.$$eval('#task-list li', e => e.length), 5);
        assert.deepEqual(errors, []);
      })(),
    ]);
    const result = { status: 'VERIFIED', url, checks: ['real empty snapshot', 'saved metrics', 'zero and missing latency', 'clipboard success and denial', 'mobile overflow and keyboard scroll', 'reduced motion', 'safe text rendering', 'invalid JSON shape', 'missing data', 'JavaScript disabled'], screenshots, errors };
    fs.writeFileSync(path.join(out, 'leaderboard-verification.json'), JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result, null, 2));
  } finally {
    clearTimeout(deadline);
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
