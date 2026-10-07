'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
let browser;
let server;
let url;

test.before(async () => {
  server = http.createServer((req, res) => {
    if (req.url === '/static/notify.js' || req.url === '/static/popups.js') {
      res.writeHead(200, { 'Content-Type': 'text/javascript' });
      return res.end(fs.readFileSync(path.join(__dirname, '..', req.url)));
    }
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end('<!doctype html><script src="/static/popups.js"></script><script src="/static/notify.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  url = 'http://127.0.0.1:' + server.address().port;
  browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
});

test.after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

test('notification API supports callable and object callers without bypassing approvals', async () => {
  const page = await browser.newPage();
  try {
    await page.goto(url, { waitUntil: 'load' });
    assert.equal(await page.evaluate(() => typeof window.cccNotify), 'function');
    const held = await page.evaluate(() => ({
      callable: window.cccNotify({ title: 'Held task', kind: 'task' }),
      object: window.cccNotify.show({ title: 'Held task', kind: 'task' }),
    }));
    assert.deepEqual(held, { callable: false, object: false });
    assert.equal(await page.$('.ccc-toast'), null);
    const shown = await page.evaluate(() => {
      localStorage.setItem('ccc-popups-preview', 'notify-task');
      return window.cccNotify({ title: 'Task finished', body: 'A synthetic test.', kind: 'task' });
    });
    assert.equal(shown, true);
    await page.waitForSelector('.ccc-toast-kind-task');
    assert.equal(await page.$('[aria-label="Turn on notifications"]'), null);
    const legacy = await page.evaluate(() => {
      localStorage.setItem('ccc-popups-preview', 'notify-other');
      return window.cccNotify('Setup complete', 'Your team is ready.', 'success');
    });
    assert.equal(legacy, true);
    await page.waitForSelector('.ccc-toast-kind-success');
    assert.equal(await page.evaluate(() => typeof window.cccNotify._deliver), 'function');
    assert.equal(await page.evaluate(() => typeof window.cccNotify.setPref), 'function');
  } finally { await page.close(); }
});
