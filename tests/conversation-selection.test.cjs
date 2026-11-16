const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require.resolve('../static/conversation-selection.js'), 'utf8');
function harness(previous = 'old-session') {
  const calls = [];
  const classList = { remove() {}, toggle() {} };
  const view = { dataset: {}, classList, parentNode: { querySelector: () => null } };
  const pane = { conversationId: previous };
  const state = {
    sessionIdByConv: {}, sessionSourceByConv: {}, conversationsData: [], archiveData: [],
    sessionCwdByConv: {}, sessionCwdExistsByConv: {}, sessionSpawnPidByConv: {},
    conversationPaneLoadToken: 0, CONV_POPOUT_MODE: true, _convReplayPaneIds: [],
  };
  const implementations = {
    activePaneId: () => 'p1', paneByPaneId: () => pane,
    getConvView: () => view, getConvViewForPane: () => view,
    resolvedSpawnConversationId: id => id,
    getConvInputBarForPane: () => null,
    setActivePaneById: (paneId, id) => { pane.conversationId = id; },
    rowRepoPath: () => '', popoutRepoPath: () => '',
    cleanIssuePrompt: value => value,
  };
  const adapter = new Proxy({}, { get(_, name) {
    return (...args) => {
      calls.push([name, ...args]);
      return implementations[name]?.(...args);
    };
  } });
  const context = { window: { dispatchEvent() {} },
    document: { body: { classList }, querySelector: () => null, getElementById: () => null },
    CustomEvent: class {}, Event: class {}, performance: { now: () => 10 },
    clearTimeout, setTimeout, setInterval,
  };
  vm.runInNewContext(source, context);
  const controller = context.window.CCCConversationSelection.createController({ state,
    panes: adapter, transport: adapter, composer: adapter, navigation: adapter,
    chrome: adapter, replay: adapter, pkood: adapter });
  return { controller, state, calls, pane, implementations };
}

test('reselecting preserves a restored draft; leaving a session remembers it', async () => {
  const h = harness('same-session');
  await h.controller.selectConversation('same-session');
  assert.equal(h.calls.filter(c => c[0] === 'rememberComposerDraftForPane').length, 0);
  assert(h.calls.some(c => c[0] === 'restoreComposerDraftForPane' && c[2] === 'same-session'));
  await h.controller.selectConversation('new-session');
  assert.equal(h.calls.filter(c => c[0] === 'rememberComposerDraftForPane').length, 1);
  assert.equal(h.state.currentConversation, 'new-session');
});

test('superseded reads cannot reopen streams or clear the newer load state', async () => {
  const h = harness();
  let releaseFirst;
  h.implementations.fetchConversationEvents = () => new Promise(resolve => { releaseFirst = resolve; });
  const first = h.controller.selectConversation('first-session');
  h.implementations.fetchConversationEvents = async () => {};
  await h.controller.selectConversation('new-session');
  const streamCount = h.calls.filter(c => c[0] === 'startConvStream').length;
  releaseFirst();
  await first;
  assert.equal(h.calls.filter(c => c[0] === 'startConvStream').length, streamCount);
  assert.equal(h.state.currentConversation, 'new-session');
  assert.equal(h.state.conversationPaneLoading, false);
});

test('raw session ids populate the real composer context before the tail resolves', async () => {
  const h = harness();
  const sid = '00000000-0000-4000-8000-000000000001';
  let rejectRead;
  h.implementations.fetchConversationEvents = () => new Promise((_, reject) => { rejectRead = reject; });
  const loading = h.controller.selectConversation(sid);
  assert(h.calls.some(c => c[0] === 'setCurrentSession' && c[2] === sid));
  assert(h.calls.some(c => c[0] === 'updateInputBar'));
  assert.equal(h.state.conversationPaneLoading, true);
  rejectRead(new Error('Read unavailable'));
  await assert.rejects(loading, /Read unavailable/);
  assert.equal(h.state.conversationPaneLoading, false);
  assert.equal(h.calls.filter(c => c[0] === 'startConvStream').length, 0);
});
