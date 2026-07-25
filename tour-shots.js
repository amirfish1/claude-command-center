// FIRST FLIGHT verification: drive the tour headlessly and screenshot every
// step, both paths, at 1440x900 and 390x844. Not shipped; verification rig.
//
// Usage: SHOTS_URL=http://127.0.0.1:8199 node tour-shots.js
// Server should be a fresh-install CCC (empty HOME) started separately.
const fs = require('fs');
const path = require('path');
const puppeteer = require('./require-puppeteer.js');

function findChromePath() {
  if (process.env.SNAPSHOT_CHROME) return process.env.SNAPSHOT_CHROME;
  const macs = [
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  for (const p of macs) {
    try { fs.accessSync(p, fs.constants.X_OK); return p; } catch (_) {}
  }
  return undefined;
}

const URL = process.env.SHOTS_URL || 'http://127.0.0.1:8199';
const OUT = path.join(__dirname, 'docs', 'first-flight-shots');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function cardTitle(page) {
  return page.evaluate(() => {
    const el = document.querySelector(
      '.fft-title, [class*="fft-"] h1, [class*="fft-"] h2, [class*="fft-"] h3'
    );
    return el ? el.textContent.trim() : null;
  });
}

async function tourActive(page) {
  return page.evaluate(() => !!document.querySelector('[class*="fft-"]'));
}

async function driveTour(page, pathChoice, width, height, tag, results) {
  await page.setViewport({ width, height, deviceScaleFactor: 1 });
  await page.goto(URL, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(1800);
  await page.evaluate(() => {
    try { localStorage.removeItem('ccc-tour-done'); } catch (_) {}
    document.querySelectorAll('.upd-overlay.open').forEach((el) => el.classList.remove('open'));
  });
  const loadErr = await page.evaluate(async () => {
    try {
      if (!window.cccTour) {
        await new Promise((res, rej) => {
          const s = document.createElement('script');
          s.src = '/static/tour.js';
          s.onload = res;
          s.onerror = () => rej(new Error('tour.js failed to load'));
          document.head.appendChild(s);
        });
      }
      window.cccTour.start({ force: true });
      return null;
    } catch (e) { return String(e); }
  });
  if (loadErr) throw new Error(`${tag}: ${loadErr}`);
  await sleep(800);

  const shot = async (name) => {
    const title = await cardTitle(page);
    const file = `${tag}-${name}.png`;
    await page.screenshot({ path: path.join(OUT, file) });
    results.push({ tag, name, title, file });
    return title;
  };

  await shot('01-welcome');
  await page.keyboard.press('Enter'); await sleep(500);
  await shot('02-fork');
  await page.keyboard.press(pathChoice); await sleep(800);
  for (let i = 1; i <= 6; i++) {
    if (!(await tourActive(page))) break;
    await shot(`0${i + 2}-step${i}`);
    await page.keyboard.press('ArrowRight'); await sleep(650);
  }
  if (await tourActive(page)) {
    await shot('09-finale');
    await page.keyboard.press('Enter'); await sleep(500);
  }
  const still = await tourActive(page);
  const flag = await page.evaluate(() => {
    try { return localStorage.getItem('ccc-tour-done'); } catch (_) { return 'ERR'; }
  });
  results.push({ tag, name: 'END', title: `active=${still} flag=${flag}` });
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: findChromePath(),
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const results = [];
  const errors = [];
  const combos = [
    ['1', 1440, 900, 'newcomer-1440'],
    ['2', 1440, 900, 'multi-1440'],
    ['1', 390, 844, 'newcomer-390'],
    ['2', 390, 844, 'multi-390'],
  ];
  for (const [choice, w, h, tag] of combos) {
    const page = await browser.newPage();
    page.on('pageerror', (e) => errors.push(`${tag} pageerror: ${e.message}`));
    try {
      await driveTour(page, choice, w, h, tag, results);
    } catch (e) {
      errors.push(`${tag} FAILED: ${e.message}`);
    }
    await page.close();
  }
  await browser.close();
  for (const r of results) console.log(`${r.tag}  ${r.name}  ${r.title || '(no card)'}`);
  if (errors.length) {
    console.error('ERRORS:'); errors.forEach((e) => console.error('  ' + e));
    process.exit(1);
  }
  console.log('OK: ' + results.filter((r) => r.name !== 'END').length + ' screenshots');
})();
