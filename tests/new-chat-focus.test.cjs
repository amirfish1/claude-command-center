const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(process.env.CCC_APP_SOURCE || path.join(__dirname, '../static/app.js'), 'utf8');

function fn(name, async = false) {
  const from = app.indexOf('  ' + (async ? 'async ' : '') + 'function ' + name + '(');
  const to = app.indexOf('\n  }\n', from);
  assert.ok(from >= 0 && to > from, name);
  return app.slice(from, to + 5);
}

function harness(autoOpen = true, engine = 'codex') {
  const store = new Map(autoOpen ? [] : [['ccc-auto-open-new-chats', 'off']]);
  const panes = { p1: { id: 'p1', conversationId: '__new__' }, p2: { id: 'p2', conversationId: 'other-chat' } };
  const selections = [], held = [], choices = [], failures = [], streams = [], drafts = new Map();
  const input = { value: 'First task', focus() { this.focused = true; }, style: {}, dispatchEvent() {} };
  const button = { disabled: false };
  let now = 1000000;
  const ctx = vm.createContext({
    Date: { now: () => ++now }, Map, Set, Number, console: { error() {} },
    document: { hidden: false, getElementById: () => null },
    window: { CCCProjectContext: { prepareLaunch: async () => {} } },
    localStorage: { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) },
    activePane: 'p1', activePaneId: () => ctx.activePane, paneByPaneId: id => panes[id],
    currentConversation: '__new__', conversationsData: [], pendingSpawns: new Map(), columnOverrides: {},
    splitState: { panes: [panes.p1, panes.p2], activeIndex: 0 },
    $convSearch: { value: '' }, $convInput: input, $convSendBtn: button,
    $convInputModelSelect: { value: 'model-one', style: { display: '' } },
    $convInputEffortSelect: { value: 'high', style: { display: '' } },
    composerInputForPane: () => input,
    spawnHandoffDraftRequest: 1, spawnEffortPickedByUser: false, spawnEffortChoiceDirty: false,
    spawnFastMode: null, f2DraftContinuation: null, spawnInTerminalChecked: () => false,
    getSpawnEngine: () => engine, engineSupportsEffort: () => true,
    spawnFirstSentence: text => text, createMissionFolderIndex: () => ({ folders: new Map() }),
    findActiveFlowObject: () => null, createSpawnGroupParent: () => 'object:task',
    flowNodeParents: {}, flowNodeKey: (kind, id) => kind + ':' + id, persistFlowNodeParents() {},
    spawnProjectState: { loaded: true }, spawnModeState: { loaded: true },
    spawnMcpState: { loaded: true }, spawnSubagentState: { loaded: true }, spawnHandoffState: { loaded: true },
    spawnSetupState: {}, getSpawnSkills: () => [], validateSpawnAgentSetup: () => '', spawnHarnessSettings: () => ({}),
    SPAWN_PROJECT_KEY: 'project', SPAWN_CWD_KEY: 'cwd', SPAWN_PERMISSION_OPTIONS: {},
    getSpawnProjectMemory: () => '', getSpawnMode: () => '', getSpawnMcps: () => [],
    getSpawnHandoff: () => ({ sessionId: '' }), getSpawnProvider: () => '',
    getSpawnSubagent: () => '', getSpawnSubagentMode: () => '',
    getSpawnCwd: () => '/projects/example', popoutRepoPath: () => '/projects/example',
    findSpawnCwdRepo: () => ({ path: '/projects/example' }), settleRepoGuessBeforeSend: async () => {},
    repoGuessAuto: null, repoGuessUserPicked: false, spawnCwdIsMissing: async () => false,
    resetRepoGuess() {}, requestClaudePrewarm: async () => null,
    _claudePrewarmKey: '', _claudePrewarmPromise: null, abortBackgroundApiReadsForSpawn() {},
    spawnSourceForEngine: eng => eng === 'claude' ? 'interactive' : eng,
    isSpawnLogPlaceholderSource: source => source !== 'interactive',
    spawnUsesLogPlaceholder: eng => eng !== 'claude',
    revealSessionInSidebar() {}, savePendingSpawnReceipt() {}, renderSidebar() {}, filterConversations: () => [],
    _watchPendingSpawnRegistration() {}, setTimeout() {},
    selectConversation: (id, paneId = ctx.activePane) => {
      selections.push({ id, paneId }); ctx.currentConversation = id; panes[paneId].conversationId = id;
    },
    clearInputDraftForConversation: id => drafts.delete(id),
    inputDraftKeyForConversation: id => id, setInputDraftForKey: (key, text) => drafts.set(key, text),
    spawnEndpointForEngine: () => '/api/sessions/spawn', durableActionId: () => 'spawn:test-' + now,
    buildSpawnBody: options => ({ engine: options.engine, prompt: options.prompt, cwd: options.cwd,
      model: options.model, reasoning_effort: options.effort }),
    fetch: (_url, options) => new Promise(resolve => held.push({
      body: JSON.parse(options.body), reply: data => resolve({ ok: data.ok !== false, status: data.ok === false ? 503 : 200,
        json: async () => data }),
    })),
    adoptPendingSpawnPid: (id, realId, _log, sid) => {
      const card = ctx.pendingSpawns.get(id); if (!card) return null;
      ctx.pendingSpawns.delete(id); ctx.pendingSpawns.set(realId, card);
      card.expected_session_id = sid; return card;
    },
    _failPendingSpawnCard: (id, error) => { failures.push({ id, error }); ctx.pendingSpawns.get(id).spawn_failed = true; },
    releaseClaudeSpawnPaintGate() {}, assignSpawnedSessionToDefaultObject() {}, recordSpawnModeAdviceOutcome() {},
    clearSpawnHandoffSelection() {}, recordSpawnChoice: (...choice) => choices.push(choice),
    spawnStatsBegin() {}, spawnStatsMark() {}, claudeSpawnAwaitingFirstPaint: new Set(),
    stopConvStream: paneId => streams.push({ kind: 'stop', paneId }),
    startSpawnStream: (_id, paneId) => streams.push({ kind: 'start', paneId }),
    showOpToast() {}, showErrorWithFix() {}, flashRed() {}, refreshConversationList() {}, chasePendingSpawn() {},
  });
  const start = app.indexOf('  let newSessionComposerRevision = 0;');
  const end = app.indexOf('  function insertPendingSpawnCard(', start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(app.slice(start, end), ctx);
  ctx.enterNewSessionMode = (initialPrompt = '') => {
    vm.runInContext('++newSessionComposerRevision', ctx);
    ++ctx.spawnHandoffDraftRequest;
    ctx.currentConversation = '__new__'; panes[ctx.activePane].conversationId = '__new__';
    input.value = initialPrompt; button.disabled = false;
  };
  vm.runInContext(fn('insertPendingSpawnCard'), ctx);
  const local = app.includes('  async function dispatchSpawnFromInlineInput(');
  vm.runInContext(fn(local ? 'dispatchSpawnFromInlineInput' : 'spawnFromInlineInput', true), ctx);
  const launch = text => ctx[local ? 'dispatchSpawnFromInlineInput' : 'spawnFromInlineInput'](text);
  return { ctx, store, panes, input, button, selections, held, choices, failures, streams, drafts, launch };
}

async function waitForPost(h, count = 1) {
  for (let i = 0; i < 20 && h.held.length < count; i++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.held.length, count, 'launch should send its request');
}
const accepted = id => ({ ok: true, spawn_id: 'pid-' + id, session_id: id });

test('automatic opening is enabled for existing browsers and tolerates unavailable storage', () => {
  const h = harness();
  assert.equal(h.ctx.getAutoOpenNewChatsPref(), true);
  h.ctx.localStorage.getItem = () => { throw new Error('storage unavailable'); };
  assert.equal(h.ctx.getAutoOpenNewChatsPref(), true);
});

test('a delayed accepted launch cannot replace a newer new-chat draft', async () => {
  const h = harness();
  const launch = h.launch('First task'); await waitForPost(h);
  assert.match(h.ctx.currentConversation, /^spawning-/);
  h.ctx.enterNewSessionMode(); h.input.value = 'Second draft';
  h.held[0].reply(accepted('first-chat')); await launch;
  assert.equal(h.ctx.currentConversation, '__new__');
  assert.equal(h.input.value, 'Second draft');
  assert.equal(h.selections.length, 1, 'only the immediate placeholder was selected');
});

test('a delayed accepted launch cannot replace another open chat', async () => {
  const h = harness();
  const launch = h.launch('First task'); await waitForPost(h);
  h.ctx.selectConversation('other-chat'); h.input.value = 'Reply to the other chat';
  h.held[0].reply(accepted('first-chat')); await launch;
  assert.equal(h.ctx.currentConversation, 'other-chat');
  assert.equal(h.input.value, 'Reply to the other chat');
  assert.equal(h.selections.length, 2);
});

test('background launches allow a second launch before either response arrives', async () => {
  const h = harness(false);
  const first = h.launch('First task'); await waitForPost(h);
  assert.equal(h.ctx.currentConversation, '__new__');
  assert.equal(h.button.disabled, false);
  h.input.value = 'Second task'; h.ctx.$convInputModelSelect.value = 'model-two';
  const second = h.launch('Second task'); await waitForPost(h, 2);
  h.input.value = 'Third draft';
  h.held[1].reply(accepted('second-chat')); await second;
  h.held[0].reply(accepted('first-chat')); await first;
  assert.equal(h.ctx.currentConversation, '__new__');
  assert.equal(h.input.value, 'Third draft');
  assert.deepEqual(h.selections, []);
  assert.deepEqual(h.held.map(request => [request.body.prompt, request.body.model]),
    [['First task', 'model-one'], ['Second task', 'model-two']]);
});

test('a late failure keeps its exact retry request and preserves a newer draft', async () => {
  const h = harness(false);
  const launch = h.launch('First task'); await waitForPost(h);
  h.input.value = 'Second draft'; h.drafts.set('__new__', 'Second draft');
  h.held[0].reply({ ok: false, error: 'Temporarily unavailable' }); await launch;
  assert.equal(h.ctx.currentConversation, '__new__');
  assert.equal(h.input.value, 'Second draft');
  assert.equal(h.drafts.get('__new__'), 'Second draft');
  assert.equal(h.failures.length, 1);
  const card = [...h.ctx.pendingSpawns.values()][0];
  assert.equal(card.spawn_body.prompt, 'First task');
  assert.equal(card.spawn_failed, true);
});

test('Claude completion cannot stop or replace the stream in another split pane', async () => {
  const h = harness(true, 'claude');
  const launch = h.launch('First task'); await waitForPost(h);
  h.ctx.activePane = 'p2'; h.ctx.currentConversation = 'other-chat';
  h.held[0].reply(accepted('first-chat')); await launch;
  assert.equal(h.ctx.currentConversation, 'other-chat');
  assert.deepEqual(h.streams, []);
});

test('a still-selected placeholder refreshes normally when its launch is accepted', async () => {
  const h = harness();
  const launch = h.launch('First task'); await waitForPost(h);
  h.ctx.$convInputEffortSelect.value = 'low';
  h.held[0].reply(accepted('first-chat')); await launch;
  assert.equal(h.selections.length, 2);
  assert.match(h.ctx.currentConversation, /^spawning-/);
  assert.equal(h.choices[0][2], 'high', 'history uses the submitted effort, not a later picker value');
});

test('external launches stay unselected with automatic opening enabled', () => {
  const h = harness();
  h.ctx.insertPendingSpawnCard('external', 'External task', 'codex', null, { no_auto_select: true });
  assert.equal(h.ctx.currentConversation, '__new__');
  assert.deepEqual(h.selections, []);
});

test('refreshing a selected canonical chat cannot reopen its older placeholder', () => {
  const h = harness();
  const card = { id: 'spawning-old', expected_session_id: 'canonical-chat', spawn_pane_id: 'p1' };
  h.ctx.selectConversation('canonical-chat');
  h.ctx.refreshSelectedPendingSpawn(card);
  assert.equal(h.ctx.currentConversation, 'canonical-chat');
});

test('typing a newer draft during launch preparation keeps that text', async () => {
  const h = harness(false);
  let finishPreparation;
  h.ctx.window.CCCProjectContext.prepareLaunch = () => new Promise(resolve => { finishPreparation = resolve; });
  const launch = h.launch('First task');
  for (let i = 0; i < 20 && !finishPreparation; i++) await new Promise(resolve => setImmediate(resolve));
  if (finishPreparation) {
    h.input.value = 'Typed during preparation'; finishPreparation();
    await waitForPost(h);
    assert.equal(h.input.value, 'Typed during preparation');
  } else {
    // Upstream has no separate project-settings preparation stage.
    await waitForPost(h); h.input.value = 'Typed during preparation';
  }
  h.held[0].reply(accepted('first-chat')); await launch;
  assert.equal(h.input.value, 'Typed during preparation');
});
