const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = require('./legacy-dashboard-source.cjs');
const start = app.indexOf('  function _ttsSpeakBrowser(');
const source = app.slice(start, app.indexOf('\n  }\n', start) + 4);

function harness(ready = true) {
  let callbacks;
  const restarts = [], starts = [], highlights = [], logs = [];
  const state = {chunks: [{text: 'A complete long read.', start: 10}], paneId: 'p2', index: 0,
    perf: {stalls: 0, maxStall: 0}};
  const c = vm.createContext({
    _ttsChunkState: state, _ttsPlaybackToken: 0, _ttsUtterance: null, _ttsBrowserPlayback: null,
    _ttsRate: 1.25, _ttsPaused: false, _ttsLastCharIndex: 0,
    _ttsBoundUtteranceText: '0123456789A complete long read.',
    window: {cccBrowserVoice: {start: (text, opts) => {
      callbacks = opts;
      return ready ? {cancel() {}} : null;
    }}},
    ttsBrowserVoiceForPane: () => null,
    _ttsLog: (...args) => logs.push(args),
    _ttsPerfStart: (s, how) => starts.push(how), _ttsShowVoice() {}, _ttsSetStreaming() {},
    setTtsButtonsBusy() {}, setTtsButtonsState() {}, _ttsSetPlay() {},
    _ttsStartChunkedSpeech: (...args) => restarts.push(args),
    _ttsWordAtProgress: () => ({start: 3, length: 4}),
    highlightTtsWord: (...args) => highlights.push(args), updateTtsCaption() {},
    _ttsSpeakNextChunk() {},
  });
  vm.runInContext(source, c);
  c._ttsSpeakBrowser(state, state.chunks[0]);
  return {c, state, restarts, starts, highlights, logs, get callbacks() {return callbacks;}};
}

test('audio-clock start, word offsets and raw underruns reach the existing speaker state', () => {
  const h = harness();
  h.callbacks.onstart({lagMs: 600});
  assert.equal(h.starts[0].engine, 'browser');
  assert.equal(h.starts[0].lagMs, 600);
  h.callbacks.onposition({played: 1, underruns: 2, maxGapMs: 123});
  assert.equal(h.c._ttsLastCharIndex, 13);
  assert.deepEqual(h.highlights[0], [13, 4]);
  assert.equal(h.state.perf.stalls, 2);
  assert.equal(h.state.perf.maxStall, 123);
});

test('failure resumes the existing splitter from the last word, with pause preserved', () => {
  const h = harness();
  h.c._ttsLastCharIndex = 21;
  h.c._ttsPaused = true;
  h.callbacks.onerror(new Error('underrun'));
  assert.deepEqual(h.restarts[0], ['long read.', 'p2', 21, true, true]);
  assert.equal(h.c._ttsBrowserPlayback, null);
});

test('a model that stopped between selection and start falls back without losing the text', () => {
  const h = harness(false);
  assert.deepEqual(h.restarts[0], ['A complete long read.', 'p2', 10, false, true]);
});

test('late callbacks of a cancelled/replaced read cannot change the new read', () => {
  const h = harness();
  h.c._ttsChunkState = {index: 4};
  h.callbacks.onstart({}); h.callbacks.onposition({played: 4});
  h.callbacks.onerror(new Error('late')); h.callbacks.onend();
  assert.equal(h.restarts.length, 0);
  assert.equal(h.starts.length, 0);
  assert.equal(h.c._ttsChunkState.index, 4);
});
