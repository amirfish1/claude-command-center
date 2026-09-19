const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const q2 = fs.readFileSync('static/q2.js', 'utf8');

// CCC-1167: in the default "Recent closed · last 12h" view the
// "Show N more closed" button bumped state.closedCap but nothing read it —
// shownClosed was hard-wired to recentClosed. pickShownClosed now admits
// closedCap - CLOSED_CAP extra older rows on top of the always-shown set.

function loadPicker() {
  const start = q2.indexOf('  function pickShownClosed(');
  const end = q2.indexOf('\n\n', start);
  assert.ok(start >= 0 && end > start, 'pickShownClosed found in static/q2.js');
  return new Function('CLOSED_CAP', q2.slice(start, end) + '; return pickShownClosed;')(50);
}

const pickShownClosed = loadPicker();

function mkClosed(n, recentIdxs, unresolvedIdxs) {
  // touchedAt-sorted newest-first, like renderTickets' `closed`.
  return Array.from({ length: n }, (_, i) => ({
    ref: 'C-' + i,
    _recent: recentIdxs.includes(i),
    resolution: unresolvedIdxs.includes(i) ? { unresolved: ['follow up'] } : {},
  }));
}

const keep = (it) => it._recent || (it.resolution && it.resolution.unresolved && it.resolution.unresolved.length > 0);

test('default cap shows exactly the recent + unresolved set', () => {
  const closed = mkClosed(120, [0, 1, 2], [80]);
  const shown = pickShownClosed(closed, keep, 50);
  assert.deepEqual(shown.map((it) => it.ref), ['C-0', 'C-1', 'C-2', 'C-80']);
});

test('each +50 click admits the next 50 oldest rows in touchedAt order', () => {
  const closed = mkClosed(200, [0, 1], []);
  const shown = pickShownClosed(closed, keep, 100);
  assert.equal(shown.length, 52);
  assert.deepEqual(shown.map((it) => it.ref).slice(0, 3), ['C-0', 'C-1', 'C-2']);
  assert.equal(shown[51].ref, 'C-51');
  // a second click widens again
  assert.equal(pickShownClosed(closed, keep, 150).length, 102);
});

test('cap beyond the list size shows everything', () => {
  const closed = mkClosed(70, [0], []);
  assert.equal(pickShownClosed(closed, keep, 200).length, 70);
});

test('unresolved rows deep in the list stay pinned when expanding', () => {
  const closed = mkClosed(100, [], [90]);
  const shown = pickShownClosed(closed, keep, 100);
  assert.equal(shown.length, 51);
  assert.ok(shown.some((it) => it.ref === 'C-90'), 'unresolved row kept');
});

test('renderTickets routes the recent branch through pickShownClosed with state.closedCap', () => {
  const start = q2.indexOf('  function renderTickets(');
  const end = q2.indexOf('\n  function ', start + 20);
  const body = q2.slice(start, end > start ? end : undefined);
  assert.match(body, /pickShownClosed\(closed, function \(it\) \{\s*return isRecentClosed\(it\) \|\| unresolvedNotes\(it\)\.length > 0;\s*\}, state\.closedCap\)/);
  // the button still bumps the cap the picker reads
  assert.match(q2, /state\.closedCap \+= CLOSED_CAP/);
});
