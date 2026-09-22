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
    spawnCwdAutoDefault: '', spawnCwdOptions: [], SPAWN_CWD_KEY: 'cwd',
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
