const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const q2js = fs.readFileSync('static/q2.js', 'utf8');
const css = fs.readFileSync('static/q2.css', 'utf8');

test('logbar fills the column bottom by default and only a drag pins it', () => {
  const m = css.match(/\.q2-logbar \{[\s\S]*?\n\}/);
  assert.ok(m, '.q2-logbar base rule found');
  assert.match(m[0], /flex: 1 1 auto/, 'default rule grows to fill leftover column space');
  assert.match(m[0], /height: auto/, 'no fixed percentage height by default');
  const sized = css.match(/\.q2-logbar\.is-sized \{[\s\S]*?\n\}/);
  assert.ok(sized, '.is-sized rule found');
  assert.match(sized[0], /flex: 0 0 auto/, 'dragged band stops growing');
  assert.match(sized[0], /height: var\(--q2-logbar-h, 28%\)/, 'dragged band keeps the px override');
  const collapsed = css.match(/\.q2-logbar\.is-collapsed \{[^}]*\}/);
  assert.ok(collapsed, 'collapsed rule found');
  assert.match(collapsed[0], /flex: 0 0 auto/, 'collapsed band does not fill');
});

test('setLogbarHeight marks the band sized; reset paths clear it', () => {
  const start = q2js.indexOf('  function setLogbarHeight(px, persist) {');
  const end = q2js.indexOf('\n  (function initLogbarHeight()', start);
  assert.ok(start >= 0 && end > start, 'setLogbarHeight found');
  const fn = q2js.slice(start, end);
  assert.match(fn, /classList\.add\('is-sized'\)/, 'drag pins the height');
  const resets = q2js.match(/classList\.remove\('is-sized'\)/g) || [];
  assert.equal(resets.length, 2, 'dblclick and Home-key resets both unpin');
});
