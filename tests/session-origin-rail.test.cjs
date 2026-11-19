const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/conversation-core.js'), 'utf8');
const start = source.indexOf('  function _renderRailSpawnedVia(');
const end = source.indexOf('  async function launchTerminal(', start);
function render(row) {
  const el = { removeAttribute() {} };
  const context = vm.createContext({document: {getElementById: () => el}, window: {},
    sharedState: {RAIL_SPAWNED_VIA_LABELS: {
      tokeneconomizer: ['TokenEconomizer', 'Inferred helper directory'],
      unknown: ['Unknown', 'No launch origin recorded'],
      subagent: ['Subagent', 'Recorded parent'],
    }}, _isWatchTowerWorkerRow: () => false});
  vm.runInContext(source.slice(start, end), context);
  context._renderRailSpawnedVia(row);
  return el;
}
test('missing provenance never renders as Terminal', () => {
  for (const row of [{}, {spawned_via: 'unknown'}, {spawned_via: 'not available'}]) {
    assert.equal(render(row).textContent, 'via: Unknown');
  }
});
test('helper, caller and parent attribution remain visible', () => {
  assert.equal(render({spawned_via: 'tokeneconomizer'}).textContent, 'via: TokenEconomizer');
  assert.equal(render({spawned_via: 'unknown', spawn_caller: 'example.py'}).textContent, 'via: example.py');
  assert.equal(render({parent_session_id: 'parent'}).textContent, 'via: Subagent');
  assert.equal(render(null).hidden, true);
});
