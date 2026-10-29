const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = fs.readFileSync('static/app.js', 'utf8');
const cache = app.slice(app.indexOf('  const ARCHIVE_SIDEBAR_CACHE_KEY'),
  app.indexOf('  // Dedupe concurrent /api/conversations/list'));
assert.ok(cache.length > 100);

function harness() {
  const storage = new Map();
  const idle = [];
  const paints = [];
  const context = vm.createContext({
    localStorage: {
      getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key),
    },
    window: { requestIdleCallback: fn => idle.push(fn) },
    _archiveWindow: () => '7d',
    _archiveQuery: () => '',
    renderArchiveList: (query, options) => paints.push({ query, options }),
  });
  vm.runInContext(`let archiveLoaded = false, archiveData = [],
    _archiveFromLocalCache = false, archiveDataWindow = null;
    ${cache}
    this.read = _readArchiveSidebarCache;
    this.write = _writeArchiveSidebarCache;
    this.schedule = _scheduleArchiveSidebarCache;
    this.paint = _paintArchiveSidebarCache;
    this.state = () => ({ archiveLoaded, archiveData, _archiveFromLocalCache, archiveDataWindow });
    this.legacyKey = ARCHIVE_SIDEBAR_CACHE_KEY;
    this.key = _archiveSidebarCacheKey('7d');
    this.keyFor = _archiveSidebarCacheKey;`, context);
  return { context, storage, idle, paints };
}

test('bounded cache preserves identity and lifecycle without transcript or live state', () => {
  const { context: c, storage } = harness();
  c.write(Array.from({ length: 1543 }, (_, i) => ({
    session_id: `session-${i}`, engine: 'codex', folder_path: '/tmp/example',
    display_name: 'Example '.repeat(200), first_message: 'Prompt '.repeat(1000),
    archived: true, trashed: true, verified: true, pinned: true, pin_rank: 2,
    parent_session_id: 'parent', all_lane_override: 'workers',
    is_live: true, needs_approval: true, question_text: 'Old question',
    last_assistant_text: 'Transcript '.repeat(10000), session_state: { large: 'detail' },
  })), '7d');
  assert.ok(storage.get(c.key).length <= 256 * 1024);
  const rows = c.read('7d');
  assert.ok(rows.length > 234 && rows.length <= 500);
  assert.equal(rows[0].display_name.length, 400);
  assert.equal(rows[0].first_message.length, 400);
  for (const key of ['archived', 'trashed', 'verified', 'pinned']) assert.equal(rows[0][key], true);
  assert.equal(rows[0].parent_session_id, 'parent');
  assert.equal(rows[0].all_lane_override, 'workers');
  for (const key of ['last_assistant_text', 'session_state', 'is_live', 'needs_approval', 'question_text']) {
    assert.equal(key in rows[0], false);
  }
});

test('bad, expired, future, oversized and wrong-window caches are ignored', () => {
  const { context: c, storage } = harness();
  const good = { version: 1, window: '7d', savedAt: Date.now(), rows: [{ id: 'one' }] };
  for (const value of ['{', 'x'.repeat(256 * 1024 + 1),
    JSON.stringify({ ...good, version: 2 }),
    JSON.stringify({ ...good, window: 'all' }),
    JSON.stringify({ ...good, savedAt: Date.now() - 25 * 3600 * 1000 }),
    JSON.stringify({ ...good, savedAt: Date.now() + 3600 * 1000 }),
    JSON.stringify({ ...good, rows: new Array(501).fill({ id: 'one' }) }),
  ]) {
    storage.set(c.key, value);
    assert.equal(c.read('7d'), null);
  }
  storage.set(c.key, JSON.stringify({ ...good, rows: [null, {}, [], { id: 'one' }] }));
  assert.equal(c.read('7d').length, 1);
  c.localStorage.getItem = () => { throw new Error('Storage disabled'); };
  assert.equal(c.read('7d'), null);
  c.localStorage.setItem = () => { throw new Error('Quota exceeded'); };
  assert.doesNotThrow(() => c.write([{ id: 'one' }], '7d'));
});

test('writes coalesce after paint and cached rows never claim a complete window', () => {
  const { context: c, idle, paints, storage } = harness();
  c.schedule([{ id: 'old' }], '7d');
  c.schedule([{ id: 'new' }], '7d');
  assert.equal(storage.size, 0);
  assert.equal(idle.length, 1);
  idle.shift()();
  c.paint();
  assert.equal(paints.length, 1);
  assert.equal(paints[0].options.skipRestore, true);
  assert.equal(c.state().archiveData[0].id, 'new');
  assert.equal(c.state()._archiveFromLocalCache, true);
  assert.equal(c.state().archiveDataWindow, null);
  c.paint();
  assert.equal(paints.length, 1, 'cache must not overwrite loaded network data');
});

test('boot paints cache before waiting on the list request and always revalidates', async () => {
  const { context: c, paints } = harness();
  c.write([{ session_id: 'cached', display_name: 'Cached task' }], '7d');
  const start = app.indexOf('  async function setArchiveMode()');
  const end = app.indexOf('  (function wireArchiveMode()', start);
  let refreshes = 0;
  Object.assign(c, {
    document: { getElementById: () => null },
    kanbanView: false, isFlowView: () => false,
    _ARCHIVE_MODE_KEY: 'ccc-archive-mode',
    _renderQueuePanel: () => {},
    refreshArchiveData: () => { refreshes++; return new Promise(() => {}); },
  });
  vm.runInContext(app.slice(start, end) + '\nsetArchiveMode();', c);
  assert.equal(refreshes, 1);
  assert.equal(paints.length, 1);
  assert.equal(c.state().archiveData[0].session_id, 'cached');
});

test('network failure keeps the preview; successful empty history removes it', async () => {
  for (const response of [null, [], [{ id: 'fresh' }]]) {
    const { context: c } = harness();
    c.write([{ id: 'cached' }], '7d');
    c.paint();
    Object.assign(c, {
      _clientLog() {}, _startArchiveProgressPoll() {}, _stopArchiveProgressPoll() {},
      _markArchiveFirstLoaded() {}, _mergeIntoMasterRows() {}, _resetArchiveUnknownPatchIds() {},
      loadArchiveAll: async () => response,
      _mergeArchivePrSnapshot: fresh => fresh,
      _applyPendingDashboardConversationPatches: rows => rows,
      refreshLiveSessionsActivity: async () => {},
      _queueDashboardArchiveRender() {}, setTimeout() {},
    });
    const start = app.indexOf('  let _archiveRefreshPromise = null;');
    const end = app.indexOf('  async function refreshGhIssuesSection()', start);
    vm.runInContext(app.slice(start, end) + '\nthis.refresh = refreshArchiveData;', c);
    await c.refresh();
    if (response === null) {
      assert.equal(c.state().archiveData[0].id, 'cached');
      assert.equal(c.state()._archiveFromLocalCache, true);
    } else {
      assert.equal(c.state().archiveData.length, response.length);
      assert.equal(c.state().archiveData[0]?.id, response[0]?.id);
      assert.equal(c.state()._archiveFromLocalCache, false);
    }
  }
});

test('each window keeps its own snapshot so tabs on different windows do not evict each other', () => {
  const { context: c, storage } = harness();
  storage.set(c.legacyKey, 'stale');
  c.write([{ id: 'week' }], '7d');
  c.write([{ id: 'all' }], 'all');
  assert.equal(storage.has(c.legacyKey), false);
  assert.equal(c.read('7d')[0].id, 'week');
  assert.equal(c.read('all')[0].id, 'all');
  assert.equal(c.read('8h'), null);
});
