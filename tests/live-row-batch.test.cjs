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
    const _rowInvestigationChecks = new Map();
    let _rowInvestigationChecksInFlight = 0;
    window.candidates = {};
    const _ultraFreshRow = sid => candidates[sid] || null, _ultraFreshCurrent = row => row;
    const rowQuickActionSource = row => row, currentRowQuickActionSource = () => null;
    const loadRowDecisionReply = row => events.push('reply:' + row.dataset.sessionId);
    const checkRowInvestigation = candidate => {
      events.push('investigate:' + candidate.sid);
      _rowInvestigationChecks.set(candidate.sid, {key:candidate.key, pending:true});
      return new Promise(() => {});
    };
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

test('live updates remove stale reply controls without unnecessary geometry reads', async () => {
  const page = await pageForBatch();
  try {
    await page.evaluate(() => paintBatch(['one', 'two']));
    assert.deepEqual(await page.evaluate(() => events), ['paint:one', 'paint:two']);
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
    assert.deepEqual(await page.evaluate(() => events), ['paint:one', 'paint:two']);
    assert.equal(await page.$$eval('button', rows => rows.length), 0);
  } finally { await page.close(); }
});

test('phone reply and investigation visibility reads stay batched and skip offscreen requests', async () => {
  const page = await pageForBatch();
  try {
    await page.setViewport({width:390, height:844});
    await page.evaluate(() => {
      document.querySelector('[data-session-id="one"]').insertAdjacentHTML('beforeend', '<div class="wa-decision-reply"></div>');
      candidates.two = {sid:'two', key:'current', investigation:'Inspect the current transcript'};
      candidates.other = {sid:'other', key:'offscreen', investigation:'Inspect another transcript'};
      document.querySelector('[data-session-id="other"]').style.cssText = 'position:absolute;top:10000px';
      paintBatch(['one', 'two', 'other']);
    });
    assert.deepEqual(await page.evaluate(() => events), [
      'paint:one', 'paint:two', 'paint:other', 'measure:one', 'measure:two', 'measure:other',
      'reply:one', 'reply:two', 'investigate:two',
    ]);
    assert.equal(await page.$$eval('.conv-quick-action-slot', rows => rows.length), 0, 'offscreen stale replies still disappear');
    assert.equal(await page.$$eval('[data-role="popout-row-session"]', rows => rows.length), 2, 'available session popouts remain');
  } finally { await page.close(); }
});
