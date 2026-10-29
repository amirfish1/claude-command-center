// CCC-182: the new-session model picker shows each model's cost.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const grab = (start, end) => { const i = src.indexOf(start); return src.slice(i, src.indexOf(end, i)); };
const code = grab('  function _normalizeModelId', '  function _knownModelForEngine')
  + grab('  function sessionCostTier', '  function sessionIsActivelyWorking')
  + grab('  const _EFFORT_SUFFIX_RE', '  function modelOptionsForSpawnEngine');

const MODEL_OPTIONS_BY_ENGINE = {
  devin: [
    { id: 'adaptive', label: 'Adaptive (default)' },
    { id: 'swe-2', label: 'SWE-2' },
    { id: 'glm-5.2', label: 'GLM-5.2' },
    { id: 'swe-2-high', label: 'SWE-2 High', cost_tier: 'Free' },
    { id: 'swe-2-medium', label: 'SWE-2 Medium', cost_tier: 'Free' },
    { id: 'swe-2-high-lightning', label: 'SWE-2 High Lightning', cost_tier: 'High cost' },
    { id: 'glm-5-2-max', label: 'GLM-5.2 Max', cost_tier: 'Low cost', cost_summary: '$1.4 / 1M Input' },
  ],
  claude: [
    { id: 'opus-5-5', label: 'Claude Opus 5.5', cost_tier: '24', cost_summary: '$4.00 in / 1M, $20.00 out / 1M' },
  ],
  kimi: [{ id: 'kimi-code/k3', label: 'K3' }],
};
// eslint-disable-next-line no-eval
const api = eval(code + '; ({ spawnModelCostTier, spawnModelCostSummary })');

test('Devin variants show their CLI cost tier', () => {
  assert.strictEqual(api.spawnModelCostTier('devin', 'swe-2-high'), 'Free');
  assert.strictEqual(api.spawnModelCostSummary('devin', 'glm-5-2-max'), '$1.4 / 1M Input');
});

test('Devin family rows borrow their effort variants tier, ignoring lightning', () => {
  assert.strictEqual(api.spawnModelCostTier('devin', 'swe-2'), 'Free');
  assert.strictEqual(api.spawnModelCostTier('devin', 'glm-5.2'), 'Low cost');
  assert.strictEqual(api.spawnModelCostTier('devin', 'adaptive'), '');
});

test('numeric catalog tiers render as in/out rates; no catalog falls back to classifier', () => {
  assert.strictEqual(api.spawnModelCostTier('claude', 'claude-opus-5-5'), '$4/$20');
  assert.strictEqual(api.spawnModelCostTier('kimi', 'kimi-code/k3'), '$$$');
});
