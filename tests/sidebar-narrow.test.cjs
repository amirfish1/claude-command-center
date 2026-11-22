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

test('the usage strip keeps the shared one-line status when narrow', () => {
  assert.doesNotMatch(css, /headroom-bars|hb-/);
});

test('narrow rows keep only the name and status line', () => {
  for (const cls of ['conv-idea-avatar', 'conv-auto-switch', 'conv-meta-col', 'conv-pct-badge',
    'conv-quick-action-slot', 'conv-attn-chip', 'conv-signal', 'conv-orch-lanes-chip', 'conv-family-details']) {
    assert.match(css, new RegExp('\\.' + cls + '\\b'), cls);
  }
  assert.match(css, /\.conv-main-row > \.conv-row-end \{\s*position: absolute;/);
});
