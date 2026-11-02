// The Queue panel and sidebar badges share one delta-synced copy of
// /api/queue/list (CCC-142): ?since=<version> answers merge into the copy,
// and anything that cannot merge falls back to a full read.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = app.indexOf('  let _queueListMirror = ');
const code = app.slice(start, app.indexOf('  async function _fetchUxqItems(', start));

function load(responses) {
  const urls = [];
  const ctx = {
    backgroundApiFetch: async url => {
      urls.push(url);
      const body = responses.shift();
      return { ok: true, json: async () => body };
    },
  };
  vm.runInNewContext(code + '\nthis.fetchList = _queueListFetch; this.merge = _queueListMerge;', ctx);
  return { ctx, urls };
}

// vm arrays come from another realm: compare elements, not prototypes.
const same = (got, want) => { assert.equal(got.length, want.length); want.forEach((w, i) => assert.equal(got[i], w)); };
const A = { ref: 'A', s: 1 }, B = { ref: 'B', s: 1 }, B2 = { ref: 'B', s: 2 }, C = { ref: 'C' };

test('first read is full; later reads send since and merge only changes', async () => {
  const { ctx, urls } = load([
    { ok: true, items: [A, B], version: 'v1', order: ['A', 'B'], synced_at: 1 },
    { ok: true, delta: true, version: 'v2', order: ['B', 'C'], changed: { B: B2, C } },
    { ok: true, delta: true, unchanged: true, version: 'v2' },
  ]);
  same((await ctx.fetchList()).items, [A, B]);
  const second = await ctx.fetchList();
  same(second.items, [B2, C]);
  const third = await ctx.fetchList();
  assert.equal(third.items, second.items);
  assert.deepEqual(urls, ['/api/queue/list?slim=1', '/api/queue/list?slim=1&since=v1', '/api/queue/list?slim=1&since=v2']);
});

test('a delta naming a ticket the copy lacks falls back to a full read', async () => {
  const { ctx, urls } = load([
    { ok: true, items: [A], version: 'v1', order: ['A'] },
    { ok: true, delta: true, version: 'v2', order: ['A', 'Z'], changed: {} },
    { ok: true, items: [A, C], version: 'v3', order: ['A', 'C'] },
  ]);
  await ctx.fetchList();
  same((await ctx.fetchList()).items, [A, C]);
  assert.deepEqual(urls.slice(1), ['/api/queue/list?slim=1&since=v1', '/api/queue/list?slim=1']);
});

test('an old server without version/order keeps doing full reads', async () => {
  const { ctx, urls } = load([{ ok: true, items: [A] }, { ok: true, items: [B] }]);
  await ctx.fetchList();
  same((await ctx.fetchList()).items, [B]);
  assert.deepEqual(urls, ['/api/queue/list?slim=1', '/api/queue/list?slim=1']);
});

test('a failed read throws so callers keep their last good copy', async () => {
  const { ctx } = load([{ ok: false, error: 'boom' }]);
  await assert.rejects(ctx.fetchList(), /boom/);
});

test('worker heartbeats do not expire the ticket list', () => {
  assert.match(app, /const workersOnly = Array\.isArray\(what\)/);
  assert.match(app, /if \(!workersOnly\) _uxqItemsCache\.ts = 0;/);
  assert.match(app, /if \(resources\.has\('queue'\) \|\| resources\.has\('workers'\)\)/);
  assert.match(app, /const refreshItems = !ticketsChanged \? null :/);
});
