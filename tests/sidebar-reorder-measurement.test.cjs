const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = source.indexOf('    if (_flipBefore.size > 0 && !document.hidden) {');
const end = source.indexOf("    $convList.querySelectorAll('[data-role=\"repeat-row-group-toggle\"]')", start);
assert.ok(start > 0 && end > start);

function render(positions) {
  const reads = [], frames = [], logs = [];
  const rows = positions.map(([id, top]) => ({
    id, style: {}, getAttribute: () => id,
    getBoundingClientRect() { reads.push(id); return {top}; },
    addEventListener() {},
  }));
  const context = {
    _flipBefore: new Map([['a', 100], ['b', 200]]),
    _flipBeforeOrder: new Map([['a', 0], ['b', 1]]),
    document: {hidden: false},
    $convList: {querySelectorAll: () => rows, offsetHeight: 400},
    _logReshuffle: (moves, suppressed) => logs.push({moves, suppressed}),
    requestAnimationFrame: callback => frames.push(callback),
  };
  vm.runInNewContext(source.slice(start, end), context);
  return {rows, reads, frames, logs};
}

test('insertions and duplicate rows preserve order without measuring new positions', () => {
  const result = render([['a', 110], ['new', 160], ['b', 220], ['a', 300]]);
  assert.deepEqual(result.reads, []);
  assert.deepEqual(result.frames, []);
  assert.ok(result.rows.every(row => !row.style.transform));
});

test('real reorders still measure and animate the first instance of each row', () => {
  const result = render([['b', 100], ['a', 200], ['b', 300]]);
  assert.deepEqual(result.reads, ['b', 'a']);
  assert.equal(result.rows[0].style.transform, 'translateY(100px)');
  assert.equal(result.rows[1].style.transform, 'translateY(-100px)');
  assert.equal(result.rows[2].style.transform, undefined);
  assert.equal(result.logs[0].suppressed, false);
  assert.equal(result.frames.length, 1);
  result.frames[0]();
  assert.equal(result.rows[0].style.transform, '');
  assert.match(result.rows[0].style.transition, /280ms/);
});
