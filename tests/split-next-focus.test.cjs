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
  assert.match(body, /await selectConversation\(nextId, 'p1'\);\s*setActivePaneById\('p1'\);/);
});

function advanceHarness(rows, { current = 'moved', repaint, sessionIds = {} } = {}) {
  const ctx = {
    currentConversation: current,
    sessionIdByConv: sessionIds,
    splitState: { orientation: null, panes: [{ id: 'p1', conversationId: current }], activeIndex: 0 },
    document: { querySelectorAll: () => rows },
    toasts: [],
    showOpToast: text => ctx.toasts.push(text),
    openConversationInPane: async id => {
      ctx.splitState.orientation = 'vertical';
      ctx.splitState.panes.push({ id: 'p2', conversationId: id });
      ctx.splitState.activeIndex = 1;
      if (repaint) repaint();
    },
    selectConversation: async (id, paneId) => {
      ctx.splitState.panes.find(p => p.id === paneId).conversationId = id;
    },
    setActivePaneById: paneId => { ctx.splitState.activeIndex = ctx.splitState.panes.findIndex(p => p.id === paneId); },
  };
  vm.createContext(ctx);
  vm.runInContext('async ' + extractFunction('moveMainConversationToSplitAndAdvance')
    + '; this.advance = moveMainConversationToSplitAndAdvance;', ctx);
  return ctx;
}

const row = (id, sessionId = id, visible = true) => ({
  dataset: { id, sessionId }, offsetParent: visible ? {} : null,
});

function assertSplit(ctx, left, right = 'moved') {
  assert.equal(ctx.splitState.panes[0].conversationId, left);
  assert.equal(ctx.splitState.panes[1].conversationId, right);
  assert.equal(ctx.splitState.activeIndex, 0);
}

test('split skips repeated rows and aliases for the moved session', async () => {
  const ctx = advanceHarness([row('moved'), row('moved'), row('alias', 'moved'), row('next')]);
  await ctx.advance();
  assertSplit(ctx, 'next');
});

test('split captures the next id before the sidebar repaints', async () => {
  const next = row('next');
  const ctx = advanceHarness([row('moved'), next], {
    repaint: () => { next.dataset.id = 'moved'; },
  });
  await ctx.advance();
  assertSplit(ctx, 'next');
});

test('split skips hidden rows and falls back to a preceding distinct session', async () => {
  const ctx = advanceHarness([row('previous'), row('moved'), row('hidden', 'hidden', false), row('moved')]);
  await ctx.advance();
  assertSplit(ctx, 'previous');
});

test('split uses a visible different session when the current row is filtered out', async () => {
  const ctx = advanceHarness([row('next'), row('later')]);
  await ctx.advance();
  assertSplit(ctx, 'next');
});

test('split does not create duplicate panes when no other session is visible', async () => {
  const ctx = advanceHarness([row('moved'), row('alias', 'moved')]);
  await ctx.advance();
  assert.equal(ctx.splitState.panes.length, 1);
  assert.equal(ctx.splitState.orientation, null);
  assert.equal(ctx.splitState.activeIndex, 0);
  assert.deepEqual([...ctx.toasts], ['No next conversation to load']);
});

test('split compares session identities even when conversation ids differ', async () => {
  const ctx = advanceHarness([row('current', 'session'), row('alias', 'session'), row('next')], {
    current: 'current', sessionIds: { current: 'session' },
  });
  await ctx.advance();
  assertSplit(ctx, 'next', 'current');
});
