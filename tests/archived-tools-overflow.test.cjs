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

test('CSS folds inline controls into the always-visible overflow below the breakpoint', () => {
  // CCC-283: the ⋮ is always shown (it holds the engine filter + Table view).
  assert.match(css, /#convList \.conv-archived-overflow\s*{[^}]*display:\s*inline-flex/, 'overflow always shown');
  const cq = css.match(/@container sidebar \(max-width: 600px\)\s*{([\s\S]*?)}\s*}/);
  assert.ok(cq, 'sidebar container query at 600px exists');
  assert.match(cq[1], /data-role="archived-grouping-toggle"/, 'grouping folded');
  assert.match(cq[1], /data-role="session-density-toggle"/, 'density folded');
});

test('engine filter and Table view live only in the ⋮ menu (CCC-283)', () => {
  const start = app.indexOf("const _arcTools = '<div class=\"conv-archived-tools\"");
  assert.ok(start >= 0, 'toolbar markup found');
  const row = app.slice(start, start + 600);
  assert.doesNotMatch(row, /_arcEngineToggle|_trackedTableToggleHtml/, 'not inline in the toolbar row');
  const menuStart = app.indexOf('const _arcDisplayControls');
  const menu = app.slice(menuStart, menuStart + 600);
  assert.match(menu, /_arcEngineMenuRow/, 'engine row in menu');
  assert.match(menu, /_arcTrackedTableOpt/, 'Table view item in menu');
  assert.match(app, /const _arcEngineMenuRow = '<span class="conv-archived-engine-filter is-menu-row" data-role/, 'engine row not phone-only');
  assert.match(app, /conv-tracked-table-opt[\s\S]{0,200}role="menuitemcheckbox" aria-checked=/, 'Table item shows on/off state');
});

test('menu copies are bound: toggles use querySelectorAll, not first-match', () => {
  for (const role of ['archived-grouping-toggle', 'session-density-toggle']) {
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
