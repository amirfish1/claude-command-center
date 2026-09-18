/* Canvas uses the native annotation flow and can be opened beside the dashboard. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');

test('Canvas exposes the native annotation entry point', () => {
  const html = read('static/canvas.html');
  const annotation = read('static/q2-annotation.js');
  assert.match(html, /id="pcAnnotateBtn"/);
  assert.match(html, /src="\/static\/q2-annotation\.js"/);
  assert.match(html, /Q2Annotation\.attach\(document\.getElementById\('pcAnnotateBtn'\)/);
  assert.match(html, /onStatus: function \(message\)/);
  assert.match(html, /getElementById\('pcToast'\)/);
  assert.match(annotation, /options\.onStatus/);
});

test('only the Canvas rail entry opens in a separate window', () => {
  const rail = read('static/app-rail.js');
  assert.match(rail, /app\.id === "pipeline-canvas"/);
  assert.match(rail, /a\.target = "_blank"/);
  assert.match(rail, /a\.rel = "noopener"/);
});
