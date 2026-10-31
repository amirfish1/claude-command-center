const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = app.indexOf('  async function _setOpenSessionLifecycle(');
const code = app.slice(start, app.indexOf('  // Show only the lifecycle/queue items', start));

test('a confirming local render cannot consume archive protection before a delayed poll', () => {
  const begin = app.indexOf('  const _optimisticOverrides = new Map()');
  const fn = app.indexOf('  function applyOptimisticOverrides(', begin);
  const end = app.indexOf('\n  }\n', fn) + 4;
  let now = 1000;
  const context = { Date: { now: () => now } };
  vm.createContext(context);
  vm.runInContext(app.slice(begin, end), context);
  const intent = { archived: true, explicitly_archived: true };
  context.setOptimisticOverride('session-test', intent);
  context.applyOptimisticOverrides([{ session_id: 'session-test', ...intent }]);
  const stale = { session_id: 'session-test', archived: false, explicitly_archived: false };
  context.applyOptimisticOverrides([stale]);
  assert.equal(stale.archived, true);
  assert.equal(stale.explicitly_archived, true);
  context.setOptimisticOverride('session-test', { archived: false, explicitly_archived: false });
  let olderArchive = { session_id: 'session-test', ...intent };
  context.applyOptimisticOverrides([olderArchive]);
  assert.equal(olderArchive.archived, false, 'a new Restore supersedes the old Archive');
  now += 31000;
  context.applyOptimisticOverrides([olderArchive = { session_id: 'session-test', ...intent }]);
  assert.equal(olderArchive.archived, true, 'expired protection yields to the server');
});

function fixture(response) {
  const row = { id: 'conversation-test', session_id: 'session-test' };
  const calls = [];
  const context = {
    currentConversation: row.id,
    _openConversationRow: () => row,
    rowRepoPath: () => '',
    archivePayloadForRow: (_, session_id, archived) => ({ session_id, archived }),
    ccPostJson: async () => response,
    conversationsData: [row], archiveData: [], currentRepoBacklogData: [],
    setOptimisticOverride: () => {},
    returnToConversationList: () => calls.push('list'),
    updateInputBar: () => {}, renderSidebar: () => {}, filterConversations: () => [],
    $convSearch: { value: '' }, showOpToast: text => calls.push(text),
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return { context, calls, row, run: kind => context._setOpenSessionLifecycle(kind) };
}

test('archive returns to the list only after successful persistence', async () => {
  let resolve;
  const pending = new Promise(r => { resolve = r; });
  const f = fixture(pending);
  const done = f.run('archive');
  // af8451a1: the row moves optimistically with a progress toast, but the
  // reader stays open until the archive is persisted.
  assert.deepEqual(f.calls, ['Archiving…']);
  resolve({ ok: true, archived: true });
  await done;
  assert.equal(f.row.archived, true);
  assert.equal(f.calls[1], 'list');
});

test('archive failure preserves the open conversation', async () => {
  const f = fixture({ ok: false, error: 'Network failed' });
  await f.run('archive');
  assert.equal(f.context.currentConversation, 'conversation-test');
  assert.equal(f.calls.includes('list'), false);
  assert.equal(f.row.archived, false, 'the optimistic archive is rolled back');
  assert.match(f.calls.at(-1), /Archive failed/);
});

test('a delayed archive does not navigate away from another conversation', async () => {
  let resolve;
  const f = fixture(new Promise(r => { resolve = r; }));
  const done = f.run('archive');
  f.context.currentConversation = 'another-conversation';
  resolve({ ok: true, archived: true });
  await done;
  assert.equal(f.row.archived, true);
  assert.equal(f.calls.includes('list'), false);
});

test('restoring a conversation keeps its reader open', async () => {
  const f = fixture({ ok: true, archived: false });
  await f.run('unarchive');
  assert.equal(f.calls.includes('list'), false);
  assert.equal(f.row.archived, false);
});


test('archive and restore patch every returned group member but leave other ideas alone', async () => {
  for (const archived of [true, false]) {
    const f = fixture({ ok: true, archived, session_ids: ['session-test', 'child', 'continued'] });
    const child = { id: 'child', archived: !archived };
    const continued = { id: 'continued', archived: !archived };
    const other = { id: 'unrelated', archived: !archived };
    f.context.conversationsData.push(child, other);
    f.context.archiveData.push(continued);
    await f.run(archived ? 'archive' : 'unarchive');
    assert.equal(f.row.archived, archived);
    assert.equal(child.archived, archived);
    assert.equal(continued.archived, archived);
    assert.equal(other.archived, !archived);
  }
});

const closeStart = app.indexOf('  async function closeReadyConversation(');
const closeCode = app.slice(closeStart, app.indexOf("  document.addEventListener('click'", closeStart));
const readinessStart = app.indexOf('  function sessionReadyToClose(');
const readinessCode = app.slice(readinessStart, app.indexOf('  // Completion and explicit archiving', readinessStart));

function closeFixture() {
  const row = { id: 'ready-session', state: 'idle', session_state: {
    next_step_user: 'None.', completion: { version: 1, valid: true, ready_to_close: true },
  } };
  const card = {};
  const pane = { dataset: { paneId: 'p2' }, querySelectorAll: () => [card] };
  const calls = [];
  const button = { disabled: false, dataset: { ssbAct: 'close' },
    closest: selector => selector === '.conv-pane' ? pane : card };
  const context = { _openConversationRow: () => row, _applyLiveOverlayToRow: r => r,
    sessionIsOptimisticallySending: () => false, setActivePaneById: id => calls.push(id),
    _setOpenSessionLifecycle: async kind => calls.push(kind) };
  vm.createContext(context);
  vm.runInContext(readinessCode + closeCode, context);
  return { row, pane, card, button, calls, context, close: () => context.closeReadyConversation(button) };
}

test('transcript Close targets its own pane and archives only on a deliberate click', async () => {
  const f = closeFixture();
  assert.deepEqual(f.calls, []);
  await f.close();
  assert.deepEqual(f.calls, ['p2', 'archive']);
  assert.equal(f.button.disabled, false);
});

test('a rendered Close cannot archive after new work, send, or a newer card', async () => {
  for (const invalidate of [
    f => { f.row.state = 'working'; },
    f => { f.row.last_event_type = 'user'; },
    f => { f.row.archived = true; },
    f => { f.row.session_state.completion.ready_to_close = false; },
    f => { f.context.sessionIsOptimisticallySending = () => true; },
    f => { f.pane.querySelectorAll = () => [f.card, {}]; },
  ]) {
    const f = closeFixture();
    invalidate(f);
    await f.close();
    assert.equal(f.calls.includes('archive'), false);
  }
});
