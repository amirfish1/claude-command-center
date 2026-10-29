// Run with: node tests/session-list-drag-select.cjs
// Exercise the real UI against seeded demo data; never touch live sessions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { launchBrowser, forceDemoFixtures, seedLocalStorage, gotoAndSettle } = require('../scripts/story-capture/lib.js');
const { LIST } = require('../scripts/story-capture/flows/_seeds.js');
const root = path.resolve(__dirname, '..');

(async () => {
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404).end();
      return;
    }
    res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml' })[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await launchBrowser();
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });
    await forceDemoFixtures(page, '/docs/demo/api');
    await seedLocalStorage(page, LIST);
    await gotoAndSettle(page, `http://127.0.0.1:${server.address().port}/static/index.html?demo=1`, { settleMs: 1000 });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const measureRows = () => page.$$eval('#convList .conv-item', elements => elements.slice(0, 3).map(row => {
      const box = row.querySelector('.conv-session-icon').getBoundingClientRect();
      return { id: row.dataset.id, x: box.x + box.width / 2, y: box.y + box.height / 2 };
    }));
    const rows = await measureRows();
    assert.equal(rows.length, 3);
    const selected = () => page.$$eval('#convList .list-selected', elements => elements.map(row => row.dataset.id));
    async function drag(from, to, modifier = 'Shift') {
      if (modifier) await page.keyboard.down(modifier);
      await page.mouse.move(from.x, from.y);
      await page.mouse.down();
      await page.mouse.move(to.x, to.y, { steps: 12 });
      await page.mouse.up();
      if (modifier) await page.keyboard.up(modifier);
    }

    // A plain row-body click still opens the conversation.
    await page.mouse.click(rows[0].x, rows[0].y);
    await page.waitForFunction(id => document.querySelector('#convList .conv-item.active')?.dataset.id === id, {}, rows[0].id);
    await drag(rows[0], rows[2]);
    assert.deepEqual(await selected(), rows.map(row => row.id));
    assert.equal(await page.$eval('#coordCount', element => element.textContent), '3 sessions selected');
    await page.click('#coordClearBtn');
    assert.deepEqual(await selected(), []);

    // Additive dragging keeps the original selection, but contracts its range.
    await page.keyboard.down('Control');
    await page.mouse.click(rows[2].x, rows[2].y);
    await page.mouse.move(rows[0].x, rows[0].y);
    await page.mouse.down();
    await page.mouse.move(rows[1].x, rows[1].y, { steps: 8 });
    await page.mouse.move(rows[0].x, rows[0].y, { steps: 8 });
    await page.mouse.up();
    await page.keyboard.up('Control');
    assert.deepEqual((await selected()).sort(), [rows[0].id, rows[2].id].sort());
    await page.click('#coordClearBtn');
    await drag(rows[0], rows[2]);

    // Cancel sends nothing. A partial failure keeps the failed row selected;
    // the next click retries only that row, using the existing per-session API.
    await page.evaluate(failId => {
      const originalFetch = window.fetch;
      window.trashRequests = [];
      window.trashFailId = failId;
      window.confirm = () => false;
      window.fetch = async (url, options) => {
        if (options?.method === 'POST' && /\/api\/conversations\/[^/]+\/trash$/.test(String(url))) {
          const payload = JSON.parse(options.body);
          window.trashRequests.push(payload);
          const ok = payload.session_id !== window.trashFailId;
          return new Response(JSON.stringify({ ok, trashed: ok, archived: ok, cascaded: [], error: ok ? undefined : 'test refusal' }), { headers: { 'Content-Type': 'application/json' } });
        }
        return originalFetch(url, options);
      };
    }, rows[1].id);
    await page.click('#coordTrashBtn');
    assert.equal(await page.evaluate(() => window.trashRequests.length), 0);
    await page.evaluate(() => { window.confirm = () => true; });
    await page.click('#coordTrashBtn');
    await page.waitForFunction(() => window.trashRequests.length === 3 && !document.getElementById('coordTrashBtn').disabled);
    assert.deepEqual(await selected(), [rows[1].id]);
    assert.equal(await page.evaluate(() => window.trashRequests.every(request => request.trashed === true)), true);
    await page.evaluate(() => { window.trashFailId = null; });
    await page.click('#coordTrashBtn');
    await page.waitForFunction(() => window.trashRequests.length === 4 && !document.getElementById('coordTrashBtn').disabled);
    assert.deepEqual(await selected(), []);
    assert.equal(await page.evaluate(() => window.trashRequests[3].session_id), rows[1].id);
    // CCC-212: a plain drag is reserved for moving a row (e.g. into a split
    // pane); it never range-selects. Last, since the native drag it starts
    // leaves Puppeteer's mouse in a drag session.
    await drag(rows[0], rows[2], null);
    assert.deepEqual(await selected(), []);
    assert.equal(await page.$eval('#convList', el => el.classList.contains('is-drag-selecting')), false);
    assert.deepEqual(errors, []);
    console.log('PASS: row clicks, plain drag not selecting, Shift range/additive selection, clear, cancel, partial trash failure, and retry.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
