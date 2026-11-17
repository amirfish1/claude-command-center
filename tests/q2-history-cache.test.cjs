const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/q2.js', 'utf8');
const historySource = source.slice(source.indexOf('  var HISTORY_POLL_MS'),
  source.indexOf('  function renderLiveWorkersStrip'));
const points = [{ts: 100, open: 4, closed: 6, needs_input: 1},
  {ts: 200, open: 5, closed: 7, needs_input: 2}];

function harness(saved, fetcher) {
  const storage = new Map(saved ? [['q2-history-7d-hourly-v1', JSON.stringify(saved)]] : []);
  const state = {queueHistory: null};
  const host = {innerHTML: ''};
  let calls = 0;
  const context = vm.createContext({state, Date, Number, JSON, Array, AbortController,
    setTimeout, clearTimeout, esc: s => s,
    localStorage: {getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v)},
    fetch: (...args) => { calls++; return fetcher(...args); },
    renderDiagram: () => context.renderQueueHistoryChart(host)});
  vm.runInContext(historySource, context);
  return {context, state, storage, host, calls: () => calls,
    render: () => context.renderQueueHistoryChart(host)};
}

test('saved graph paints before a delayed request; fresh result replaces and persists it', async () => {
  let resolve;
  const h = harness({fetchedAt: Date.now(), points}, () => new Promise(r => {resolve = r;}));
  h.render();
  assert.match(h.host.innerHTML, /q2-history-svg/);
  assert.match(h.host.innerHTML, /Saved history/);
  h.render();
  assert.equal(h.calls(), 1, 'one in-flight refresh');
  const fresh = [{ts: 300, open: 9}];
  resolve({ok: true, json: async () => ({ok: true, points: fresh})});
  await new Promise(setImmediate);
  assert.deepEqual(h.state.queueHistory, fresh);
  assert.doesNotMatch(h.host.innerHTML, /Saved history/);
  assert.deepEqual(JSON.parse(h.storage.get('q2-history-7d-hourly-v1')).points, fresh);
  h.render();
  assert.equal(h.calls(), 1, 'successful requests retain five-minute cadence');
});

test('failed refresh preserves saved graph and releases guard for retry', async () => {
  const h = harness({fetchedAt: Date.now(), points}, async () => ({ok: false}));
  h.render();
  await new Promise(setImmediate);
  assert.match(h.host.innerHTML, /q2-history-svg/);
  assert.match(h.host.innerHTML, /retrying/);
  assert.equal(h.calls(), 1, 'failure repaint does not recursively retry');
  h.render();
  assert.equal(h.calls(), 2);
  await new Promise(setImmediate);
});

test('invalid/expired storage is ignored and cold errors do not stay Loading forever', async () => {
  for (const saved of [{fetchedAt: Date.now() - 25 * 3600000, points},
    {fetchedAt: Date.now(), points: [null]}]) {
    const h = harness(saved, async () => {throw new Error('offline');});
    h.render();
    assert.match(h.host.innerHTML, /Loading history/);
    await new Promise(setImmediate);
    assert.match(h.host.innerHTML, /Couldn’t load history/);
  }
});

test('empty refreshing server cannot erase saved graph or overwrite saved time', async () => {
  const saved = {fetchedAt: Date.now() - 10000, points};
  const h = harness(saved, async () => ({ok: true,
    json: async () => ({ok: true, points: [], refreshing: true})}));
  h.render();
  await new Promise(setImmediate);
  assert.match(h.host.innerHTML, /q2-history-svg/);
  assert.deepEqual(JSON.parse(h.storage.get('q2-history-7d-hourly-v1')), saved);
});
