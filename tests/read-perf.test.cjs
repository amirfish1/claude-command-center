const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');

function harness(supported = ['longtask', 'long-animation-frame']) {
  let now = 0;
  const posts = [], callbacks = {}, handlers = {};
  class Observer {
    static supportedEntryTypes = supported;
    constructor(callback) { this.callback = callback; }
    observe(options) { callbacks[options.type || options.entryTypes[0]] = this.callback; }
  }
  const context = vm.createContext({
    performance: {now: () => now}, PerformanceObserver: Observer,
    Date: {now: () => now}, _cccBootId: 'test-boot',
    _perfLongTasks: [], _clientLog: () => {},
    fetch: (_url, opts) => { posts.push(JSON.parse(opts.body).msg); return Promise.resolve(); },
    setInterval: fn => { handlers.tick = fn; },
    document: {
      querySelector: () => ({getAttribute: () => 'app.js?v=test-build'}),
      addEventListener: (event, fn) => { handlers[event] = fn; },
    },
    window: {addEventListener: (event, fn) => { handlers[event] = fn; }},
  });
  const start = app.indexOf('  function _loafReadTag()');
  const end = app.indexOf('  // ── Session-completion sounds', start);
  assert.ok(start > 0 && end > start);
  vm.runInContext('let _ttsActive = false, _ttsPaused = false;\n' + app.slice(start, end), context);
  return {
    state(active, paused = false) { vm.runInContext(`_ttsActive = ${active}; _ttsPaused = ${paused};`, context); },
    tick(t) { now = t; handlers.tick(); },
    event(event) { handlers[event](); },
    entries(type, entries) { callbacks[type]({getEntries: () => entries}); },
    rows: () => posts.map(msg => JSON.parse(msg.slice('[READ-PERF] '.length))),
  };
}

test('exposure excludes idle and paused intervals and flushes short reads', () => {
  const h = harness();
  h.tick(10000);
  assert.equal(h.rows().length, 0);
  h.state(true); h.tick(11000);
  h.tick(12000);
  h.state(true, true); h.tick(13000);
  assert.equal(h.rows().at(-1).read_ms, 2000);
  h.tick(23000);
  h.state(true); h.tick(24000); h.tick(25000);
  h.state(false); h.tick(26000);
  const rows = h.rows();
  assert.deepEqual(rows.map(r => r.read_ms), [2000, 4000]);
  assert.equal(rows[1].seq, 2);
  assert.equal(rows[1].build, 'app.js?v=test-build');
});

test('all observer entries count despite logging dedupe and LOAF throttle', () => {
  const h = harness();
  h.state(true); h.tick(1000);
  h.entries('longtask', [199, 200, 250, 300].map(duration => ({startTime: 1000, duration})));
  h.entries('long-animation-frame', [210, 220, 230].map(duration => ({duration,
    scripts: [{duration: 200, invoker: 'Response.json.then'}]})));
  h.tick(30000);
  const row = h.rows().at(-1);
  assert.equal(row.longtasks, 3);
  assert.equal(row.longtask_ms, 750);
  assert.equal(row.loafs, 3);
  assert.equal(row.sources.json, 3);
  assert.equal(row.read_ms, 29000);
  assert.equal(row.longtask_supported, true);
});

test('suspension uncertainty and unsupported observers remain explicit', () => {
  const h = harness([]);
  h.state(true); h.tick(1000); h.tick(121000);
  const row = h.rows().at(-1);
  assert.equal(row.max_gap_ms, 120000);
  assert.equal(row.longtask_supported, false);
  assert.equal(row.loaf_supported, false);
});

test('lifecycle flushes use cumulative snapshots without duplicate exposure', () => {
  const h = harness();
  h.state(true); h.tick(1000); h.tick(2000);
  h.event('visibilitychange'); h.event('pagehide');
  assert.equal(h.rows().length, 1);
  assert.equal(h.rows()[0].read_ms, 1000);
});

test('snapshot fits the client-log sink even with all source categories', () => {
  const h = harness();
  h.state(true); h.tick(1000);
  h.entries('long-animation-frame', ['FrameRequestCallback', 'Response.json.then', 'TimerHandler:setInterval', 'unknown'].map(invoker => ({duration: 210, scripts: [{duration: 200, invoker}]})));
  h.tick(30000);
  const row = h.rows().at(-1);
  assert.deepEqual(row.sources, {raf: 1, json: 1, timer: 1, other: 1});
  assert.ok(('[READ-PERF] ' + JSON.stringify(row)).length < 500);
});
