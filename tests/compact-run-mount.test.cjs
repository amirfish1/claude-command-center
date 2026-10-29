// CCC-151: a finished /compact card stays where the compaction ended. Codex
// writes no resume-summary row to anchor to, so the card used to be re-pinned
// under every new turn forever.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  function _compactRunMount(');
const end = source.indexOf('  function _compactRunAnchorTo(', start);
assert.ok(start >= 0 && end > start, '_compactRunMount present');

function fakeView() {
  const view = {
    children: [],
    get lastElementChild() { return this.children[this.children.length - 1] || null; },
    appendChild(el) {
      if (el.parentNode) el.parentNode.children.splice(el.parentNode.children.indexOf(el), 1);
      this.children.push(el);
      el.parentNode = this;
      return el;
    },
  };
  return view;
}
function load(run) {
  const ctx = vm.createContext({
    _compactRun: run,
    _compactRunIsForeground: () => true,
    _compactRunView: () => null,
    document: { createElement: () => ({ dataset: {}, parentNode: null }) },
  });
  vm.runInContext(source.slice(start, end), ctx);
  return ctx;
}

test('a live run is kept at the tail', () => {
  const run = { sid: 's1', stage: 'working' };
  const ctx = load(run);
  const view = fakeView();
  ctx._compactRunMount(view);
  view.appendChild({ dataset: {} });          // a new event lands
  ctx._compactRunMount(view);
  assert.equal(view.lastElementChild, run.el);
  assert.ok(!run.anchored);
});

test('a done run without a summary row stays put under new turns', () => {
  const run = { sid: 's1', stage: 'working' };
  const ctx = load(run);
  const view = fakeView();
  ctx._compactRunMount(view);
  run.stage = 'done';
  ctx._compactRunMount(view);                 // completeCompactRun's mount
  const next = { dataset: {} };
  view.appendChild(next);                     // the next turn renders
  ctx._compactRunMount(view);                 // later render passes
  ctx._compactRunMount(view);
  assert.equal(view.lastElementChild, next);
  assert.equal(view.children.indexOf(run.el), 0);
  assert.equal(run.anchored, true);
});
