// Verification pass for docs/index.html (the static product-story page).
// Loads the page headless, checks every <img>/<video> resolves, that no
// missing-asset onerror fallback fired, checks in-page anchor links, and
// writes desktop + mobile screenshots. Serve the repo root on CAP_BASE
// (default http://127.0.0.1:8877) first.
'use strict';
const fs = require('fs');
const path = require('path');

const BASE = process.env.CAP_BASE || 'http://127.0.0.1:8877';
const PAGE = BASE + '/docs/index.html';
const OUT_DIR = process.env.OUT_DIR || '/tmp/w6-verify';

function findChrome() {
  const c = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta',
  ];
  return c.find((p) => fs.existsSync(p));
}

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const puppeteer = require('puppeteer');
  const browser = await puppeteer.launch({ executablePath: findChrome(), headless: 'new', args: ['--no-sandbox'] });
  const page = await browser.newPage();

  const failedRequests = [];
  page.on('requestfailed', (r) => failedRequests.push({ url: r.url(), err: r.failure() && r.failure().errorText }));
  const badResponses = [];
  page.on('response', (res) => {
    const u = res.url();
    if (/product-story\/assets\/|\/images\//.test(u) && res.status() >= 400) {
      badResponses.push({ url: u, status: res.status() });
    }
  });

  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  await page.goto(PAGE, { waitUntil: 'networkidle2', timeout: 60000 });
  // Nudge lazy assets: scroll the whole page so lazy <img> load.
  await page.evaluate(async () => {
    await new Promise((resolve) => {
      let y = 0;
      const step = () => {
        window.scrollTo(0, y);
        y += 600;
        if (y < document.body.scrollHeight) setTimeout(step, 40);
        else { window.scrollTo(0, 0); setTimeout(resolve, 300); }
      };
      step();
    });
  });
  await new Promise((r) => setTimeout(r, 1200));

  const audit = await page.evaluate(() => {
    const imgs = [...document.querySelectorAll('img')].map((im) => ({
      src: im.currentSrc || im.src,
      alt: im.alt || '',
      naturalWidth: im.naturalWidth,
      complete: im.complete,
      isFallback: /\/images\/demo\.png(\?|$)/.test(im.currentSrc || im.src),
    }));
    const sources = [...document.querySelectorAll('video source')].map((s) => s.src);
    const posters = [...document.querySelectorAll('video[poster]')].map((v) => v.poster);
    // in-page anchor links
    const anchors = [...document.querySelectorAll('a[href^="#"]')].map((a) => a.getAttribute('href'));
    const brokenAnchors = anchors.filter((h) => h && h.length > 1 && !document.querySelector(h));
    return { imgs, sources, posters, anchors, brokenAnchors };
  });

  // HEAD-check every video source + poster URL.
  async function headOk(url) {
    try {
      const res = await page.evaluate(async (u) => {
        const r = await fetch(u, { method: 'GET' });
        return { status: r.status, len: r.headers.get('content-length') };
      }, url);
      return res;
    } catch (e) { return { status: -1, err: String(e) }; }
  }
  const videoChecks = [];
  for (const s of [...new Set([...audit.sources, ...audit.posters])]) {
    videoChecks.push({ url: s, ...(await headOk(s)) });
  }

  // Screenshots
  await page.screenshot({ path: path.join(OUT_DIR, 'page-desktop.png'), fullPage: true });
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  await page.reload({ waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise((r) => setTimeout(r, 800));
  await page.screenshot({ path: path.join(OUT_DIR, 'page-mobile.png'), fullPage: true });

  await browser.close();

  const fallbackImgs = audit.imgs.filter((i) => i.isFallback);
  const brokenImgs = audit.imgs.filter((i) => !i.isFallback && (i.naturalWidth === 0 || !i.complete));
  const brokenVideos = videoChecks.filter((v) => v.status < 200 || v.status >= 400);

  const report = {
    page: PAGE,
    imgCount: audit.imgs.length,
    videoSourceCount: audit.sources.length,
    posterCount: audit.posters.length,
    fallbackImgs,
    brokenImgs,
    brokenVideos,
    brokenAnchors: audit.brokenAnchors,
    failedRequests,
    badResponses,
    videoChecks,
  };
  console.log(JSON.stringify(report, null, 2));
  const pass = fallbackImgs.length === 0 && brokenImgs.length === 0 && brokenVideos.length === 0 && audit.brokenAnchors.length === 0 && badResponses.length === 0;
  console.log('\nVERIFY_RESULT:', pass ? 'PASS' : 'FAIL');
  console.log('Screenshots:', path.join(OUT_DIR, 'page-desktop.png'), path.join(OUT_DIR, 'page-mobile.png'));
  process.exit(pass ? 0 : 1);
})().catch((e) => { console.error('VERIFY ERROR', e); process.exit(2); });
