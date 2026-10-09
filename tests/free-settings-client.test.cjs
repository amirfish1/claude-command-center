'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const ROOT = path.resolve(__dirname, '..');
const FS_JS = path.join(ROOT, 'static', 'free-settings.js');

let browser;
let server;
let url;

function fixtureHtml(open) {
  return `<!doctype html>
<html><head><meta charset="utf-8"><title>free-settings fixture</title></head>
<body>
<button type="button" id="settingsBtn" aria-expanded="${open}">Settings</button>
<div id="settingsModal" class="settings-modal-overlay${open ? ' open' : ''}"${open ? '' : ' hidden'}>
  <div class="settings-modal">
    <div id="settingsRail" role="tablist">
      <button type="button" class="settings-rail-item" id="settingsRailTab-engines"
        data-section-target="engines" role="tab">Engines</button>
    </div>
    <div id="settingsPane">
      <section class="settings-section" id="settingsSection-engines" data-section-id="engines"></section>
    </div>
  </div>
</div>
<script>
  window.__btnClicks = 0;
  document.getElementById('settingsBtn').addEventListener('click', () => {
    window.__btnClicks += 1;
    const m = document.getElementById('settingsModal');
    if (m.classList.contains('open')) { m.classList.remove('open'); m.hidden = true; }
    else { m.classList.add('open'); m.hidden = false; }
  });
  document.getElementById('settingsRail').addEventListener('click', (e) => {
    const t = e.target.closest('[data-section-target]');
    if (!t) return;
    document.querySelectorAll('.settings-section').forEach((s) => s.classList.remove('is-active-section'));
    const sec = document.getElementById('settingsSection-' + t.dataset.sectionTarget);
    if (sec) sec.classList.add('is-active-section');
  });
</script>
<script src="/static/free-settings.js"></script>
</body></html>`;
}

test.before(async () => {
  server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://x');
    if (u.pathname === '/static/free-settings.js') {
      res.writeHead(200, { 'Content-Type': 'text/javascript' });
      return res.end(fs.readFileSync(FS_JS));
    }
    if (u.pathname === '/static/free-settings.css') {
      res.writeHead(200, { 'Content-Type': 'text/css' });
      return res.end('');
    }
    if (u.pathname.startsWith('/api/')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end('{}');
    }
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(fixtureHtml(u.searchParams.get('modal') === 'open'));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  url = 'http://127.0.0.1:' + server.address().port;
  browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
});

test.after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

async function openFixture(query) {
  const page = await browser.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', (err) => errors.push(String(err)));
  await page.goto(url + '/?ccc_settings=free' + (query || ''), { waitUntil: 'load' });
  page.__errors = errors;
  return page;
}

test('deep link keeps an already-open Settings modal visible', async () => {
  const page = await openFixture('&modal=open');
  try {
    await page.waitForSelector('#settingsSection-free.is-active-section .fs-hero-title', { visible: true });
    assert.equal(await page.evaluate(() => window.__btnClicks), 0);
    assert.equal(await page.evaluate(() => {
      const m = document.getElementById('settingsModal');
      return m.classList.contains('open') && !m.hidden;
    }), true);
    assert.deepEqual(page.__errors, []);
  } finally { await page.close(); }
});

test('deep link opens the modal exactly once when it starts closed', async () => {
  const page = await openFixture();
  try {
    await page.waitForSelector('#settingsSection-free.is-active-section .fs-hero-title', { visible: true });
    assert.equal(await page.evaluate(() => window.__btnClicks), 1);
    assert.equal(await page.evaluate(() => {
      const m = document.getElementById('settingsModal');
      return m.classList.contains('open') && !m.hidden;
    }), true);
    assert.deepEqual(page.__errors, []);
  } finally { await page.close(); }
});
