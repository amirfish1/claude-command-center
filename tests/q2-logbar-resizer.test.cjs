const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const q2 = fs.readFileSync('static/q2.js', 'utf8');
const css = fs.readFileSync('static/q2.css', 'utf8');

// CCC-1168: once the activity log is a fixed size (.is-sized — after a drag
// or a restored height) its flex-grow is off, so leftover column space used
// to open BELOW the log. The handle stayed glued under the upper content and
// dragging moved the log's bottom edge instead of its top edge. The handle
// carries margin-top:auto so the logbar+handle unit anchors to the column
// bottom and the log's top edge tracks the drag.

test('the logbar handle is bottom-anchored via auto margin', () => {
  const m = css.match(/\.q2-resizer-h\[data-q2-resize-v="logbar"\]\s*\{([^}]*)\}/);
  assert.ok(m, 'dedicated logbar-handle rule exists');
  assert.match(m[1], /margin-top:\s*auto/);
});

test('the sized logbar keeps a fixed height and does not grow', () => {
  const m = css.match(/\.q2-logbar\.is-sized\s*\{([^}]*)\}/);
  assert.ok(m, '.q2-logbar.is-sized rule exists');
  assert.match(m[1], /flex:\s*0 0 auto/);
  assert.match(m[1], /height:\s*var\(--q2-logbar-h/);
});

test('drag-up still grows the band (delta sign unchanged)', () => {
  const start = q2.indexOf("document.querySelector('[data-q2-resize-v=\"logbar\"]')");
  assert.ok(start >= 0, 'logbar handle wiring found');
  const body = q2.slice(start, start + 3000);
  assert.match(body, /setLogbarHeight\(startH \+ \(startY - ev\.clientY\), false, dragMax\)/);
});

test('a drag that starts above the 70% cap does not snap the band smaller', () => {
  // Fill mode can render the log taller than logbarMax(); the drag cap floors
  // at startH so the first drag moves the top edge WITH the cursor.
  const start = q2.indexOf("document.querySelector('[data-q2-resize-v=\"logbar\"]')");
  const body = q2.slice(start, start + 3000);
  assert.match(body, /var dragMax = Math\.max\(logbarMax\(\), startH\)/);
  const fn = q2.indexOf('function setLogbarHeight(');
  assert.match(q2.slice(fn, fn + 500), /Math\.min\(cap, px\)/);
});
