const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('puppeteer');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

// Run the real presentation + fold code, not a re-implementation of it: the
// question "is this column uniform?" is answered by sessionIconPresentation,
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
  };
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return ctx._workersUniformColumns(rows);
}

const worker = (engine, model) => ({ engine, model, state: 'interactive' });

test('a column is hoisted only when every row in view agrees', () => {
  const allCodexTerra = [worker('codex', 'terra'), worker('codex', 'terra'), worker('codex', 'terra')];
  const both = fold(allCodexTerra);
  assert.equal(both.engine, 'codex');
  assert.equal(both.tier, 'high');
  assert.equal(both.count, 3);

  // Engine agrees, cost does not: hoist the engine, leave the dollars on the
  // rows. Folding a column that varies would delete information.
  const mixedCost = fold([worker('codex', 'terra'), worker('codex', 'luna')]);
  assert.equal(mixedCost.engine, 'codex');
  assert.equal(mixedCost.tier, '');

  const mixedEngine = fold([worker('codex', 'terra'), worker('claude', 'opus')]);
  assert.equal(mixedEngine.engine, '');

  // An unrecognised model has no tier; '' is absence, not agreement.
  assert.equal(fold([worker('codex', 'mystery'), worker('codex', 'mystery')]).tier, '');

  // One row is not a repeating column, and neither is none.
  assert.equal(fold([worker('codex', 'terra')]).engine, '');
  assert.equal(fold([]).count, 0);
});

// The failure this pins is the one that already happened once on this tab: a
// class that never reached the DOM, leaving a whole stylesheet block dead
// while the source read as if the feature shipped. Measure the layout.
test('hoisting reclaims the icon column and keeps the activity dot', async () => {
  const rowHtml = (i) => `<div class="conv-item">
    <span class="conv-session-icon codex cost-high is-working">
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
      + [1, 2, 3, 4, 5].map(rowHtml).join('')
      + `</div></body></html>`
    );

    const probe = (classes) => page.evaluate((cls) => {
      const list = document.getElementById('convList');
      list.className = cls;
      const item = list.querySelector('.conv-item');
      const vis = (el) => !!el && getComputedStyle(el).display !== 'none';
      return {
        glyph: vis(item.querySelector('.conv-session-svg')),
        dollars: vis(item.querySelector('.session-tier-cost')),
        dot: vis(item.querySelector('.session-activity-dot')),
        titleLeft: Math.round(item.querySelector('.conv-title').getBoundingClientRect().left),
        rows: list.querySelectorAll('.conv-item').length,
      };
    }, classes);

    const plain = await probe('');
    const engineOnly = await probe('workers-hoist-engine');
    const both = await probe('workers-hoist-engine workers-hoist-cost');

    assert.ok(plain.glyph && plain.dollars, 'baseline shows both columns');

    // Engine uniform, cost varying: glyph goes, dollars stay.
    assert.equal(engineOnly.glyph, false);
    assert.equal(engineOnly.dollars, true);
    assert.ok(engineOnly.titleLeft < plain.titleLeft,
      `expected the title to move left, got ${plain.titleLeft} -> ${engineOnly.titleLeft}`);

    // Both uniform: the whole column collapses.
    assert.equal(both.glyph, false);
    assert.equal(both.dollars, false);
    assert.ok(both.titleLeft < engineOnly.titleLeft,
      `expected a further reclaim, got ${engineOnly.titleLeft} -> ${both.titleLeft}`);
    assert.ok(plain.titleLeft - both.titleLeft >= 24,
      `expected >=24px reclaimed, got ${plain.titleLeft - both.titleLeft}px`);

    // Working-vs-idle is exactly what differs per row, so the dot never folds.
    assert.equal(both.dot, true);
    // Comprehensiveness: hoisting hides columns, never sessions.
    assert.equal(both.rows, plain.rows);
  } finally { await browser.close(); }
});
