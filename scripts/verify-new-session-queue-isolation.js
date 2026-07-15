#!/usr/bin/env node

const puppeteer = require('../require-puppeteer.js');

const baseUrl = process.env.CCC_NEW_SESSION_QUEUE_URL || 'http://127.0.0.1:8090';
const timeout = Number(process.env.CCC_NEW_SESSION_QUEUE_TIMEOUT_MS) || 30000;
const marker = 'CCC_NEW_SESSION_FOREIGN_QUEUE_SENTINEL';

(async () => {
  const browser = await puppeteer.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();

  try {
    await page.evaluateOnNewDocument(() => {
      localStorage.setItem('ccc-session-view', 'list');
    });
    await page.goto(baseUrl, { waitUntil: 'load', timeout });
    await page.waitForSelector('#sidebarNewBtn', { timeout });
    await page.waitForSelector('#convInputBar', { timeout });

    await page.evaluate(markerText => {
      const inputBar = document.querySelector('#convInputBar');
      if (!inputBar) throw new Error('missing conversation input bar');
      const tray = document.createElement('div');
      tray.className = 'queued-steer-tray';
      tray.dataset.conversationId = 'foreign-session';
      tray.innerHTML = '<div class="event user_text send-queued"><div class="user-msg"></div></div>';
      tray.querySelector('.user-msg').textContent = markerText;
      inputBar.insertBefore(tray, inputBar.firstChild);
    }, marker);

    await page.evaluate(() => {
      const listView = document.querySelector('#kptListViewBtn');
      if (!listView) throw new Error('missing list-view control');
      listView.click();
      const newSession = document.querySelector('#sidebarNewBtn');
      if (!newSession) throw new Error('missing New session control');
      newSession.click();
    });
    await new Promise(resolve => setTimeout(resolve, 250));

    const result = await page.evaluate(markerText => ({
      trayCount: document.querySelectorAll('#convInputBar .queued-steer-tray').length,
      markerVisible: document.body.textContent.includes(markerText),
      newSessionVisible: Array.from(document.querySelectorAll('.ns-stage-title'))
        .some(title => title.textContent.trim() === 'New session'),
      sessionView: localStorage.getItem('ccc-session-view'),
    }), marker);

    if (!result.newSessionVisible || result.trayCount !== 0 || result.markerVisible) {
      throw new Error(
        `new-session composer leaked foreign queue state: ${JSON.stringify(result)}`,
      );
    }

    process.stdout.write('PASS new-session composer clears queued messages from the previous session\n');
  } finally {
    await browser.close();
  }
})().catch(error => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exit(1);
});
