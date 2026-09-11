const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('puppeteer');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

// Run the real presentation + fold code, not a re-implementation of it: the
// question "what does this column say?" is answered by sessionIconPresentation,
// so a hand-rolled stub would happily agree with a broken app.
const source = app.slice(
  app.indexOf('  // SESSION_ICON_PRESENTATION_START'),
  app.indexOf('  // WORKERS_UNIFORM_COLUMNS_END')
);

function fold(rows) {
  const ctx = {
    formatModelEffort: (m) => String(m || ''),
    rowReasoningEffort: () => '',
    sessionIsOptimisticallySending: () => false,
    // Ticket attribution lives 57k lines away; the fold only asks whether any
    // row has tickets, so the answer is the fixture's business.
    _uxFixesWorkerTicketsForRow: (c) => c.tickets || [],
  };
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return ctx._workersUniformColumns(rows);
}

const worker = (engine, model, extra) => ({ engine, model, state: 'interactive', ...extra });

// The first version of this hoisted a column only when EVERY row agreed. On
// the real tab that rule never once fired -- 16 Codex sessions and 2 others is
// a repeating column by any human reading of it -- so the glyph still drew on
// all 18 rows and the feature was invisible. Majority + visible exceptions is
// what actually removes the repetition.
test('a column is hoisted by majority, and the exceptions are counted', () => {
  const allCodexTerra = [worker('codex', 'terra'), worker('codex', 'terra'), worker('codex', 'terra')];
  const both = fold(allCodexTerra);
  assert.equal(both.engine, 'codex');
  assert.equal(both.tier, 'high');
  assert.equal(both.count, 3);
  assert.equal(both.engineOthers, 0);
  assert.equal(both.tierOthers, 0);
  assert.equal(both.tierRange, '$$');

  // The shape from the screenshot that started this: a strong majority with a
  // couple of stragglers. Hoist it, and say how many rows disagree.
  const fleet = fold([
    ...Array.from({ length: 16 }, () => worker('codex', 'terra')),
    worker('claude', 'opus'),
    worker('claude', 'haiku'),
  ]);
  assert.equal(fleet.engine, 'codex');
  assert.equal(fleet.engineOthers, 2);
  assert.equal(fleet.count, 18);
  // Cost is a separate vote from engine: claude/opus is also 'high', so 17 of
  // the 18 share a tier even though two engines are in play. The range still
  // names both ends, so the banner cannot imply one flat price.
  assert.equal(fleet.tier, 'high');
  assert.equal(fleet.tierOthers, 1);
  assert.equal(fleet.tierRange, 'Low–$$');

  // A tie is not a majority: 1-and-1 has no repetition to remove.
  assert.equal(fold([worker('codex', 'terra'), worker('claude', 'opus')]).engine, '');
  // Nor does an even split three ways.
  assert.equal(fold([worker('codex', 'terra'), worker('claude', 'opus'),
                     worker('kimi', 'k3'), worker('codex', 'luna')]).engine, '');

  // An unrecognised model has no tier; '' is absence, not agreement, so it can
  // neither win the vote nor be folded away.
  assert.equal(fold([worker('codex', 'mystery'), worker('codex', 'mystery')]).tier, '');

  // One row is not a repeating column, and neither is none.
  assert.equal(fold([worker('codex', 'terra')]).engine, '');
  assert.equal(fold([]).count, 0);
});

// "no tickets" on every row is not information, it is the repetition this tab
// came here to lose. The marker only earns its column when the column has
// something in it somewhere.
test('the no-tickets marker is only claimed when some row has tickets', () => {
  assert.equal(fold([worker('codex', 'terra'), worker('codex', 'terra')]).anyTickets, false);
  assert.equal(fold([
    worker('codex', 'terra', { tickets: [{ ref: 'OPS-1' }] }),
    worker('codex', 'terra'),
  ]).anyTickets, true);
});

// The failure this pins is the one that already happened once on this tab: a
// class that never reached the DOM, leaving a whole stylesheet block dead
// while the source read as if the feature shipped. Measure the layout.
test('hoisting drops the majority glyph, keeps the exception, reclaims the column', async () => {
  const rowHtml = (i, iconCls) => `<div class="conv-item">
    <span class="conv-session-icon codex cost-high is-working ${iconCls}">
      <svg class="conv-session-svg" width="14" height="14"><rect width="14" height="14"></rect></svg>
      <span class="session-activity-dot"></span>
      <span class="session-tier-cost"><i>$</i><i>$</i></span>
    </span>
    <div class="conv-main-row"><div class="conv-title-row">
      <span class="conv-title">OPS#99${i}: some worker did some work</span>
    </div></div>
  </div>`;

  const browser = await puppeteer.launch({ headless: true });
  try {
    const page = await browser.newPage();
    // Desktop width: the narrow-container rule hides .conv-session-icon
    // outright below 460px, which would mask what these rules do.
    await page.setViewport({ width: 1400, height: 1200 });
    await page.setContent(
      `<html><head><style>${css}</style></head><body>`
      + `<div id="convList" style="width:500px">`
      // Rows 1-4 match the hoisted engine+cost; row 5 is the exception.
      + [1, 2, 3, 4].map((i) => rowHtml(i, 'hoisted-engine hoisted-cost')).join('')
      + rowHtml(5, '')
      + `</div></body></html>`
    );

    const probe = (classes) => page.evaluate((cls) => {
      const list = document.getElementById('convList');
      list.className = cls;
      const items = [...list.querySelectorAll('.conv-item')];
      const vis = (el) => !!el && getComputedStyle(el).display !== 'none';
      const read = (item) => ({
        glyph: vis(item.querySelector('.conv-session-svg')),
        dollars: vis(item.querySelector('.session-tier-cost')),
        dot: vis(item.querySelector('.session-activity-dot')),
        titleLeft: Math.round(item.querySelector('.conv-title').getBoundingClientRect().left),
      });
      return { majority: read(items[0]), exception: read(items[4]), rows: items.length };
    }, classes);

    const plain = await probe('');
    const engineOnly = await probe('workers-hoist-engine');
    const both = await probe('workers-hoist-engine workers-hoist-cost');
    const narrow = await probe('workers-hoist-engine workers-hoist-cost workers-icon-narrow');

    assert.ok(plain.majority.glyph && plain.majority.dollars, 'baseline shows both columns');

    // Engine hoisted, cost varying: the matching rows drop the glyph and keep
    // the dollars.
    assert.equal(engineOnly.majority.glyph, false);
    assert.equal(engineOnly.majority.dollars, true);
    // ...and the row that DIFFERS keeps its glyph. That is the whole point:
    // the column now shows exactly the exceptions.
    assert.equal(engineOnly.exception.glyph, true);

    // Both hoisted: the matching rows go blank, the exception is untouched.
    assert.equal(both.majority.glyph, false);
    assert.equal(both.majority.dollars, false);
    assert.equal(both.exception.glyph, true);
    assert.equal(both.exception.dollars, true);

    // The column is shared by every row, so it may only shrink when nothing
    // renders in it anywhere -- which is the flag JS sets separately.
    assert.equal(both.majority.titleLeft, plain.majority.titleLeft,
      'an exception row still needs the room, so the column must not move');
    assert.ok(plain.majority.titleLeft - narrow.majority.titleLeft >= 24,
      `expected >=24px reclaimed, got ${plain.majority.titleLeft - narrow.majority.titleLeft}px`);

    // Working-vs-idle is exactly what differs per row, so the dot never folds.
    assert.equal(both.majority.dot, true);
    // Comprehensiveness: hoisting hides columns, never sessions.
    assert.equal(both.rows, plain.rows);
  } finally { await browser.close(); }
});
