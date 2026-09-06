const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('puppeteer');

const css = fs.readFileSync('static/app.css', 'utf8');
const app = fs.readFileSync('static/app.js', 'utf8');

// A worker row carries a title plus the second-line chrome Dense drops.
const row = (i) => `<div class="conv-item">
  <div class="conv-main-row"><div class="conv-title-row">
    <span class="conv-title">OPS#99${i}: Resolved catalog root lookup error and reported back</span>
  </div></div>
  <div class="conv-last">last message preview</div>
  <div class="conv-outcome">outcome card</div>
  <details class="conv-worker-history"><summary><span class="conv-worker-history-count">4 tickets</span></summary></details>
</div>`;

async function measure(page, dense) {
  await page.evaluate((on) => {
    document.getElementById('convList').classList.toggle('workers-dense', on);
  }, dense);
  return page.evaluate(() => {
    const rows = [...document.querySelectorAll('#convList .conv-item')];
    return {
      matched: document.querySelectorAll('#convList.workers-dense .conv-item').length,
      rowCount: rows.length,
      total: Math.round(rows.reduce((s, r) => s + r.getBoundingClientRect().height, 0)),
    };
  });
}

// The bug this pins: Dense used to set its class on the archived-section
// wrapper, which is rebuilt by render paths that don't carry the flag. The
// class never reached the DOM, so the whole stylesheet block was dead and
// clicking Dense left the list byte-identical. Assert the layout actually
// moves rather than asserting the source text says it should.
test('dense mode measurably shortens worker rows without dropping any', async () => {
  const browser = await puppeteer.launch({ headless: true });
  try {
    const page = await browser.newPage();
    // Desktop width on purpose: the mobile breakpoint sets row padding with
    // !important, which would mask the rule under test.
    await page.setViewport({ width: 1400, height: 1200 });
    await page.setContent(
      `<html><head><style>${css}</style></head><body>`
      + `<div id="convList" style="width:500px">`
      + [1,2,3,4,5,6,7,8,9,10].map(row).join('')
      + `</div></body></html>`
    );

    const off = await measure(page, false);
    const on = await measure(page, true);

    assert.equal(off.matched, 0, 'dense rules must not apply with the class off');
    assert.equal(on.matched, 10, 'dense rules must bind to every row with the class on');
    // Comprehensiveness is the hard constraint: Dense hides chrome, never rows.
    assert.equal(on.rowCount, off.rowCount);
    assert.ok(on.total < off.total * 0.6,
      `expected dense to cut list height by >40%, got ${off.total}px -> ${on.total}px`);
  } finally { await browser.close(); }
});

test('the dense class is driven off convList, not a re-rendered wrapper', () => {
  // applyRowDensityToggles is the same hook .compact-rows uses, and it now
  // runs on every render (it used to run once at startup, so .workers-dense
  // followed you out of Workers into Coding).
  assert.match(app, /\$convList\.classList\.toggle\('workers-dense', workersDenseTabActive\(\)\)/);
  assert.match(app, /objects-scroll-split', _objectsSplitActive\);[\s\S]{0,900}?\n    applyRowDensityToggles\(\);/);
  // The stylesheet has to be scoped to match, or the class binds to nothing.
  assert.match(css, /#convList\.workers-dense \.conv-item \{/);
  assert.ok(!/^\.workers-dense /m.test(css),
    'no bare .workers-dense rules: they matched the wrapper that never carried the class');
});

// The three-way control replaced a Dense pill and a Details pill that between
// them expressed one axis twice. Exercise the real model, not its markup.
const densityModel = (() => {
  const src = app.slice(app.indexOf("  const WORKERS_DENSITY_KEY = 'ccc-workers-density';"),
                        app.indexOf('  // Row spacing:'));
  return (stored) => {
    const store = { ...stored };
    const ctx = {
      WORKERS_DENSE_KEY: 'ccc-workers-dense',
      localStorage: {
        getItem: (k) => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
      },
    };
    vm.createContext(ctx);
    vm.runInContext(src, ctx);
    return { ctx, store };
  };
})();

test('density is one control: three modes, and Detailed drives the Details key', () => {
  // Default: the tab opens compact, which is what the old pill defaulted to.
  assert.equal(densityModel({}).ctx.workersDensity(), 'compact');
  assert.equal(densityModel({}).ctx.workersDenseOn(), true);

  // Users who had turned the old Dense pill off wanted their row back -- that
  // is Cozy. It never meant "and show me outcome cards too".
  assert.equal(densityModel({ 'ccc-workers-dense': '0' }).ctx.workersDensity(), 'cozy');
  assert.equal(densityModel({ 'ccc-workers-dense': '1' }).ctx.workersDensity(), 'compact');
  // An explicit choice outranks the legacy key.
  assert.equal(densityModel({ 'ccc-workers-dense': '0', 'ccc-workers-density': 'detailed' })
    .ctx.workersDensity(), 'detailed');
  // Garbage in storage falls back rather than rendering an unknown mode.
  assert.equal(densityModel({ 'ccc-workers-density': 'enormous' }).ctx.workersDensity(), 'compact');

  for (const [mode, dense, compactRows] of [['compact', true, '1'], ['cozy', false, '1'], ['detailed', false, '0']]) {
    const { ctx, store } = densityModel({});
    assert.equal(ctx.setWorkersDensity(mode), mode);
    assert.equal(ctx.workersDensity(), mode);
    assert.equal(ctx.workersDenseOn(), dense);
    // Detailed is exactly "show the outcome card", so it writes the Details
    // toggle's own key instead of a second flag that could disagree with it.
    assert.equal(store['ccc-compact-rows'], compactRows);
  }

  const { ctx } = densityModel({});
  assert.equal(ctx.setWorkersDensity('nonsense'), 'compact');
});
