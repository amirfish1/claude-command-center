#!/usr/bin/env node
'use strict';

const puppeteer = require('puppeteer');

async function verify() {
  const baseUrl = process.env.CCC_VERIFY_BASE_URL || 'http://127.0.0.1:8090';
  const browser = await puppeteer.launch({headless: true});
  try {
    const page = await browser.newPage();
    const browserErrors = [];
    page.on('pageerror', (error) => browserErrors.push(String(error)));
    await page.setViewport({width: 1440, height: 1000, deviceScaleFactor: 1});
    await page.goto(baseUrl + '/productivity.html', {waitUntil: 'networkidle2'});
    await page.waitForSelector('#projectTable');
    await page.waitForFunction(
      () => !document.body.classList.contains('is-loading'),
      {timeout: 180000}
    );
    const desktop = await page.evaluate(() => ({
      ranges: document.querySelectorAll('[data-weeks]').length,
      cards: document.querySelectorAll('#summaryCards .metric-card').length,
      projectRows: document.querySelectorAll('#projectTable tbody tr').length,
      dailyRows: document.querySelectorAll('#dailyTable tbody tr').length,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      status: document.getElementById('refreshStatus').textContent.trim(),
    }));
    if (desktop.ranges !== 4 || desktop.cards < 12 || desktop.dailyRows < 1 || desktop.overflow) {
      throw new Error('desktop contract failed: ' + JSON.stringify(desktop));
    }
    await page.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
    await new Promise((resolve) => setTimeout(resolve, 100));
    const mobileOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth
    );
    if (mobileOverflow) throw new Error('mobile page has horizontal viewport overflow');
    if (browserErrors.length) throw new Error('browser errors: ' + browserErrors.join(' | '));
    await page.screenshot({path: '/tmp/ccc-productivity-mobile.png', fullPage: true});
    process.stdout.write(JSON.stringify({ok: true, desktop}) + '\n');
  } finally {
    await browser.close();
  }
}

verify().catch((error) => {
  console.error(error);
  process.exit(1);
});
