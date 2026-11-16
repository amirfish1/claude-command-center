const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./legacy-dashboard-source.cjs');

test('choosing Codex loads account options without waiting for background startup reads', async () => {
  const start = source.indexOf('  function syncCodexAccountUi(');
  const end = source.indexOf('\n  function buildSpawnBody(', start);
  assert.ok(start >= 0 && end > start);
  const select = {
    dataset: {}, value: '', options: [],
    replaceChildren(...options) { this.options = options; },
    add(option) { this.options.push(option); },
  };
  const nodes = {codexAccountRow: {}, codexAccountSelect: select, codexSessionAccount: {}};
  const requests = [];
  const ctx = vm.createContext({
    currentConversation: '__new__', currentSession: null, getSpawnEngine: () => 'codex',
    document: {getElementById: id => nodes[id]},
    spawnDefaultsState: {codex_account_id: 'secondary-test'},
    _codexAccountSession: '', _codexAccountsLoading: false, _codexAccountsLoadedAt: 0,
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    fetch: (url, init) => {
      requests.push({url, init});
      // A parked startup read never resolves until startup releases it.
      if (init?.cccPriority !== 'user') return new Promise(() => {});
      return Promise.resolve({json: async () => ({ok: true, accounts: [
        {current: true, id: '__current__'}, {id: 'secondary-test', label: 'Test secondary'},
      ]})});
    },
  });
  vm.runInContext(source.slice(start, end), ctx);
  ctx.syncCodexAccountUi();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests[0].url, '/api/codex/accounts');
  assert.equal(requests[0].init.cccPriority, 'user');
  assert.equal(select.options.length, 2);
  assert.equal(select.value, 'secondary-test');
  assert.equal(ctx._codexAccountsLoading, false);
  ctx.getSpawnEngine = () => 'claude';
  ctx.syncCodexAccountUi();
  assert.equal(nodes.codexAccountRow.hidden, true);
  assert.equal(requests.length, 1);
});
