'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const puppeteer = require(path.resolve(__dirname, '../..', 'require-puppeteer.js'));

async function main() {
  const htmlPath = process.argv[2];
  const framesDirectory = process.argv[3];
  if (!htmlPath || !framesDirectory) {
    throw new Error('Usage: node film_capture.cjs <html-path> <frames-directory>');
  }

  const html = await fs.readFile(htmlPath, 'utf8');
  await fs.mkdir(framesDirectory, { recursive: true });

  let browser;
  try {
    browser = await puppeteer.launch({
      headless: true,
      pipe: true,
      args: ['--no-sandbox', '--disable-background-networking'],
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 720, deviceScaleFactor: 1 });
    await page.setRequestInterception(true);
    page.on('request', (request) => {
      request.abort().catch(() => {});
    });
    await page.setContent(html, { waitUntil: 'load' });

    await page.addStyleTag({ content: 'nav{display:none!important}' });
    const frameCount = await page.evaluate(() => document.querySelectorAll('section.scene').length);
    for (let index = 0; index < frameCount; index += 1) {
      await page.evaluate((activeIndex) => {
        document.querySelectorAll('section.scene').forEach((scene, sceneIndex) => {
          scene.classList.toggle('active', sceneIndex === activeIndex);
        });
      }, index);
      await page.screenshot({
        path: path.join(framesDirectory, `frame-${String(index).padStart(3, '0')}.png`),
        type: 'png',
      });
    }
    process.stdout.write(`${JSON.stringify({ frameCount })}\n`);
  } finally {
    if (browser) await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
