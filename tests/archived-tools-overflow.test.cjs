const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const app = fs.readFileSync('static/app.js', 'utf8');
const css = fs.readFileSync('static/app.css', 'utf8');

// CCC-1158: on narrow sidebars the archived-tools bar wrapped its pill
// clusters into ~4 rows. Grouping/density controls fold into a ⋮ overflow
// menu below the sidebar container breakpoint so the toolbar stays one row.

test('archived toolbar renders a ⋮ overflow menu with the display controls', () => {
  const start = app.indexOf('const _arcDisplayControls');
  assert.ok(start >= 0, 'overflow menu markup found');
  const block = app.slice(start, start + 1200);
  assert.match(block, /class="conv-archived-overflow"/, 'overflow details rendered');
  assert.match(block, /conv-archived-overflow-menu/, 'menu container rendered');
  assert.match(block, /_arcGroupingToggle/, 'grouping control included');
  assert.match(block, /_arcDenseToggle/, 'density control included');
});

test('CSS folds inline controls and shows the overflow below the breakpoint', () => {
  assert.match(css, /\.conv-archived-overflow\s*{[^}]*display:\s*none/, 'overflow hidden by default');
  const cq = css.match(/@container sidebar \(max-width: 600px\)\s*{([\s\S]*?)}\s*}/);
  assert.ok(cq, 'sidebar container query at 600px exists');
  assert.match(cq[1], /data-role="archived-grouping-toggle"/, 'grouping folded');
  assert.match(cq[1], /data-role="session-density-toggle"/, 'density folded');
  assert.match(cq[1], /\.conv-archived-overflow { display: inline-flex/, 'overflow shown');
});

test('menu copies are bound: toggles use querySelectorAll, not first-match', () => {
  for (const role of ['archived-grouping-toggle', 'wrap-toggle', 'details-toggle', 'session-density-toggle']) {
    assert.ok(
      app.includes(`querySelectorAll('[data-role="${role}"]')`),
      `${role} binds every rendered copy`,
    );
    assert.ok(
      !app.includes(`querySelector('[data-role="${role}"]')`),
      `${role} has no stale first-match binding`,
    );
  }
});
