const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

async function verify() {
  const output = process.env.COMPARE_OUT_DIR;
  if (!output) throw new Error('Set COMPARE_OUT_DIR to an existing screenshot directory.');
  assert(fs.statSync(output).isDirectory());
  const svgUrl = new URL('/docs/images/how-it-compares.svg', process.env.COMPARE_BASE_URL || 'http://127.0.0.1:9210');
  assert(['127.0.0.1', 'localhost', '[::1]'].includes(svgUrl.hostname), 'Use a local verification server.');
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'], timeout: 20000 });
  const results = [];
  let deadline;
  try {
    await Promise.race([
      new Promise((_, reject) => { deadline = setTimeout(() => reject(new Error('Comparison verification exceeded 60 seconds.')), 60000); }),
      (async () => {
        const page = await browser.newPage();
        page.setDefaultTimeout(10000);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        for (const scheme of ['light', 'dark']) {
          await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: scheme }]);
          await page.setViewport({ width: 1200, height: 860 });
          const response = await page.goto(svgUrl.href, { waitUntil: 'load', timeout: 10000 });
          assert.equal(response.status(), 200);
          assert.match(response.headers()['content-type'], /image\/svg\+xml/);
          const geometry = await page.evaluate(() => {
            const svg = document.querySelector('svg');
            const view = svg.viewBox.baseVal;
            const rect = document.querySelector('svg > rect');
            const colors = {
              background: getComputedStyle(rect).fill,
              panel: getComputedStyle(document.querySelector('.panel')).fill,
              main: getComputedStyle(document.querySelector('.heading')).fill,
              secondary: getComputedStyle(document.querySelector('.body')).fill,
            };
            const outside = (box, area, inset = 0) => box.x < area.x + inset - 0.5 || box.y < area.y + inset - 0.5 || box.x + box.width > area.x + area.width - inset + 0.5 || box.y + box.height > area.y + area.height - inset + 0.5;
            const overflow = [];
            for (const text of svg.querySelectorAll('text')) {
              if (outside(text.getBBox(), view)) overflow.push({ text: text.textContent, area: 'viewBox' });
            }
            for (const id of ['ccc-card', 'routers-card', 'orchestrators-card']) {
              const card = document.getElementById(id);
              const area = card.querySelector('rect').getBBox();
              for (const text of card.querySelectorAll('text')) {
                if (outside(text.getBBox(), area, 16)) overflow.push({ text: text.textContent, area: id });
              }
            }
            return { colors, overflow, accessibleName: svg.getAttribute('aria-labelledby') };
          });
          assert.deepEqual(geometry.overflow, [], `${scheme}: clipped text`);
          const standalone = path.join(output, `compare-${scheme}.png`);
          await page.screenshot({ path: standalone });
          results.push({ scheme, surface: 'standalone', status: response.status(), ...geometry, screenshot: standalone });
          await page.goto(svgUrl.origin, { waitUntil: 'load' });
          for (const width of [1200, 375]) {
            await page.setViewport({ width, height: width === 375 ? 420 : 920 });
            const escapedUrl = svgUrl.href.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
            await page.setContent(`<html lang="en" style="color-scheme:${scheme}"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;padding:12px;background:${scheme === 'dark' ? '#0b1018' : '#ffffff'};font-family:Arial,sans-serif}img{display:block;width:100%;height:auto}a{display:inline-block;margin-top:12px;color:${scheme === 'dark' ? '#9fc2ff' : '#2456a6'}}</style></head><body><img src="${escapedUrl}" alt="CCC, model routers, and session tools at different layers"><a href="${escapedUrl}">Open full-size diagram</a></body></html>`, { waitUntil: 'load' });
            const embedded = await page.evaluate(() => {
              const image = document.querySelector('img');
              return { loaded: image.complete && image.naturalWidth === 1200 && image.naturalHeight === 860, overflow: document.documentElement.scrollWidth > innerWidth, width: image.getBoundingClientRect().width };
            });
            assert.equal(embedded.loaded, true);
            assert.equal(embedded.overflow, false);
            const screenshot = path.join(output, `compare-${scheme}-embedded-${width}.png`);
            await page.screenshot({ path: screenshot, fullPage: true });
            results.push({ scheme, surface: 'embedded', viewportWidth: width, ...embedded, screenshot });
          }
        }
        assert.deepEqual(errors, []);
      })(),
    ]);
  } finally {
    clearTimeout(deadline);
    await browser.close();
  }
  fs.writeFileSync(path.join(output, 'verification.json'), JSON.stringify(results, null, 2) + '\n');
  console.log(JSON.stringify(results, null, 2));
}

verify().catch(error => { console.error(error); process.exitCode = 1; });
