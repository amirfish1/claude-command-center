const assert = require('node:assert/strict');
const fs = require('node:fs');
const { test, before, after } = require('node:test');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const app = require('./legacy-dashboard-source.cjs');
function source(name) {
  const start = app.indexOf('  function ' + name + '(');
  assert(start >= 0);
  return app.slice(start, app.indexOf('\n  }\n', start) + 4);
}
const helpers = ['_ultraFreshPaintBatch', 'rowActionSelector', 'syncRowQuickActions', 'syncRowPopouts'].map(source).join('\n');
let browser;
before(async () => { browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] }); });
after(async () => { await browser?.close(); });

async function pageForBatch() {
  const page = await browser.newPage();
  await page.setContent('<div id="convList">' + ['one', 'two', 'other'].map(sid =>
    `<div class="conv-item" data-session-id="${sid}"><div class="conv-outcome"><span class="conv-quick-action-slot"><button>Old reply</button></span><button data-role="popout-row-session">Open</button></div></div>`).join('') + '</div>');
  await page.addScriptTag({ content: `
    window.events = [];
    const _rowQuickActionSending = new Set(), _rowQuickActionFeedback = new Map();
    let _rowInvestigationChecksInFlight = 0;
    const _ultraFreshRow = () => null, currentRowQuickActionSource = () => null;
    const loadRowDecisionReply = () => {};
    const _ultraFreshPaint = (sid, options) => {
      events.push('paint:' + sid);
      document.querySelector('[data-session-id="' + sid + '"]').dataset.state = 'working';
      if (!options?.deferActions) syncRowQuickActions(sid);
    };
    document.querySelectorAll('.conv-item').forEach(row => {
      const measure = row.getBoundingClientRect.bind(row);
      row.getBoundingClientRect = () => { events.push('measure:' + row.dataset.sessionId); return measure(); };
    });
    ${helpers}
    window.paintBatch = _ultraFreshPaintBatch;
  ` });
  return page;
}

test('live updates remove stale reply controls after all row writes and one geometry pass', async () => {
  const page = await pageForBatch();
  try {
    await page.evaluate(() => paintBatch(['one', 'two']));
    assert.deepEqual(await page.evaluate(() => events), ['paint:one', 'paint:two', 'measure:one', 'measure:two']);
    for (const sid of ['one', 'two']) {
      assert.equal(await page.$eval(`[data-session-id="${sid}"]`, row => row.dataset.state), 'working');
      assert.equal(await page.$$eval(`[data-session-id="${sid}"] button`, rows => rows.length), 0);
    }
    assert.equal(await page.$$eval('[data-session-id="other"] button', rows => rows.length), 2, 'a live batch leaves unrelated controls alone');
  } finally { await page.close(); }
});

test('an empty visible batch never becomes an archive-wide action update', async () => {
  const page = await pageForBatch();
  try {
    await page.evaluate(() => paintBatch([]));
    assert.deepEqual(await page.evaluate(() => events), []);
    assert.equal(await page.$$eval('button', rows => rows.length), 6);
  } finally { await page.close(); }
});

test('a complete redraw still synchronizes actions on every mounted row', async () => {
  const page = await pageForBatch();
  try {
    await page.evaluate(() => paintBatch(new Set(['one', 'two']).keys(), true));
    assert.deepEqual(await page.evaluate(() => events), ['paint:one', 'paint:two', 'measure:one', 'measure:two', 'measure:other']);
    assert.equal(await page.$$eval('button', rows => rows.length), 0);
  } finally { await page.close(); }
});
