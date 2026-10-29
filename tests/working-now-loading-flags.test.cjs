// Working Now loading flicker: "loading" placeholders key off whether a list
// ever landed (_uxqItemsLoaded/_uxqHealthLoaded), not cache freshness (`ts`),
// and queue SSE bursts coalesce into one delayed repaint.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function itemsHarness(fetchImpl) {
  const ctx = vm.createContext({
    backgroundApiFetch: fetchImpl,
    _queueSnapshotIsForeground: () => false,
    _uxqItemRef: it => it && it.ref,
    Date, Set, Map, Promise,
  });
  const start = source.indexOf('  let _uxqItemsCache = ');
  const end = source.indexOf('  // Per-project queue-health snapshot', start);
  assert.ok(start > 0 && end > start, 'items cache block found');
  vm.runInContext(source.slice(start, end) + `
    globalThis.__h = {
      fetch: allowStale => _fetchUxqItems(allowStale),
      loaded: () => _uxqItemsLoaded,
      cache: () => _uxqItemsCache,
    };`, ctx);
  return ctx.__h;
}

test('never-loaded failure stays loading; success flips loaded', async () => {
  let fail = true;
  let calls = 0;
  const h = itemsHarness(async () => {
    calls++;
    if (fail) throw new Error('offline');
    return { json: async () => ({ items: [{ ref: 'CCC-1' }] }) };
  });
  assert.equal(h.loaded(), false);
  assert.deepEqual([...await h.fetch(false)], []);
  assert.equal(h.loaded(), false, 'a failed first fetch is still never-loaded');
  fail = false;
  await h.fetch(false);
  assert.equal(h.loaded(), true);
  assert.equal(h.cache().items.length, 1);
  assert.equal(calls, 2);
});

test('invalidated (stale) cache keeps loaded and serves allowStale reads', async () => {
  let calls = 0;
  const h = itemsHarness(async () => {
    calls++;
    return { json: async () => ({ items: [{ ref: 'CCC-1' }] }) };
  });
  await h.fetch(false);
  h.cache().ts = 0;  // what every SSE invalidation hint does
  assert.equal(h.loaded(), true, 'stale is not never-loaded');
  const items = await h.fetch(true);
  assert.equal(items.length, 1);
  assert.equal(calls, 1, 'allowStale read served from the loaded cache');
  // A later failed revalidation keeps the loaded items.
  const h2 = itemsHarness(async () => { calls++; if (calls > 2) throw new Error('x'); return { json: async () => ({ items: [{ ref: 'A' }] }) }; });
  await h2.fetch(false);
  h2.cache().ts = 0;
  assert.equal((await h2.fetch(false)).length, 1);
  assert.equal(h2.loaded(), true);
});

test('Working Now placeholders key off loaded flags, not ts', () => {
  const body = source.slice(source.indexOf('  function _uxqRenderWorkingNow()'),
    source.indexOf('  function _wtWarmActivityForWorkersLane'));
  assert.match(body, /stage \|\| !_uxqItemsLoaded \? 'loading ticket…'/);
  assert.match(body, /!_uxqHealthLoaded \? 'loading workers' : !_uxqItemsLoaded \? 'loading tickets'/);
  assert.doesNotMatch(body, /!health\.ts|!_uxqItemsCache\.ts/);
});

test('queue SSE event bursts expire caches at once but repaint once', () => {
  const start = source.indexOf('    (function _uxqEventStream() {');
  const end = source.indexOf('    })();', start) + '    })();'.length;
  assert.ok(start > 0 && end > start);
  const timers = [];
  const sources = [];
  let renders = 0;
  const ctx = vm.createContext({
    EventSource: function (url) { this.url = url; sources.push(this); },
    setTimeout: (cb, ms) => { timers.push({ cb, ms }); return timers.length; },
    _dashboardEventStreamHealthy: false,
    _uxqStreamLive: false,
    _uxqLegacyEventSource: null,
    _uxqItemsCache: { ts: 1 }, _uxqHealthCache: { ts: 1 }, _wtWorkersCache: { ts: 1 },
    _queuePanelIsVisible: () => true,
    _renderQueuePanel: () => { renders++; },
  });
  vm.runInContext(source.slice(start, end), ctx);
  assert.equal(timers.length, 1);  // delayed connect
  timers.shift().cb();
  const es = sources[0];
  es.onopen();
  for (let i = 0; i < 5; i++) es.onmessage();
  assert.equal(ctx._uxqItemsCache.ts, 0);
  assert.equal(ctx._uxqHealthCache.ts, 0);
  assert.equal(ctx._wtWorkersCache.ts, 0);
  assert.equal(renders, 0, 'no repaint per event');
  assert.equal(timers.length, 1, 'one coalesced repaint timer');
  assert.equal(timers[0].ms, 750);
  timers.shift().cb();
  assert.equal(renders, 1);
  es.onmessage();
  assert.equal(timers.length, 1, 'next burst schedules a new repaint');
  timers.shift().cb();
  assert.equal(renders, 2);
});
