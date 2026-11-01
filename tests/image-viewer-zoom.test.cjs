const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const lightbox = app.slice(app.indexOf('  let _imgLightbox = null;'), app.indexOf('  // Inline-code paths in conversation messages'));
const image = 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="2400" height="1200"><rect width="2400" height="1200" fill="#fff"/><text x="50" y="100" font-size="48">Image detail at actual size</text><text x="1300" y="900" font-size="48">Lower right detail</text></svg>');

test('Image viewer zooms and pans on phones and desktops, and resets for the next image', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({width: 390, height: 844, hasTouch: true});
    await page.setRequestInterception(true);
    page.on('request', req => req.respond({status: 200, contentType: 'text/html', body:
      `<meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style>`}));
    await page.goto('http://ccc-image.test/');
    await page.addScriptTag({content: `function escapeAttr(s) { return s; }
      function isImeKey() { return false; }
      ${lightbox}`});
    const geometry = () => page.evaluate(() => {
      const stage = document.querySelector('.img-lightbox-stage');
      const img = stage.querySelector('img');
      return {width: img.getBoundingClientRect().width, height: img.getBoundingClientRect().height,
        stageWidth: stage.clientWidth, stageHeight: stage.clientHeight,
        scrollWidth: stage.scrollWidth, scrollHeight: stage.scrollHeight,
        scale: Number(img.dataset.scale)};
    });
    for (const width of [390, 1440]) {
      await page.setViewport({width, height: 844, hasTouch: true});
      await page.evaluate(src => openMediaLightbox(src, {title: 'wide-image.png'}), image);
      await page.waitForFunction(() => document.querySelector('.img-lightbox-stage img').dataset.scale);
      let fit = await geometry();
      assert.ok(fit.width <= fit.stageWidth && fit.height <= fit.stageHeight, JSON.stringify(fit));
      assert.equal(await page.$eval('[data-image-zoom="out"]', el => el.disabled), true);
      await page.click('[data-image-zoom="in"]');
      assert.ok((await geometry()).width > fit.width, 'Zoom in enlarges');
      await page.click('[data-image-zoom="out"]');
      assert.ok(Math.abs((await geometry()).width - fit.width) < 1, 'Zoom out restores fit');
      await page.click('[data-image-zoom="actual"]');
      const actual = await geometry();
      assert.equal(actual.width, 2400);
      assert.equal(actual.height, 1200);
      assert.ok(actual.scrollWidth > actual.stageWidth && actual.scrollHeight > actual.stageHeight);
      await page.$eval('.img-lightbox-stage', el => {el.scrollLeft = 500; el.scrollTop = 300;});
      assert.ok(await page.$eval('.img-lightbox-stage', el => el.scrollLeft > 0 && el.scrollTop > 0), 'both axes pan');
      await page.click('.img-lightbox-stage img');
      assert.equal(await page.$eval('.img-lightbox', el => el.classList.contains('open')), true, 'image interaction keeps viewer open');
      if (process.env.IMAGE_ZOOM_SCREENSHOT_PREFIX) await page.screenshot({path: `${process.env.IMAGE_ZOOM_SCREENSHOT_PREFIX}-${width}.png`});
      await page.click('[data-image-zoom="fit"]');
      assert.ok(Math.abs((await geometry()).width - fit.width) < 1);
      await page.click('[data-image-zoom="in"]');
      await page.evaluate(src => openMediaLightbox(src, {title: 'next-image.png'}), image);
      await page.waitForFunction(() => document.querySelector('.img-lightbox-stage img').dataset.scale);
      assert.ok(Math.abs((await geometry()).width - fit.width) < 1, 'next image starts fitted');
    }
    await page.setViewport({width: 390, height: 700, hasTouch: true});
    const resized = await geometry();
    assert.ok(resized.width <= 390 && resized.height <= resized.stageHeight, 'fit follows resize');
    for (const kind of ['video', 'frame']) {
      await page.evaluate(kind => openMediaLightbox('about:blank', {kind}), kind);
      assert.equal(await page.$eval('.img-lightbox-zoom', el => el.hidden), true, `${kind} has no image controls`);
    }
    await page.click('[data-lightbox-close]');
    assert.equal(await page.$eval('.img-lightbox', el => el.classList.contains('open')), false);
  } finally { await browser.close(); }
});
