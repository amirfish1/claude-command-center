// Vertical budget for the Workers list.
//
// Every feature added to a Workers row used to take a new line: the session
// count, the blocker category, the answer buttons. Rows grew to ~136px and a
// screen showed five tickets. This test renders the REAL row renderer with
// the real stylesheet and holds each row to a fixed height budget.
//
// The budget is the contract. If a feature genuinely needs more vertical
// space, ask the owner for budget first; do not raise these numbers to make
// a new line fit.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const {findChromePath} = require('../puppeteer-browser-config.js');

const app = fs.readFileSync('static/app.js', 'utf8');
const slice = (from, to) => {
  const start = app.indexOf(from), end = app.indexOf(to, start);
  assert.ok(start >= 0 && end > start, 'missing source slice: ' + from);
  return app.slice(start, end);
};

// Heights in CSS pixels at the default 380px sidebar.
const BUDGET = {
  identityOnly: 30,  // one line: ref, title, state, age, session count
  withContext: 50,   // plus one line: category, question, answer buttons
  withContextTouch: 60, // same two lines with 28px touch targets
  awaitingStrip: 32, // "Awaiting worker" label and its chips on one line
};

const BLOCK = {status:'awaiting_answer', needs_input:true, blocked_at:'2026-01-01T10:00:00Z'};
const ITEMS = [
  {ref:'EXAMPLE-1', title:'A long ticket title that has to truncate instead of wrapping onto a second line', status:'in_progress'},
  {ref:'EXAMPLE-2', title:'Owner approval with quick answers', ...BLOCK,
    block_question:'[Q1:restore] Approve the guarded restoration of only one item to active, preserving the audit trail and adding a note?'},
  {ref:'EXAMPLE-3', title:'Owner decision with a long explanation', ...BLOCK,
    block_question:'[Q2:steer] Should Steer be removed from already-delivered transcript messages, or should the existing grey buttons be enabled to resend those messages into a running turn? The tradeoff is clarity versus recovery.'},
  {ref:'EXAMPLE-4', title:'Several sessions and lanes', ...BLOCK,
    block_question:'[Q3:scope] Choose: keep narrow or expand scope.'},
  {ref:'EXAMPLE-LONG-REFERENCE-5', title:'Resolved work', status:'closed', closed_at:'2026-01-01T11:00:00Z'},
];
const AWAITING = Array.from({length:12}, (_, i) => ({ref:'READY-' + (i + 1), title:'Ready ticket ' + i, status:'open', priority:'p2'}));

async function renderList(page, touch) {
  await page.setViewport({width:380, height:900, isMobile:touch, hasTouch:touch});
  await page.setContent('<html data-theme="light"><head><meta name="viewport" content="width=device-width"></head><body><div class="sidebar"><div id="convList" class="workers-view">'
    + '<div class="conv-archived-section"><div class="conv-workers-activity"><div class="fq-recent-work" data-role="workers-recent-work">'
    + '<div class="workers-awaiting" data-role="workers-awaiting" hidden></div>'
    + '<div class="fq-recent-rows"></div></div></div></div></div></div></body></html>');
  await page.addStyleTag({content:fs.readFileSync('static/app.css', 'utf8')
    + '\nbody{display:block!important;height:auto!important;margin:0}.sidebar{width:380px;height:auto}'});
  await page.addScriptTag({content:fs.readFileSync('static/queue-attention.js', 'utf8')});
  await page.addScriptTag({content:`
    const ITEMS = ${JSON.stringify(ITEMS)};
    const WORKERS_RECENT_DETAILED = 0;
    const _uxqItemsCache = {items:${JSON.stringify(AWAITING)}};
    const _uxqHealthCache = {wt_workers:[]};
    const _workersTicketSessions = new Map([
      ['EXAMPLE-1', {count:1, lanes:0, cards:[{id:'solo', outcome:{kind:'working', text:'Running the focused tests.'}}], html:''}],
      ['EXAMPLE-4', {count:3, lanes:2, cards:[{id:'a'}, {id:'b'}, {id:'c'}], html:'<div>lanes</div>'}],
    ]);
    const _workersTicketExpanded = new Set();
    let _workersTicketRecentSignature = '';
    const _uxqItemRef = item => item.ref;
    const _uxqTicketAttention = item => window.QueueAttention.project(item, {explain:true});
    const _workersActivityRows = () => ITEMS.map(item => ({item, at:'2026-01-01T12:00:00Z',
      workerState:{label:item.needs_input ? 'Needs you' : item.closed_at ? 'Resolved' : 'Working',
        css:item.needs_input ? 'is-needs-you' : ''}, worker:'Example worker', summary:'Summary.'}));
    const _scheduleSidebarRender = () => {};
    const _ultraFreshCurrent = c => c, _applyLiveOverlayToRow = c => c;
    const _ultraFreshTime = v => Number(v || 0) * 1000;
    const workerStateBucket = () => 'idle';
    const rowOutcomeView = c => c.outcome || {};
    const wipAgeChipHtml = () => '<span class="conv-working-age">5m 43s</span>';
    const _uxqRelTime = () => '16m ago';
  ` + slice('  function escapeHtml(s)', '  function unescapeHtml(s)')
    + slice('  function sessionQuickAction(value)', '  // Model-authored chips')
    + slice('  function _workersMeaningfulUpdate(', '  // Compact history rows')
    + slice('  function _uxqRecentWorkHosts()', '  function _uxqBindWorkingStrip(')
    + '\n_uxqRenderRecentWork();'});
  return page.evaluate(() => ({
    rows: [...document.querySelectorAll('.workers-ticket-entry')].map(entry => ({
      ref: entry.dataset.workersTicket,
      height: entry.getBoundingClientRect().height,
      context: !!entry.querySelector('.workers-ticket-context'),
      buttons: entry.querySelectorAll('.workers-block-choice').length,
    })),
    awaiting: document.querySelector('.workers-awaiting').getBoundingClientRect().height,
    overflow: document.querySelector('#convList').scrollWidth > 380,
    wide: [...document.querySelectorAll('#convList *')].filter(e => e.getBoundingClientRect().right > 381 && !e.closest('.workers-awaiting-chips')).map(e => e.className + ':' + Math.round(e.getBoundingClientRect().right)).slice(0, 8),
  }));
}

for (const touch of [false, true]) {
  test('Workers rows stay inside the vertical budget' + (touch ? ' on touch screens' : ''), async () => {
    const browser = await puppeteer.launch({executablePath:findChromePath(), args:['--no-sandbox']});
    try {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const result = await renderList(page, touch);
      assert.deepEqual(errors, []);
      assert.equal(result.rows.length, ITEMS.length);
      // The fixture must exercise the expensive shapes, or the budget proves nothing.
      assert.ok(result.rows.some(row => row.buttons >= 3), 'a row with quick answers plus Answer…');
      assert.ok(result.rows.some(row => !row.context), 'a row with no context line');
      for (const row of result.rows) {
        const limit = row.context ? (touch ? BUDGET.withContextTouch : BUDGET.withContext) : BUDGET.identityOnly;
        assert.ok(row.height <= limit, `${row.ref} is ${row.height}px; budget is ${limit}px. `
          + 'Fit the feature into the existing lines, or ask the owner for more vertical budget.');
      }
      assert.ok(result.awaiting <= BUDGET.awaitingStrip, `awaiting strip is ${result.awaiting}px; budget is ${BUDGET.awaitingStrip}px`);
      assert.equal(result.overflow, false, 'rows must truncate, not scroll sideways: ' + result.wide.join(' '));
    } finally { await browser.close(); }
  });
}
