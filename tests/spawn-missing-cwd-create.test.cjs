// CCC-1187: starting a New Session in a folder that doesn't exist offers to
// create it, then spawns there — instead of only failing.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./legacy-dashboard-source.cjs');
const start = source.indexOf('  // CCC-1187: a New Session aimed at a folder');
const end = source.indexOf('  // Check a restored cwd once per page load', start);
assert.ok(start >= 0 && end > start, 'helper block present');

function load({fsList, create, known = false, conversation = '__new__', input = 'draft'}) {
  const calls = {fetch: [], spawned: [], toasts: [], cwd: null};
  const ctx = vm.createContext({
    spawnCwdMissing: new Set(),
    repoListState: {},
    currentConversation: conversation,
    $convInput: {value: input},
    normalizeSpawnCwdPath: (v) => String(v || '').trim(),
    spawnCwdKnownToExist: () => known,
    escapeHtml: (v) => String(v),
    populateSpawnCwdPicker() {},
    setSpawnCwdInputValue: (p) => { calls.cwd = p; },
    showOpToast: (msg, kind, action) => calls.toasts.push({msg, kind, action}),
    spawnFromInlineInput: (text) => calls.spawned.push(text),
    encodeURIComponent, JSON, Error,
    fetch: async (url, opts) => {
      calls.fetch.push(url);
      const body = url.startsWith('/api/fs/list') ? fsList : create;
      return {ok: body.ok !== false, status: body.ok === false ? 400 : 200, json: async () => body};
    },
  });
  vm.runInContext(source.slice(start, end), ctx);
  return {ctx, calls};
}

test('a nonexistent folder is detected; known repos skip the round trip', async () => {
  let {ctx, calls} = load({fsList: {ok: false, error: 'not a directory: /x'}});
  assert.equal(await ctx.spawnCwdIsMissing('/x'), true);
  ({ctx, calls} = load({fsList: {ok: true, dirs: []}}));
  assert.equal(await ctx.spawnCwdIsMissing('/x'), false);
  ({ctx, calls} = load({fsList: {ok: false, error: 'not a directory'}, known: true}));
  assert.equal(await ctx.spawnCwdIsMissing('/x'), false);
  assert.equal(calls.fetch.length, 0);
});

test('the offer creates the folder, points the picker at it and spawns', async () => {
  const {ctx, calls} = load({create: {ok: true, path: '/Users/me/new', repos: []}});
  ctx.offerCreateMissingSpawnCwd('/Users/me/new', 'hello');
  assert.equal(calls.toasts[0].action.label, 'Create folder & start');
  await ctx.createMissingSpawnCwdAndSpawn('/Users/me/new', 'hello');
  assert.equal(calls.cwd, '/Users/me/new');
  assert.deepEqual(calls.spawned, ['draft']);
});

test('a failed create does not spawn; leaving New Session does not spawn', async () => {
  let {ctx, calls} = load({create: {ok: false, error: 'path must be inside your home directory'}});
  await ctx.createMissingSpawnCwdAndSpawn('/etc/x', 'hello');
  assert.deepEqual(calls.spawned, []);
  assert.match(calls.toasts.at(-1).msg, /Could not create folder/);
  ({ctx, calls} = load({create: {ok: true, path: '/Users/me/new'}, conversation: 'abc'}));
  await ctx.createMissingSpawnCwdAndSpawn('/Users/me/new', 'hello');
  assert.deepEqual(calls.spawned, []);
});
