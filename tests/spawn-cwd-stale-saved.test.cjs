// A reinstall (or a deleted repo/worktree) leaves the saved New Session folder
// in localStorage pointing at a path that no longer exists. The picker used to
// restore it unchecked, so every spawn failed with
// "invalid cwd: path does not exist" until the user noticed and retyped it.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('  const SPAWN_CWD_KEY = ');
const end = source.indexOf('  function spawnCwdOptionForPath(', start);
assert.ok(start > 0 && end > start, 'spawn cwd picker block found');

const STALE = '/home/u/Apps/deleted-repo';
const LIVE = '/home/u/Apps/live-repo';
const flush = () => new Promise(r => setImmediate(r));

function harness({ saved, repoListState, fsList }) {
  const store = new Map(saved ? [['ccc-spawn-cwd', saved]] : []);
  const picker = { value: '' };
  const fetched = [];
  const ctx = vm.createContext({
    localStorage: {
      getItem: k => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: k => store.delete(k),
    },
    document: { getElementById: id => (id === 'spawnCwdPicker' ? picker : null) },
    fetch: url => {
      fetched.push(url);
      const p = decodeURIComponent(url.split('path=')[1] || '');
      return Promise.resolve({ json: () => Promise.resolve(fsList(p)) });
    },
    repoListState,
    popoutRepoPath: () => '',
    isSpawnCwdMenuOpen: () => false,
    renderSpawnCwdQuickChips: () => {},
    updateNewSessionCwdNotice: () => {},
  });
  vm.runInContext(source.slice(start, end), ctx);
  return { ctx, store, picker, fetched };
}

const missing = p => ({ ok: false, error: 'not a directory: ' + p });
const present = p => ({ ok: true, path: p, dirs: [] });

test('a saved folder that no longer exists is dropped for a live default', async () => {
  const h = harness({
    saved: STALE,
    repoListState: { repos: [], recent: [LIVE], rankings: [], suggested: [] },
    fsList: missing,
  });
  h.ctx.populateSpawnCwdPicker();
  await flush(); await flush();
  assert.equal(h.fetched.length, 1);
  assert.equal(h.picker.value, LIVE);
  assert.equal(h.store.has('ccc-spawn-cwd'), false);
  h.ctx.populateSpawnCwdPicker();
  assert.equal(h.picker.value, LIVE, 'never re-offered this page load');
});

test('a saved folder the repo list already vouches for is kept without a check', async () => {
  const h = harness({
    saved: LIVE,
    repoListState: { repos: [{ path: LIVE }], recent: [], rankings: [], suggested: [] },
    fsList: missing,
  });
  h.ctx.populateSpawnCwdPicker();
  await flush();
  assert.deepEqual(h.fetched, []);
  assert.equal(h.picker.value, LIVE);
});

test('a saved folder outside the repo list is checked once and kept if present', async () => {
  const typed = '/home/u/scratch/sub';
  const h = harness({
    saved: typed,
    repoListState: { repos: [], recent: [LIVE], rankings: [], suggested: [] },
    fsList: present,
  });
  h.ctx.populateSpawnCwdPicker();
  h.ctx.populateSpawnCwdPicker();
  await flush(); await flush();
  assert.equal(h.fetched.length, 1);
  assert.equal(h.picker.value, typed);
  assert.equal(h.store.get('ccc-spawn-cwd'), typed);
});

test('an invalid-cwd spawn failure forgets the folder before the retry', () => {
  const fn = source.indexOf('  async function spawnFromInlineInput(');
  const body = source.slice(fn, source.indexOf('\n  }\n', fn));
  assert.match(body, /invalid cwd: \(path does not exist\|not a directory\)/);
  assert.ok(body.indexOf('forgetMissingSpawnCwd(launchCwd)') < body.indexOf('restoreDraftAfterFailure();\n        flashRed()'));
});
