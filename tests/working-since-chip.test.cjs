const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function extract(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return source.slice(a, b);
}
// Frozen clock so the rendered "Ns"/"Xm Ys" label is exact.
const NOW_MS = 1_000_000_000_000;
const NOW_S = NOW_MS / 1000;
function helpers() {
  const context = vm.createContext({
    escapeHtml: String,
    escapeAttr: String,
    Date: { now: () => NOW_MS },
  });
  vm.runInContext(
    extract('  function _optimisticAgeLabel(ms) {', '  // Shared by the 1s setInterval')
    + extract('  function wipAgeChipHtml(', '  const SESSION_ENGINE_LABELS = {'),
    context);
  return context;
}

test('working row renders a ticking WIP age chip', () => {
  const h = helpers();
  const html = h.wipAgeChipHtml({ working_since: NOW_S - 42, state: 'working' });
  assert.match(html, /conv-working-age/);
  assert.match(html, /data-working-since="/);
  assert.match(html, />42s</);
});

test('minute-scale ages render as Xm Ys', () => {
  const h = helpers();
  const html = h.wipAgeChipHtml({ working_since: NOW_S - 125, codex_state: 'working' });
  assert.match(html, />2m 5s</);
});

test('parked and idle sessions get no chip', () => {
  const h = helpers();
  const t = NOW_S - 30;
  assert.equal(h.wipAgeChipHtml({ working_since: t, state: 'waiting' }), '');
  assert.equal(h.wipAgeChipHtml({ working_since: t, codex_state: 'waiting' }), '');
  assert.equal(h.wipAgeChipHtml({ working_since: t, needs_approval: true }), '');
  assert.equal(h.wipAgeChipHtml({ working_since: t, question_waiting: true }), '');
  assert.equal(h.wipAgeChipHtml({ state: 'working' }), '');
  assert.equal(h.wipAgeChipHtml(null), '');
});
