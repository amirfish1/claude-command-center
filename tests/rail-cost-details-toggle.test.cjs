/* CCC-1131: session cost details must be a deliberate, keyboard-accessible
 * disclosure. Hovering the summary must not resize the status rail. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const appJs = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const appCss = fs.readFileSync(path.join(root, 'static/app.css'), 'utf8');

test('session cost details use an explicit disclosure control', () => {
  assert.match(appJs, /class="rail-cost-details-toggle"/);
  assert.match(appJs, /aria-controls="railCostDetails"/);
  assert.match(appJs, /aria-expanded="false"/);
  assert.match(appJs, /railCostDetailsToggle/);
  assert.match(appJs, /classList\.toggle\('is-expanded', expanded\)/);
  assert.match(appCss, /\.status-rail-tokens\.is-expanded \.rail-cost-details/);
  assert.doesNotMatch(appCss, /\.status-rail-tokens:hover \.rail-cost-details/);
});
