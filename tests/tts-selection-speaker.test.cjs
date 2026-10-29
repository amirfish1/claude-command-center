// CCC-170: with text selected in the conversation, every speaker reads just
// the selection; a second tap with the same selection pauses/resumes.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  // A conversation selection the current read did not start from.');
const end = source.indexOf('  async function readLastMessageAloud(paneId) {', start);
assert.ok(start >= 0 && end > start, 'selection helpers present');

function load(state) {
  const ctx = vm.createContext(Object.assign({
    _ttsActive: false, _ttsPaused: false, _ttsSelectionReadText: '', _ttsActivePaneId: 'p1',
    calls: [],
    activePaneId: () => 'p1',
  }, state));
  ctx.selectedConversationTtsData = () => (ctx.sel ? {text: ctx.sel, mapping: []} : null);
  ctx.readLastMessageAloud = (paneId) => ctx.calls.push(paneId);
  vm.runInContext(source.slice(start, end), ctx);
  return ctx;
}

test('no selection: speaker keeps its own target', () => {
  const ctx = load({sel: ''});
  assert.equal(ctx._ttsReadSelectionIfAny('p1'), false);
  assert.equal(ctx.calls.length, 0);
});

test('selection routes any speaker to the selection read', () => {
  const ctx = load({sel: 'just this paragraph'});
  assert.equal(ctx._ttsReadSelectionIfAny('p2'), true);
  assert.deepEqual([...ctx.calls], ['p2']);
});

test('same selection while reading is pause/resume, not fresh', () => {
  const ctx = load({sel: 'just this', _ttsActive: true, _ttsSelectionReadText: 'just this'});
  assert.equal(ctx._ttsFreshSelection('p1'), null);
  assert.equal(ctx._ttsReadSelectionIfAny('p2'), true);
  assert.deepEqual([...ctx.calls], ['p1']);
});

test('a new selection while reading is fresh', () => {
  const ctx = load({sel: 'other text', _ttsActive: true, _ttsSelectionReadText: 'just this'});
  assert.ok(ctx._ttsFreshSelection('p1'));
});
