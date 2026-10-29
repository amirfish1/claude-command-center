const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
const constants = app.slice(app.indexOf('  const F2_LAUNCH_ENGINES ='), app.indexOf('  // Fallback ladder only.'));
const helpers = app.slice(app.indexOf('  function f2AllLaunchEngines('), app.indexOf('  // One alternative, one pill.'));

test('continuation offers all session launch engines with their own models and efforts', () => {
  const context = vm.createContext({
    SPAWN_DEFAULT_ENGINES: ['claude', 'codex', 'cursor', 'antigravity', 'kilo', 'hermes', 'kimi', 'opencode', 'devin', 'grok'],
    spawnEngineLabel: engine => engine.toUpperCase(),
    spawnDefaultsState: { disabled_engines: ['kimi', 'kilo', 'opencode'] },
    MODEL_OPTIONS_BY_ENGINE: { cursor: [{ id: 'auto', label: 'Auto' }], grok: [{ id: 'grok-test', label: 'Grok test' }] },
    effortLevelsForEngine: engine => engine === 'grok' ? [{ id: 'high', label: 'High' }] : [],
  });
  vm.runInContext(constants + helpers, context);
  const evaluate = expression => JSON.parse(vm.runInContext(`JSON.stringify(${expression})`, context));
  assert.deepEqual(evaluate('f2LaunchEngines().map(e => e.id)'), ['claude', 'codex', 'cursor', 'antigravity', 'hermes', 'devin', 'grok']);
  assert.deepEqual(evaluate('f2ModelsForEngine("cursor")'), [{ id: 'auto', label: 'Auto' }]);
  assert.deepEqual(evaluate('f2ModelsForEngine("grok")'), [{ id: 'grok-test', label: 'Grok test' }]);
  assert.deepEqual(evaluate('f2EffortsForEngine("cursor")'), []);
  assert.deepEqual(evaluate('f2EffortsForEngine("grok")'), [{ id: 'high', label: 'High' }]);
});
