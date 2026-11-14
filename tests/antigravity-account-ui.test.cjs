const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');

function load(names, context) {
  vm.createContext(context);
  for (const name of names) {
    const start = source.indexOf('  function ' + name + '(');
    assert.ok(start >= 0, name);
    const end = source.indexOf('\n  function ', start + 1);
    vm.runInContext(source.slice(start, end), context);
  }
  return context;
}

test('every spawn surface sends the selected Antigravity account without leaking it to other engines', () => {
  const select = {value: 'secondary'};
  const ctx = load(['buildSpawnBody'], {
    document: {getElementById: id => id === 'antigravityAccountSelect' ? select : null},
    spawnDefaultsState: {antigravity_account_id: 'primary'}, window: {},
    engineSupportsEffort: () => false, spawnSupportsWorktree: () => true,
  });
  assert.equal(ctx.buildSpawnBody({engine: 'antigravity', prompt: 'test'}).antigravity_account_id, 'secondary');
  select.value = '';
  ctx.spawnDefaultsState.antigravity_account_id = 'secondary';
  assert.equal(ctx.buildSpawnBody({engine: 'antigravity'}).antigravity_account_id, 'secondary');
  assert.equal('antigravity_account_id' in ctx.buildSpawnBody({engine: 'claude'}), false);
});

test('account labels and both settings engine pickers distinguish Primary from Secondary', () => {
  const ctx = load(['spawnEngineLabel', 'spawnEngineLabelWithAccount', 'aoAntigravityAccount', 'aoEnginePick'], {
    spawnDefaultsState: {antigravity_account_id: 'secondary', worker_antigravity_account_id: 'primary'},
    document: {getElementById: () => null},
    ENGINE_HUB_WORKER_ENGINES: ['antigravity'], SPAWN_DEFAULT_ENGINES: ['antigravity'],
    ENGINE_HUB_META: {antigravity: {}}, _engHubData: true,
    _engHubRows: () => [{engine: 'antigravity', state: 'ready'}],
    aoCodexAccounts: () => new Set(), aoEngineLabel: () => 'Antigravity', escapeHtml: v => v,
  });
  assert.equal(ctx.spawnEngineLabelWithAccount('antigravity'), 'Antigravity (Secondary)');
  assert.equal(ctx.spawnEngineLabelWithAccount('antigravity', 'primary'), 'Antigravity (Primary)');
  assert.match(ctx.aoEnginePick('session', 'antigravity'), /value="antigravity\|secondary" selected/);
  assert.match(ctx.aoEnginePick('worker', 'antigravity'), /value="antigravity\|primary" selected/);
  ctx.spawnDefaultsState.worker_inherit_session = true;
  assert.match(ctx.aoEnginePick('worker', 'antigravity'), /value="antigravity\|secondary" selected/);
});

test('changing account for independent workers leaves session defaults alone', () => {
  const writes = [];
  const ctx = load(['aoSetAntigravityAccount'], {
    spawnDefaultsState: {models: {antigravity: 'model'}},
    aoWorkerEngine: () => 'antigravity', aoSaveDefaults: body => writes.push(body),
    document: {getElementById: () => null},
  });
  ctx.aoSetAntigravityAccount('secondary', 'worker');
  assert.equal(writes[0].worker_antigravity_account_id, 'secondary');
  assert.equal(writes[0].worker_inherit_session, false);
  assert.equal('antigravity_account_id' in writes[0], false);
});

test('a late account response cannot relabel another conversation', async () => {
  let resolve;
  const nodes = {
    antigravityAccountRow: {}, antigravityAccountSelect: {dataset: {}}, antigravitySessionAccount: {},
  };
  const ctx = load(['syncAntigravityAccountUi'], {
    document: {getElementById: id => nodes[id]},
    spawnDefaultsState: {antigravity_account_id: 'secondary'},
    currentConversation: 'session-a', currentSession: {source: 'antigravity'},
    getSpawnEngine: () => 'antigravity', _antigravityAccountSession: '',
    fetch: () => new Promise(r => {resolve = r;}),
  });
  ctx.syncAntigravityAccountUi();
  ctx.currentConversation = '__new__';
  ctx.syncAntigravityAccountUi();
  resolve({ok: true, json: async () => ({ok: true, session_account: 'Primary'})});
  await new Promise(r => setImmediate(r));
  assert.equal(nodes.antigravitySessionAccount.hidden, true);
  assert.notEqual(nodes.antigravitySessionAccount.textContent, 'Account: Primary');
  assert.equal(nodes.antigravityAccountSelect.value, 'secondary');
});
