'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const api = require('../static/sidebar-narrow.js');
const css = fs.readFileSync(path.join(root, 'static/sidebar-narrow.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');

test('only a desktop sidebar below the threshold is narrow', () => {
  assert.equal(api.NARROW_PX, 320);
  assert.equal(api.isNarrow(240, true), true);
  assert.equal(api.isNarrow(319, true), true);
  assert.equal(api.isNarrow(320, true), false);
  assert.equal(api.isNarrow(380, true), false);
  // Phones and small windows keep their own layout, whatever the column width.
  assert.equal(api.isNarrow(240, false), false);
  // A hidden (collapsed) sidebar measures 0 and must not flip the class.
  assert.equal(api.isNarrow(0, true), false);
});

test('page loads the stylesheet and the watcher once each', () => {
  assert.equal(html.split('href="/static/sidebar-narrow.css"').length - 1, 1);
  assert.equal(html.split('src="/static/sidebar-narrow.js"').length - 1, 1);
});

test('every rule is scoped to the narrow class', () => {
  const body = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const selectors = body.split('}').map((rule) => rule.split('{')[0].trim()).filter(Boolean);
  assert.ok(selectors.length > 20);
  for (const group of selectors) {
    for (const selector of group.split(',')) {
      assert.match(selector.trim(), /^\.sidebar\.is-narrow\b/, selector.trim());
    }
  }
});

test('the usage strip cannot overlap: one grid line per engine', () => {
  assert.match(css, /\.hb-grid > \.hb-chip \{[^}]*grid-template-columns: subgrid/);
  assert.match(css, /\.hb-sub \{ display: none; \}/);
});
