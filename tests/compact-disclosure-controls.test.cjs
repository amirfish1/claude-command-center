const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const compact = app.slice(app.indexOf('  function _ccUnitsOf('),
  app.indexOf('  // CCC-454: transcript view segmented control'));
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');

test('Claude disclosure arrows expand their own group without folding the run', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    for (const narration of [false, true]) {
      await page.setContent(`<style>${css}</style><body class="conv-compact">
        <div class="conv-pane"><div class="conversations-view">
          <div class="event user_text">Check the results</div>
          ${narration ? '<div class="event assistant"><div class="assistant-text"><p>Checking.</p></div></div>' : ''}
          <div class="tool-call-group collapsed" id="first">
            <div class="tool-call-group-header"><span class="tcg-arrow">▶</span><span class="tcg-label">Read first result</span></div>
            <div class="tool-call-group-body"><div class="tool-call"><details><summary>Command result</summary>First result</details></div></div>
          </div>
          <div class="event assistant"><div class="thinking-block">Considering the result</div></div>
          <div class="tool-call-group collapsed" id="second">
            <div class="tool-call-group-header"><span class="tcg-arrow">▶</span><span class="tcg-label">Read second result</span></div>
            <div class="tool-call-group-body"><div class="tool-call">Second result</div></div>
          </div>
        </div></div></body>`);
      await page.evaluate(code => {
        (0, eval)(code + '\nwindow.markRuns = _ccMarkRuns;');
        document.querySelectorAll('.tool-call-group-header').forEach(header => {
          header.addEventListener('click', () => header.parentElement.classList.toggle('collapsed'));
        });
        markRuns(document.querySelector('.conversations-view'));
      }, compact);
      await page.click(narration ? '.cc-run-chip' : '#first .tool-call-group-header');
      await page.click('#first .tcg-arrow');
      assert.equal(await page.$eval('#first', el => el.classList.contains('cc-run-open')), true,
        'first disclosure must leave the surrounding run open');
      assert.equal(await page.$eval('#first', el => el.classList.contains('collapsed')), false,
        'first disclosure must expand its command group');
      await page.click('#first summary');
      assert.equal(await page.$eval('#first details', el => el.open), true);
      await page.click('#second .tcg-arrow');
      assert.equal(await page.$eval('#second', el => el.classList.contains('collapsed')), false);
      await page.click('#first .tcg-arrow');
      assert.equal(await page.$eval('#first', el => el.classList.contains('collapsed')), true);
      assert.equal(await page.$eval('#second', el => el.classList.contains('cc-run-open')), true);
      await page.evaluate(() => {
        for (let i = 0; i < 5; i++) markRuns(document.querySelector('.conversations-view'));
      });
      assert.equal(await page.$$eval('.cc-run-fold', els => els.length), 1);
      await page.click('#first .tcg-arrow');
      if (process.env.CCC_DISCLOSURE_SCREENSHOT_DIR) {
        await page.screenshot({ path: process.env.CCC_DISCLOSURE_SCREENSHOT_DIR + '/disclosures-' + narration + '.png' });
      }
      await page.focus('.cc-run-fold');
      await page.keyboard.press('Enter');
      assert.equal(await page.$eval('#first', el => el.classList.contains('cc-run-open')), false);
      assert.equal(await page.$eval('#second', el => getComputedStyle(el).display), 'none');
      await page.click(narration ? '.cc-run-chip' : '#first .tool-call-group-header');
      await page.focus('.cc-run-fold');
      await page.keyboard.press('Space');
      assert.equal(await page.$eval('#first', el => el.classList.contains('cc-run-open')), false);
      await page.evaluate(() => document.body.classList.remove('conv-compact'));
      assert.equal(await page.$eval('.cc-run-fold', el => getComputedStyle(el).display), 'none');
      await page.click('#first .tcg-arrow');
      assert.equal(await page.$eval('#first', el => el.classList.contains('collapsed')), true,
        'normal mode retains native group controls');
      await page.evaluate(() => {
        document.querySelector('#second').remove();
        document.querySelector('.thinking-block').remove();
        document.querySelector('.assistant-text')?.closest('.event').remove();
        markRuns(document.querySelector('.conversations-view'));
      });
      assert.equal(await page.$$eval('.cc-run-fold', els => els.length), 0,
        'a group that no longer forms a compact run loses its fold control');
    }
  } finally {
    await browser.close();
  }
});
