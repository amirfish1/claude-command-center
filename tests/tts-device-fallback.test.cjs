const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('./legacy-dashboard-source.cjs');
function declaration(name) {
  const start = source.search(new RegExp('  (?:async )?function ' + name + '\\('));
  assert.ok(start >= 0, name);
  return source.slice(start, source.indexOf('\n  }', start) + 4);
}
function fixture() {
  const spoken = [], notices = [], browser = [], marks = [];
  const context = {performance, console, setTimeout,
    window: {cccBrowserVoice: {ready: true}, speechSynthesis: {speak: u => spoken.push(u)}},
    SpeechSynthesisUtterance: function(text) {this.text = text;},
    TTS_CHUNK_MAX_CHARS: 18, _ttsRate: 1.25, _ttsChunkState: null, _ttsPerfState: null,
    _ttsBoundUtteranceText: '', _ttsUtterance: null, _ttsPerfTarget: 'selection', _ttsActivePaneId: 'pane', _ttsLastCharIndex: 0,
    activePaneId: () => 'pane', _ttsPerfFlush() {}, _ttsPerfStart() {}, _ttsShowVoice() {},
    setTtsButtonsState() {}, setTtsButtonsBusy() {}, ttsButtons: () => [], _ttsSetPlay() {},
    highlightTtsWord: (...v) => marks.push(v), updateTtsCaption() {},
    _ttsSpeakBrowser: (state, chunk) => browser.push({state, chunk}),
    stopTextToSpeech() {}, showOpToast: (...v) => notices.push(v),
  };
  vm.createContext(context);
  for (const name of ['_ttsIsHebrew','_chunkTtsText','_ttsBindChunkCompletion','_ttsStartChunkedSpeech','_ttsSpeakNextChunk'])
    vm.runInContext(declaration(name), context);
  return {context, spoken, notices, browser, marks};
}
test('ready English goes directly to browser playback without server clips', () => {
  const h = fixture();
  h.context._ttsStartChunkedSpeech('A complete English reply that exceeds a device chunk.', 'pane', 0, false);
  assert.equal(h.browser.length, 1);
  assert.equal(h.spoken.length, 0);
  assert.equal(h.browser[0].state.chunks.length, 1);
  assert.equal(h.browser[0].state.perf.mode, 'browser');
});
test('Hebrew retains device language, word offsets and chunk-error continuation', () => {
  const h = fixture();
  h.context._ttsStartChunkedSpeech('שלום עולם. עוד משפט ארוך לקריאה.', 'pane', 12, false);
  const first = h.spoken[0];
  assert.equal(first.lang, 'he-IL');
  assert.equal(h.browser.length, 0);
  first.onboundary({name: 'word', charIndex: 2, charLength: 3});
  assert.deepEqual(h.marks[0], [14, 3]);
  first.onerror({error: 'synthesis-failed'});
  assert.equal(h.spoken.length, 2);
  assert.equal(h.notices.length, 1);
  first.onend();
  assert.equal(h.spoken.length, 2, 'late completion cannot advance twice');
});
test('failed browser read stays on device even if the model still reports ready', () => {
  const h = fixture();
  h.context._ttsStartChunkedSpeech('Continue this English reply on the device.', 'pane', 15, true, true);
  assert.equal(h.browser.length, 0);
  assert.equal(h.spoken.length, 1);
  assert.equal(h.context._ttsChunkState.pauseOnStart, true);
  assert.equal(h.context._ttsChunkState.deviceOnly, true);
});
test('a replaced read ignores stale device boundaries and errors', () => {
  const h = fixture();
  h.context._ttsStartChunkedSpeech('An English device fallback.', 'pane', 0, false, true);
  const old = h.spoken[0];
  h.context._ttsStartChunkedSpeech('A new English browser read.', 'pane', 0, false);
  old.onboundary({name: 'word', charIndex: 1, charLength: 2});
  old.onerror({error: 'synthesis-failed'});
  assert.equal(h.marks.length, 0);
  assert.equal(h.notices.length, 0);
  assert.equal(h.browser.length, 1);
});
test('seek and rate restarts retain the word offset, paused state and device fallback', () => {
  const restarts = [], cancels = [];
  const c = vm.createContext({_ttsBoundUtteranceText:'First sentence. Next sentence.',
    _ttsLastCharIndex: 16, _ttsPaused:true, _ttsActive:true,
    _ttsChunkState:{deviceOnly:true},_ttsActivePaneId:'pane',
    _ttsEngineCancel:()=>cancels.push(true),activePaneId:()=> 'pane',
    _ttsStartChunkedSpeech:(...args)=>{restarts.push(args);return true;}});
  vm.runInContext(declaration('_restartTtsAtCurrentPosition'),c);
  assert.equal(c._restartTtsAtCurrentPosition(),true);
  assert.deepEqual(restarts[0],['Next sentence.','pane',16,true,true]);
  assert.equal(cancels.length,1);
});
test('stop cancels browser playback even on a device without speechSynthesis', async () => {
  let cancelled=0;
  const c=vm.createContext({window:{},_ttsChunkState:{},_ttsDirectBtn:null,
    clearTtsHighlight(){},clearTtsCaption(){},_ttsShowVoice(){},_ttsPerfFlush(){},
    _ttsEngineCancel:()=>cancelled++,setTtsButtonsBusy(){},setTtsButtonsState(){}});
  vm.runInContext(declaration('stopTextToSpeech'),c);
  await c.stopTextToSpeech();
  assert.equal(cancelled,1);
  assert.equal(c._ttsUtterance,null);
  assert.equal(c._ttsBoundUtteranceText,'');
});
