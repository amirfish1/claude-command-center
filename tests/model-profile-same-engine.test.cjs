// CCC-164: model profiles may route two models on one engine (opus then
// sonnet); worker fallback stays one entry per engine.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  function fallbackModelChoices(engine) {');
const end = source.indexOf('  function renderFallbackList(', start);
const addStart = source.indexOf('  function addFallbackModel(policy, render, sectionId) {');
const addEnd = source.indexOf("  document.getElementById('workerFallbackAdd')", addStart);
assert.ok(start >= 0 && end > start && addStart > 0 && addEnd > addStart, 'helpers present');

const CATALOG = {
  claude: ['claude-opus-5-5', 'claude-sonnet-5-5'],
  codex: ['gpt-6.1-sol'],
};
function load() {
  const ctx = vm.createContext({
    SPAWN_DEFAULT_OTHER: '__other__',
    spawnDefaultsState: {model_profile_engines: ['claude', 'codex'], worker_fallback_engines: ['claude', 'codex']},
    modelOptionsForSpawnEngine: (engine) => (CATALOG[engine] || []).map(id => ({id})).concat([{id: '__other__'}]),
    effortLevelsForEngine: () => [{id: 'medium'}],
    spawnEngineLabel: (id) => id,
    spawnDefaultsInlineError(text) { ctx.lastError = text; },
    persistSpawnDefaults() { ctx.saves = (ctx.saves || 0) + 1; },
    document: {getElementById: () => null},
  });
  vm.runInContext(source.slice(start, end) + source.slice(addStart, addEnd), ctx);
  return ctx;
}

test('profile adds a second model on an engine already in the route', () => {
  const ctx = load();
  const policy = {models: [
    {engine: 'claude', model: 'claude-opus-5-5', effort: 'medium'},
    {engine: 'codex', model: 'gpt-6.1-sol', effort: 'medium'},
  ]};
  ctx.addFallbackModel(policy, () => {}, 'modelProfilesSettings');
  assert.equal(policy.models.length, 3);
  assert.deepEqual({...policy.models[2]}, {engine: 'claude', model: 'claude-sonnet-5-5', effort: 'medium'});
  // Every engine+model pair is used now: nothing left to add.
  ctx.addFallbackModel(policy, () => {}, 'modelProfilesSettings');
  assert.equal(policy.models.length, 3);
  assert.ok(ctx.lastError);
});

test('profile still prefers an unused engine first', () => {
  const ctx = load();
  const policy = {models: [{engine: 'claude', model: 'claude-opus-5-5', effort: ''}]};
  ctx.addFallbackModel(policy, () => {}, 'modelProfilesSettings');
  assert.equal(policy.models[1].engine, 'codex');
});

test('unused models dedupe on engine+model and skip the edited row', () => {
  const ctx = load();
  const policy = {models: [
    {engine: 'claude', model: 'claude-opus-5-5'},
    {engine: 'claude', model: 'claude-sonnet-5-5'},
  ]};
  assert.equal(ctx.fallbackUnusedModels(policy, 'claude', 'modelProfilesSettings', -1).length, 0);
  assert.deepEqual(ctx.fallbackUnusedModels(policy, 'claude', 'modelProfilesSettings', 1).map(m => m.id), ['claude-sonnet-5-5']);
});

test('worker fallback keeps one entry per engine', () => {
  const ctx = load();
  const policy = {models: [
    {engine: 'claude', model: 'claude-opus-5-5'},
    {engine: 'codex', model: 'gpt-6.1-sol'},
  ]};
  assert.equal(ctx.fallbackUnusedModels(policy, 'claude', 'workerFallbackSettings', -1).length, 0);
  ctx.addFallbackModel(policy, () => {}, 'workerFallbackSettings');
  assert.equal(policy.models.length, 2);
});
