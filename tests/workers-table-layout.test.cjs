const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('puppeteer');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

// Build rows from _renderRow's ACTUAL return template, not from markup written
// by hand in this file. A hand-written fixture keeps passing after someone
// moves .conv-meta-col or re-parents the ticket chips -- which is precisely
// the change that would break the table's column placement.
const TPL_START = `      return '<div class="conv-item'`;
const TPL_END = `          : '');`;
const tplFrom = app.indexOf(TPL_START);
assert.notEqual(tplFrom, -1, "could not find _renderRow's return template");
const tplTo = app.indexOf(TPL_END, tplFrom) + TPL_END.length;
const template = app.slice(tplFrom, tplTo);

const escape = (v) => String(v ?? '').replace(/[&<>"]/g, ch => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));

function renderRow(row) {
  // Every identifier the template reaches for that we do not pin resolves to
  // '' -- the optional badges a given row simply does not have.
  const named = {
    c: { id: row.id, session_id: row.id },
    title: row.title,
    rel: row.age,
    titleClass: '',
    sessionIconHtml: '<span class="conv-session-icon codex cost-high is-working">'
      + '<svg class="conv-session-svg" width="14" height="14"></svg>'
      + '<span class="session-activity-dot"></span>'
      + '<span class="session-tier-cost"><i>$</i><i>$</i></span></span>',
    workingDotHtml: row.working ? '<span class="conv-working-dot"></span>' : '',
    pctBadgeHtml: row.pct
      ? '<span class="conv-pct-badge is-actionable" role="button" tabindex="0"'
        + ' data-role="conv-pct-compact" data-pct="' + row.pct + '" title="ctx">' + row.pct + '%</span>'
      : '',
    outcomeHtml: '<div class="conv-outcome">outcome card text that would wrap onto its own line</div>',
    escapeHtml: escape,
    escapeAttr: escape,
    rowDraggableAttr: () => 'true',
    isMobileChromeActive: () => false,
    _uxFixesWorkerHistoryHtml: () => row.tickets && row.tickets.length
      ? '<details class="conv-worker-history"><summary>'
        + '<span class="conv-worker-history-count">' + row.tickets.length + ' recorded tickets</span>'
        + '<span class="conv-worker-history-recent">'
        + row.tickets.map(t => '<button type="button" class="conv-worker-ticket">' + t + '</button>').join('')
        + '</span></summary><ul class="conv-worker-history-list"></ul></details>'
      : '<span class="conv-worker-no-tickets" aria-hidden="true">no tickets</span>',
    _continuationChainBadgeHtml: () => row.legs
      ? '<span class="conv-chain-badge">↱ ' + row.legs + ' legs</span>' : '',
  };
  const ctx = new Proxy(named, {
    has: () => true,
    get: (target, key) => (key in target ? target[key] : ''),
  });
  vm.createContext(ctx);
  return vm.runInContext(`(function () {\n${template}\n})()`, ctx);
}

const ROWS = [
  { id: 'a', title: "Made cross-repository close proofs discover the caller's current repo",
    age: '36m', pct: 16, working: true, legs: 2, tickets: ['OPS-996', '-995', '-994'] },
  { id: 'b', title: 'Final code review', age: '37m', pct: 13, working: false, tickets: [] },
  { id: 'c', title: 'Moved the reported scheduled Codex run to Workers',
    age: '36m', pct: 20, working: true, tickets: ['CCC-1058'] },
  { id: 'd', title: 'Hunch is available; Codex deferred it from the top-level inventory',
    age: '1h', pct: 13, working: false, tickets: ['OPS-992'] },
];

async function withList(fn) {
  const browser = await puppeteer.launch({ headless: true });
  try {
    const page = await browser.newPage();
    // Desktop width: below 460px a container query hides the icon column and
    // below 760px it drops the meta chips, either of which would mask this.
    await page.setViewport({ width: 1400, height: 1000 });
    // The live list carries the user's row presets, and every one of them
    // declares !important. A bare #convList made this suite pass green while
    // the real tab rendered 8px-padded rounded cards with 16px titles -- the
    // card list Compact exists to replace. Set the LOUDEST combination here so
    // the table has to beat it, not sit in a vacuum.
    await page.setContent(
      `<html data-conv-rowstyle="large-bright"><head><style>${css}</style></head><body>`
      + `<div id="convList" class="workers-dense workers-tickets-present workers-hoist-engine workers-hoist-cost"`
      + ` data-rows-spacing="airy" data-row-delineation="cards" style="width:520px">`
      + ROWS.map(renderRow).join('')
      + `</div></body></html>`
    );
    return await fn(page);
  } finally { await browser.close(); }
}

const geometry = (page) => page.evaluate(() => {
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top),
             mid: Math.round(r.top + r.height / 2), width: Math.round(r.width), height: Math.round(r.height) };
  };
  return [...document.querySelectorAll('#convList .conv-item')].map(item => ({
    height: Math.round(item.getBoundingClientRect().height),
    title: box(item.querySelector('.conv-title')),
    chips: box(item.querySelector('.conv-worker-history, .conv-worker-no-tickets')),
    chain: box(item.querySelector('.conv-chain-badge')),
    pct: box(item.querySelector('.conv-main-row > .conv-pct-badge')),
    end: box(item.querySelector('.conv-row-end')),
    outcomeShown: !!item.querySelector('.conv-outcome')
      && getComputedStyle(item.querySelector('.conv-outcome')).display !== 'none',
  }));
});

test('Compact lays the rows out as a table with aligned right-hand columns', async () => {
  const rows = await withList(geometry);
  assert.equal(rows.length, ROWS.length, 'every session still renders a row');

  // The defining property of a table: the fixed columns share an x position
  // down the whole list. A flex row would let each one float to its own spot.
  const pctLefts = new Set(rows.map(r => r.pct.left));
  const endLefts = new Set(rows.map(r => r.end.left));
  assert.equal(pctLefts.size, 1, `context % must align down the list, got ${[...pctLefts]}`);
  assert.equal(endLefts.size, 1, `age must align down the list, got ${[...endLefts]}`);

  // Column ORDER, left to right: title | chips | ctx% | age.
  for (const r of rows) {
    assert.ok(r.title.right <= r.chips.left, 'chips sit right of the title');
    assert.ok(r.chips.right <= r.pct.left, 'context % sits right of the chips');
    assert.ok(r.pct.right <= r.end.left, 'age sits right of the context %');
  }
});

test('the chips share the title\'s line instead of dropping to a second one', async () => {
  const rows = await withList(geometry);
  for (const r of rows) {
    // This is what the card list got wrong: ticket chips are a DOM sibling of
    // the title row, so without explicit placement they land on row 2.
    assert.ok(Math.abs(r.chips.mid - r.title.mid) <= 2,
      `chips must be vertically centred on the title line, off by ${Math.abs(r.chips.mid - r.title.mid)}px`);
    // One text line plus padding. The card list ran ~120px per row.
    assert.ok(r.height <= 30, `expected a single-line row, got ${r.height}px`);
    assert.equal(r.outcomeShown, false, 'Compact drops the outcome card');
  }
  // A worker that closed nothing says so rather than leaving the cell blank.
  assert.ok(rows[1].chips.width > 0);
  // The continuation chain states its depth on the head row.
  assert.ok(rows[0].chain && rows[0].chain.width > 0, 'the 2-leg chain badge renders');
  assert.equal(rows[1].chain, null, 'a single-leg session carries no chain badge');
});

test('the table is Compact-only: Cozy and Detailed keep the card list', async () => {
  const seen = await withList(page => page.evaluate(() => {
    const list = document.getElementById('convList');
    const read = () => {
      const item = list.querySelector('.conv-item');
      const chips = item.querySelector('.conv-worker-history');
      const title = item.querySelector('.conv-title');
      return {
        display: getComputedStyle(item).display,
        // display:contents is the flattening the grid depends on; without the
        // Compact class the wrapper must be inert again.
        metaCol: getComputedStyle(item.querySelector('.conv-meta-col')).display,
        chipsBelow: chips.getBoundingClientRect().top >= title.getBoundingClientRect().bottom,
        noTickets: getComputedStyle(
          list.querySelectorAll('.conv-item')[1].querySelector('.conv-worker-no-tickets')).display,
      };
    };
    const compact = read();
    list.classList.remove('workers-dense');
    const cozy = read();
    return { compact, cozy };
  }));
  assert.equal(seen.compact.display, 'grid');
  assert.equal(seen.compact.metaCol, 'flex');
  // Grid blockifies its items, so assert it is shown, not how.
  assert.notEqual(seen.compact.noTickets, 'none');

  assert.notEqual(seen.cozy.display, 'grid', 'Cozy must not inherit the table layout');
  assert.equal(seen.cozy.metaCol, 'contents', 'the wrapper is invisible outside the table');
  assert.equal(seen.cozy.chipsBelow, true, 'Cozy keeps the chips on their own line');
  // The marker is a table affordance; on a card list it is repetition.
  assert.equal(seen.cozy.noTickets, 'none');
});
