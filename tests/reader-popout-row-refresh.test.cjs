// CCC-728: a reader-only popout (an app's agent pane) refreshes only its own
// row after a send instead of loading the archive controller and every row.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(__dirname, '..', 'static', name), 'utf8');
const core = read('conversation-core.js');
const host = read('conversation-host.js');
const app = read('app.js');

function fn(source, name, indent = 2) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name);
  const end = source.indexOf('\n' + ' '.repeat(indent) + '}', start) + indent + 2;
  return (source.slice(start - 6, start) === 'async ' ? 'async ' : '') + source.slice(start, end);
}

function coreHarness(state, respond) {
  const requests = [];
  const ctx = {
    sharedState: state, encodeURIComponent,
    fetch: async (url, init) => { requests.push({ url, init }); return respond(url); },
  };
  vm.createContext(ctx);
  vm.runInContext(fn(core, 'readerPopoutRowRefreshApplies') + '\n'
    + fn(core, 'refreshReaderPopoutRow'), ctx);
  return { ctx, requests };
}

const ok = body => ({ ok: true, json: async () => body });

test('only a reader-only popout on a settled conversation takes the single-row path', () => {
  const cases = [
    [{ READER_ONLY_POPOUT: true, currentConversation: 'abc', pendingSpawns: new Map() }, true],
    [{ READER_ONLY_POPOUT: false, currentConversation: 'abc', pendingSpawns: new Map() }, false],
    [{ READER_ONLY_POPOUT: true, currentConversation: '__new__', pendingSpawns: new Map() }, false],
    [{ READER_ONLY_POPOUT: true, currentConversation: 'spawning-41', pendingSpawns: new Map() }, false],
    [{ READER_ONLY_POPOUT: true, currentConversation: 'abc', pendingSpawns: new Map([[41, {}]]) }, false],
    [{ READER_ONLY_POPOUT: true, currentConversation: '', pendingSpawns: new Map() }, false],
  ];
  for (const [state, expected] of cases) {
    const { ctx } = coreHarness(state, () => ok({}));
    assert.equal(ctx.readerPopoutRowRefreshApplies(), expected, JSON.stringify(state));
  }
});

test('the open row is read alone and merged into the synthetic popout row', async () => {
  const synthetic = { id: 'conv', session_id: 'sid-1', display_name: 'From URL' };
  const state = { READER_ONLY_POPOUT: true, currentConversation: 'conv',
    sessionIdByConv: { conv: 'sid-1' }, conversationsData: [synthetic] };
  const { ctx, requests } = coreHarness(state, () => ok({ ok: true, conversation: {
    id: 'sid-1', session_id: 'sid-1', goal: 'Ship CCC-728', goal_status: 'active',
    model: 'claude-sonnet-5-5', is_live: true } }));
  const row = await ctx.refreshReaderPopoutRow();
  assert.deepEqual(requests.map(r => r.url), ['/api/conversations/sid-1/row']);
  assert.equal(requests[0].init.cache, 'no-store');
  assert.equal(row, synthetic, 'updates the row the pane already renders from');
  assert.equal(state.conversationsData.length, 1);
  assert.equal(synthetic.id, 'conv', 'keeps the pane conversation id');
  assert.equal(synthetic.goal, 'Ship CCC-728');
  assert.equal(synthetic.model, 'claude-sonnet-5-5');
  assert.equal(synthetic.is_live, true);
});

test('after a clear or Codex continuation the successor row is added for the new id', async () => {
  const old = { id: 'old-sid', session_id: 'old-sid' };
  const state = { READER_ONLY_POPOUT: true, currentConversation: 'new-sid',
    sessionIdByConv: { 'old-sid': 'old-sid', 'new-sid': 'new-sid' }, conversationsData: [old] };
  const { ctx, requests } = coreHarness(state, () => ok({ ok: true, conversation: {
    id: 'new-sid', session_id: 'new-sid', goal: '', model: 'gpt-6.1' } }));
  const row = await ctx.refreshReaderPopoutRow();
  assert.deepEqual(requests.map(r => r.url), ['/api/conversations/new-sid/row']);
  assert.equal(state.conversationsData.length, 2);
  assert.equal(row.id, 'new-sid');
  assert.equal(state.conversationsData.find(r => r.id === 'new-sid'), row);
  assert.equal(old.model, undefined, 'the predecessor row is untouched');
});

test('missing rows, failures and a pane that moved on change nothing', async () => {
  for (const respond of [
    () => ok({ ok: true, conversation: null }),
    () => ({ ok: false, json: async () => ({}) }),
    () => { throw new Error('offline'); },
  ]) {
    const synthetic = { id: 'conv' };
    const state = { READER_ONLY_POPOUT: true, currentConversation: 'conv', conversationsData: [synthetic] };
    const { ctx } = coreHarness(state, respond);
    assert.equal(await ctx.refreshReaderPopoutRow(), null);
    assert.deepEqual(state.conversationsData, [{ id: 'conv' }]);
  }
  const state = { READER_ONLY_POPOUT: true, currentConversation: 'conv', conversationsData: [{ id: 'conv' }] };
  const { ctx } = coreHarness(state, () => {
    state.currentConversation = 'other';
    return ok({ ok: true, conversation: { id: 'conv', goal: 'stale' } });
  });
  assert.equal(await ctx.refreshReaderPopoutRow(), null);
  assert.equal(state.conversationsData[0].goal, undefined);
});

test('the standalone host never loads the archive or list for a reader-only popout', async () => {
  const start = host.indexOf('async refreshConversationList() {');
  const end = host.indexOf('\n      },', start);
  const method = host.slice(start, end + '\n      }'.length);
  const calls = [];
  const state = {
    popoutRepoPath: () => '/repo',
    refreshArchiveData: async () => calls.push('archive'),
    loadConversationList: async () => calls.push('list'),
    renderSidebar: () => calls.push('render'),
    refreshReaderPopoutRow: async () => { calls.push('row'); return state.rowResult; },
  };
  const hostServices = vm.runInNewContext('({ state, ' + method + ' })', { state });
  state.readerPopoutRowRefreshApplies = () => true;
  state.rowResult = { id: 'conv' };
  await hostServices.refreshConversationList();
  assert.deepEqual(calls, ['row', 'render']);
  calls.length = 0;
  state.rowResult = null;
  await hostServices.refreshConversationList();
  assert.deepEqual(calls, ['row'], 'nothing to repaint without a row');
  calls.length = 0;
  state.readerPopoutRowRefreshApplies = () => false;
  await hostServices.refreshConversationList();
  assert.deepEqual(calls, ['archive', 'list', 'render'], 'the dashboard keeps its list refresh');
});

test('the dashboard script gates its list refresh the same way', () => {
  const body = fn(app, 'refreshConversationList');
  const gate = body.indexOf('readerPopoutRowRefreshApplies()');
  assert.ok(gate >= 0);
  assert.ok(gate < body.indexOf('refreshArchiveData('));
  assert.match(body.slice(gate, body.indexOf('refreshArchiveData(')), /refreshReaderPopoutRow\(\)[\s\S]*return;/);
});
