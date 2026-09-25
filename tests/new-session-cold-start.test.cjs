const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  function spawnCwdQuickChipOptions(');
const end = app.indexOf('  function renderSpawnCwdQuickChips(', start);
function chips(state, current = '') {
  const context = {
    repoListState: state,
    spawnCwdOptions: state.repos.map(r => ({value: r.path})),
    SPAWN_CWD_CHIP_LIMIT: 10,
    normalizeSpawnCwdPath: value => value || '',
    spawnCwdOptionForPath: value => value ? {value, label: value} : null,
  };
  vm.createContext(context);
  vm.runInContext(app.slice(start, end), context);
  return JSON.parse(JSON.stringify(context.spawnCwdQuickChipOptions(current)));
}
test('fresh folders are neutral and recent selection wins over alphabetical order', () => {
  const result = chips({repos: [{path: '/alpha'}, {path: '/zeta'}], recent: ['/zeta'], rankings: []});
  assert.deepEqual(result.map(r => r.value), ['/zeta', '/alpha']);
  assert.ok(result.every(r => r.kind === 'folders'));
});
test('ranked folders fill the strip before alphabetical fallbacks and groups stay contiguous', () => {
  const rankings = Array.from({length: 10}, (_, i) => ({path: '/ranked-' + i, kind: i === 2 ? 'dev_test' : 'production'}));
  const result = chips({repos: [{path: '/alphabetical'}], recent: [], rankings}, '/chosen');
  assert.equal(result.length, 10);
  assert.equal(result[0].value, '/chosen');
  assert.ok(!result.some(r => r.value === '/alphabetical'));
  const groups = result.map(r => r.kind).filter((kind, i, all) => i === 0 || kind !== all[i - 1]);
  assert.equal(groups.length, new Set(groups).size);
});
test('late rankings improve an automatic default without replacing a typed folder', () => {
  const source = app.slice(app.indexOf('  function populateSpawnCwdPicker('), app.indexOf('  function spawnCwdOptionForPath('));
  const input = {value: ''};
  const context = {
    document: {getElementById: () => input},
    repoListState: {repos: [{path: '/alpha'}], recent: [], rankings: []},
    spawnCwdAutoDefault: '', spawnCwdOptions: [], SPAWN_CWD_KEY: 'cwd', spawnCwdMissing: new Set(),
    localStorage: {getItem: () => ''}, popoutRepoPath: () => '',
    normalizeSpawnCwdPath: value => value || '',
    isSpawnCwdMenuOpen: () => false, renderSpawnCwdQuickChips: () => {},
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  context.populateSpawnCwdPicker();
  assert.equal(input.value, '/alpha');
  context.repoListState.rankings = [{path: '/zeta'}];
  context.populateSpawnCwdPicker();
  assert.equal(input.value, '/zeta');
  input.value = '/typed';
  context.repoListState.recent = ['/new-recent'];
  context.populateSpawnCwdPicker();
  assert.equal(input.value, '/typed');
});
test('zero-history composer suggests workspace repos, most active first, and never the filesystem root', () => {
  const state = {repos: [], recent: [], rankings: [], suggested: [{path: '/w/Apps/fresh'}, {path: '/w/projects/older'}]};
  const context = {
    repoListState: state,
    spawnCwdOptions: [{value: '/'}, {value: '/w/other'}],
    SPAWN_CWD_CHIP_LIMIT: 10,
    normalizeSpawnCwdPath: value => value || '',
    spawnCwdOptionForPath: value => value ? {value, label: value} : null,
  };
  vm.createContext(context);
  vm.runInContext(app.slice(start, end), context);
  const result = JSON.parse(JSON.stringify(context.spawnCwdQuickChipOptions('')));
  assert.deepEqual(result.map(r => r.value), ['/w/Apps/fresh', '/w/projects/older', '/w/other']);
  assert.ok(result.every(r => r.kind === 'folders'));
});
test('zero-history default folder is the most recently active workspace repo', () => {
  const source = app.slice(app.indexOf('  function populateSpawnCwdPicker('), app.indexOf('  function spawnCwdOptionForPath('));
  const input = {value: ''};
  const context = {
    document: {getElementById: () => input},
    repoListState: {repos: [], recent: [], rankings: [], suggested: [{path: '/w/Apps/fresh', label: 'fresh'}]},
    spawnCwdAutoDefault: '', spawnCwdOptions: [], SPAWN_CWD_KEY: 'cwd', spawnCwdMissing: new Set(),
    localStorage: {getItem: () => ''}, popoutRepoPath: () => '',
    normalizeSpawnCwdPath: value => value || '',
    isSpawnCwdMenuOpen: () => false, renderSpawnCwdQuickChips: () => {},
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  context.populateSpawnCwdPicker();
  assert.equal(input.value, '/w/Apps/fresh');
  assert.deepEqual(context.spawnCwdOptions.map(o => o.value), ['/w/Apps/fresh']);
});
test('a blank model is named after its engine, not a bare "Default"', () => {
  const fnStart = app.indexOf('  function formatModelNameForBadge(');
  const fnEnd = app.indexOf('  function getModelPillLabel(', fnStart);
  const labelStart = app.indexOf('  function spawnEngineLabel(');
  const labelEnd = app.indexOf('  function spawnSourceForEngine(', labelStart);
  const context = {MODEL_OPTIONS_BY_ENGINE: {}};
  vm.createContext(context);
  vm.runInContext(app.slice(labelStart, labelEnd) + app.slice(fnStart, fnEnd), context);
  assert.equal(context.formatModelNameForBadge('claude', ''), 'Claude default');
  assert.equal(context.formatModelNameForBadge('antigravity', ''), 'Antigravity default');
  assert.equal(context.formatModelNameForBadge('aider', ''), 'aider default');
});
test('a blank saved model selects the CLI default instead of the priciest listed tier', () => {
  const i = app.indexOf("        } else if (!defaultModel) {");
  assert.ok(i > 0 && i < app.indexOf("        } else if (fallbackOpt) {", i), 'blank-default branch must precede allModels fallback');
});
