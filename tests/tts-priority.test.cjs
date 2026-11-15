const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync('static/app.js', 'utf8');

function harness() {
  const leases = [], timers = new Map(), events = {};
  const context = vm.createContext({
    crypto: webcrypto, Uint8Array,
    window: {addEventListener: (event, callback) => {events[event] = callback;}},
    setInterval: callback => {timers.set(1, callback); return 1;},
    clearInterval: id => timers.delete(id),
    fetch: (url, options) => {leases.push({url, ...options, payload: JSON.parse(options.body)}); return Promise.resolve();},
  });
  vm.runInContext(`
    let _ttsActive = true, _ttsPaused = false, _ttsNeural = null, _ttsNeuralToken = 0, _ttsBrowserPlayback = null;
    const _ttsSetStreaming = () => {};
  ` + source.slice(source.indexOf('  const _ttsPriorityClient ='), source.indexOf('  function _ttsLog('))
    + source.slice(source.indexOf('  function _ttsEnginePause('), source.indexOf('  function _ttsEngineResume('))
    + source.slice(source.indexOf('  function _ttsEngineCancel('), source.indexOf('  // Prepare-ahead:')), context);
  return {leases, timers, events, run: code => vm.runInContext(code, context)};
}

test('preparation is quiet; playing renews one ordered lease and pause releases it', () => {
  const h = harness();
  assert.equal(h.leases.length, 0);
  h.run('_ttsPriorityPlaying(); _ttsPriorityPlaying()');
  assert.equal(h.timers.size, 1);
  h.timers.get(1)();
  h.run('_ttsEnginePause()');
  assert.equal(h.timers.size, 0);
  assert.deepEqual(h.leases.map(l => [l.payload.playing, l.payload.seq]), [[true, 1], [true, 2], [false, 3]]);
  assert.ok(h.leases.every(l => l.keepalive && l.cccRetryHandled && l.cccPriority === 'user'));
});

test('resume starts a newer lease; cancel and page departure stop renewal', () => {
  const h = harness();
  h.run('_ttsPriorityPlaying(); _ttsEnginePause(); _ttsPriorityPlaying(); _ttsEngineCancel()');
  assert.deepEqual(h.leases.map(l => l.payload.playing), [true, false, true, false]);
  h.run('_ttsPriorityPlaying()');
  h.events.pagehide();
  assert.equal(h.leases.at(-1).payload.playing, false);
  assert.equal(h.timers.size, 0);
});

test('an inactive or paused reader cannot keep renewing its lease', () => {
  for (const state of ['_ttsActive = false', '_ttsPaused = true']) {
    const h = harness();
    h.run('_ttsPriorityPlaying(); ' + state);
    h.timers.get(1)();
    assert.equal(h.leases.at(-1).payload.playing, false);
    assert.equal(h.timers.size, 0);
  }
});
