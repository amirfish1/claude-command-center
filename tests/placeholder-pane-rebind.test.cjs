// CCC-165: a spawn placeholder in a split pane that is NOT focused must still
// swap onto the real session when the placeholder reconciles, or that pane
// keeps "Thinking..." forever while the answer lands on the real row.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  const _resolvedPlaceholderTargets = new Map();');
const end = app.indexOf('  function rebindCurrentSelectionToRealCard(real) {');
assert.ok(start > 0 && end > start, 'resolvePlaceholderPanes source range');
const src = app.slice(start, end);

function load(panes, activeIndex) {
  const calls = [];
  const ctx = {
    splitState: { panes, activeIndex },
    activePaneId: () => panes[activeIndex].id,
    selectConversation: (id, paneId) => { calls.push([id, paneId]); return Promise.resolve(); },
    paneByPaneId: id => panes.find(p => p.id === id) || null,
    setActivePaneById: id => { calls.push(['focus', id]); },
    calls,
  };
  vm.createContext(ctx);
  vm.runInContext(src + '\nthis.resolvePlaceholderPanes = resolvePlaceholderPanes;'
    + '\nthis.targets = _resolvedPlaceholderTargets;', ctx);
  return ctx;
}

const flush = () => new Promise(r => setImmediate(r));

test('rebinds an unfocused pane still showing the placeholder, then refocuses', async () => {
  const ctx = load([
    { id: 'p1', conversationId: 'other-session' },
    { id: 'p2', conversationId: 'spawning-tmp-1' },
  ], 0);
  ctx.resolvePlaceholderPanes(['spawning-tmp-1', 'spawning-42'], { id: 'real-sid' });
  await flush();
  assert.deepEqual(ctx.calls, [['real-sid', 'p2'], ['focus', 'p1']]);
  assert.equal(ctx.targets.get('spawning-tmp-1'), 'real-sid');
  assert.equal(ctx.targets.get('spawning-42'), 'real-sid');
});

test('leaves the focused pane to rebindCurrentSelectionToRealCard', async () => {
  const ctx = load([{ id: 'p1', conversationId: 'spawning-42' }], 0);
  ctx.resolvePlaceholderPanes(['spawning-42'], { session_id: 'real-sid' });
  await flush();
  assert.deepEqual(ctx.calls, []);
  assert.equal(ctx.targets.get('spawning-42'), 'real-sid');
});

test('no real id is a no-op', () => {
  const ctx = load([{ id: 'p1', conversationId: 'x' }, { id: 'p2', conversationId: 'spawning-42' }], 0);
  ctx.resolvePlaceholderPanes(['spawning-42'], {});
  assert.deepEqual(ctx.calls, []);
  assert.equal(ctx.targets.size, 0);
});
