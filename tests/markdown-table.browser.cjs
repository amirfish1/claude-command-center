const { test } = require('node:test');
const assert = require('node:assert/strict');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const table = [
  '| Location | Before | After | Reclaimed | Status |',
  '| --- | --- | --- | --- | --- |',
  ...Array(18).fill('| `/example/storage/long-directory-name` | 42 GB | 1 GB | 41 GB | Verified |'),
].join('\n');

async function render(page, viewport, markdown) {
  await page.setViewport(viewport);
  await page.setContent('<meta name="viewport" content="width=device-width">'
    + '<div class="conv-pane" data-conv-bg="stitch"><div class="conversations-view" style="height: 360px">'
    + '<div class="event assistant"><div class="assistant-text"></div></div></div></div>');
  await page.addStyleTag({ path: require.resolve('../static/app.css') });
  await page.addScriptTag({ path: require.resolve('../static/conversation-text.js') });
  await page.$eval('.assistant-text', (message, text) => {
    message.innerHTML = window.CCCConversationText.createRenderer({}).renderMarkdown(text);
  }, markdown);
}

async function swipe(page, x, y, dx, dy) {
  await page.touchscreen.touchStart(x, y);
  for (let step = 1; step <= 8; step++) {
    await page.touchscreen.touchMove(x + dx * step / 8, y + dy * step / 8);
  }
  await page.touchscreen.touchEnd();
}

test('wide transcript tables reveal the last column on a phone swipe', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await render(page, { width: 375, height: 760, isMobile: true, hasTouch: true }, table);
    const box = await page.$eval('.md-table-wrap', el => {
      const { left, top, width, right } = el.getBoundingClientRect();
      return { left, top, width, right, overflow: el.scrollWidth - el.clientWidth };
    });
    assert.ok(box.overflow > 0);
    assert.ok(box.left >= 0 && box.right <= 375);

    for (let i = 0; i < 2; i++) {
      await swipe(page, box.right - 15, box.top + 15, -(box.width - 30), 0);
    }
    await page.waitForFunction(() => {
      const wrap = document.querySelector('.md-table-wrap');
      const last = wrap.querySelector('th:last-child');
      return wrap.scrollLeft > 0 && last.textContent === 'Status'
        && last.getBoundingClientRect().right <= wrap.getBoundingClientRect().right + 1;
    });
    assert.equal(await page.$eval('.conversations-view', el => el.scrollLeft), 0);

    await swipe(page, box.left + 40, box.top + 150, 0, -110);
    await page.waitForFunction(() => document.querySelector('.conversations-view').scrollTop > 0);
  } finally {
    await browser.close();
  }
});

test('tables keep their layout when they fit and support keyboard scrolling when constrained', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await render(page, { width: 1000, height: 760 }, '## Context Usage\n' + table);
    assert.equal(await page.$eval('.ctx-usage-details', el => el.open), false);
    await page.click('.ctx-usage-details > summary');
    const layout = await page.$eval('.md-table-wrap', wrap => ({
      width: wrap.clientWidth,
      scrollWidth: wrap.scrollWidth,
      tableWidth: wrap.querySelector('table').offsetWidth,
    }));
    assert.equal(layout.scrollWidth, layout.width);
    assert.ok(layout.tableWidth < layout.width);

    await page.$eval('.conv-pane', el => { el.style.maxWidth = '310px'; });
    assert.ok(await page.$eval('.md-table-wrap', wrap => wrap.scrollWidth > wrap.clientWidth));
    await page.focus('.md-table-wrap');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('.md-table-wrap').scrollLeft > 0);
  } finally {
    await browser.close();
  }
});
