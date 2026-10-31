// CCC-280: "Move to split view" must leave focus on the newly loaded left
// pane. The moved pane's open-backfill (a background Load-earlier fetch)
// used to call fetchConversationEvents(p2), which makes p2 active and never
// switches back, stealing focus after the split handler focused p1.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');

function extractFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `function ${name} not found`);
  let i = app.indexOf(') {', start) + 2;
  let depth = 0;
  for (; i < app.length; i++) {
    if (app[i] === '{') depth += 1;
    else if (app[i] === '}' && --depth === 0) return app.slice(start, i + 1);
  }
  assert.fail('unbalanced');
}

function harness() {
  const panes = [
    { id: 'p1', conversationId: 'next' },
    { id: 'p2', conversationId: 'moved', firstLine: 400 },
  ];
  const ctx = {
    splitState: { panes, activeIndex: 0 },
    paneByPaneId: (id) => panes.find(p => p.id === id) || null,
    paneIndexByPaneId: (id) => panes.findIndex(p => p.id === id),
    requestAnimationFrame: (fn) => fn(),
    setTimeout: (fn) => fn(),
    fetched: [],
  };
  // Mirrors the real prologue: the target pane becomes active, never restored.
  ctx.fetchConversationEvents = (paneId) => {
    ctx.splitState.activeIndex = ctx.paneIndexByPaneId(paneId);
    ctx.fetched.push(paneId);
    return Promise.resolve();
  };
  vm.createContext(ctx);
  vm.runInContext(extractFunction('_scheduleConvOpenBackfill') + '; this.backfill = _scheduleConvOpenBackfill;', ctx);
  return ctx;
}

test('a background backfill of the moved pane does not steal focus', () => {
  const ctx = harness();
  ctx.backfill('moved', 'p2', 200);
  assert.deepEqual([...ctx.fetched], ['p2']);
  assert.equal(ctx.splitState.activeIndex, 0);
});

test('a backfill of the already-active pane keeps it active', () => {
  const ctx = harness();
  ctx.splitState.activeIndex = 1;
  ctx.backfill('moved', 'p2', 200);
  assert.equal(ctx.splitState.activeIndex, 1);
});

test('split-and-advance still focuses the left pane after loading it', () => {
  const start = app.indexOf('async function moveMainConversationToSplitAndAdvance(');
  const body = app.slice(start, app.indexOf('\n  function closePane(', start));
  assert.match(body, /await selectConversation\(next\.dataset\.id, 'p1'\);\s*setActivePaneById\('p1'\);/);
});
