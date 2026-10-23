const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = app.indexOf('  async function _setOpenSessionLifecycle(');
const code = app.slice(start, app.indexOf('  // Show only the lifecycle/queue items', start));

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
  assert.deepEqual(f.calls, []);
  resolve({ ok: true, archived: true });
  await done;
  assert.equal(f.row.archived, true);
  assert.equal(f.calls[0], 'list');
});

test('archive failure preserves the open conversation', async () => {
  const f = fixture({ ok: false, error: 'Network failed' });
  await f.run('archive');
  assert.equal(f.context.currentConversation, 'conversation-test');
  assert.equal(f.calls.includes('list'), false);
  assert.match(f.calls[0], /Archive failed/);
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
