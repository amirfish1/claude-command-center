// A read crosses its clip boundaries inside the media pipeline, so a blocked
// main thread cannot open a silence between clips (CCC-144). Real Chrome and
// real MediaSource: a fake element would prove nothing about either.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test, before, after } = require('node:test');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const start = source.indexOf('  function _ttsSpeakChunkNeural(');
const player = source.slice(start, source.indexOf('  // One-time iOS-Safari', start));
const wordAt = source.slice(source.indexOf('  function _ttsWordAtProgress('), source.indexOf('  // Continuous read (CCC-144)'));

// Silent MPEG-1 Layer III frames: 128 kbps, 44.1 kHz, 417 bytes, 26.12 ms each.
const harness = `
  const silence = (seconds) => {
    const frames = Math.round(seconds / 0.02612), out = new Uint8Array(frames * 417);
    for (let i = 0; i < frames; i++) out.set([0xff, 0xfb, 0x90, 0x64], i * 417);
    return out;
  };
  window.fetched = []; window.logs = []; window.silences = [];
  let _ttsNeuralToken = 0, _ttsUtterance = null, _ttsNeural = null, _ttsAudio = null, _ttsRate = 1;
  let _ttsPaused = false, _ttsLastCharIndex = 0, _ttsActivePaneId = 'p1', _ttsDirectBtn = null, _ttsBoundUtteranceText = '';
  const noop = () => {};
  const _ttsCanStream = () => true, _ttsContinuousOk = () => false, _ttsEventLagMs = () => 0;
  const _ttsSetPlay = noop, _ttsSetStreaming = noop, _ttsScheduleCacheDebug = noop, _ttsShowVoice = noop;
  const clearTtsHighlight = noop, clearTtsCaption = noop, highlightTtsWord = noop, updateTtsCaption = noop;
  const setTtsButtonsState = noop, setTtsButtonsBusy = noop, _setTtsDirectBtnState = noop, showOpToast = noop;
  const ttsButtons = () => [], _ttsCool = noop, _ttsCoolMs = () => 0, _TTS_FAIL_COOL_MS = 0;
  const _ttsHeadCache = new Map();
  const _ttsPerfStart = noop, _ttsPerfSilence = (s, ms, kind) => window.silences.push([kind, Math.round(ms)]);
  const _ttsLog = (verb, detail) => window.logs.push(verb + ' ' + detail);
  const _ttsEngineCancel = () => { _ttsNeuralToken += 1; _ttsNeural?.cancel?.(); _ttsNeural = null; };
  const stopTextToSpeech = () => { window.finished = true; };
  let _ttsChunkState = null;
  const clipSeconds = {};
  const _ttsFetchNeural = (text) => {
    window.fetched.push(text);
    if (window.failClip === text) return Promise.reject(500);
    const bytes = silence(clipSeconds[text] || 1);
    const blob = new Blob([bytes], { type: 'audio/mpeg' });
    return Promise.resolve({ blob, body: () => blob.stream(), createUrl: () => URL.createObjectURL(blob),
      voice: 'Kokoro: test', done: Promise.resolve() });
  };
  function _ttsSpeakNextChunk() {
    const state = _ttsChunkState;
    if (!state || state.index >= state.chunks.length) { stopTextToSpeech(); return; }
    if (state.neuralOk === false) { window.gaveUp = true; return; }
    _ttsSpeakChunkNeural(state, state.chunks[state.index]);
  }
  ${wordAt}
  ${player}
  window.speak = (seconds) => {
    const chunks = seconds.map((s, i) => {
      clipSeconds['clip' + i] = s;
      return { text: 'clip' + i, start: i * 6 };
    });
    _ttsChunkState = { chunks, index: 0, paneId: 'p1', neuralOk: true };
    _ttsSpeakNextChunk();
  };
  window.audio = () => _ttsAudio;
  window.readState = () => _ttsChunkState;
  window.stopRead = () => { _ttsEngineCancel(); _ttsChunkState = null; };
  window.block = (ms) => { const end = performance.now() + ms; while (performance.now() < end); };
  window.until = (test, ms) => new Promise((resolve, reject) => {
    const t0 = performance.now();
    (function check() {
      if (test()) return resolve();
      if (performance.now() - t0 > ms) return reject(new Error('timed out: ' + test + ' ' + JSON.stringify(window.logs)));
      setTimeout(check, 20);
    })();
  });
`;

let browser;
before(async () => {
  browser = await puppeteer.launch({ headless: true, executablePath: findChromePath(), args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required'] });
});
after(async () => { await browser.close(); });

async function freshPage(chain) {
  const page = await browser.newPage();
  await page.goto('data:text/html,<!doctype html><title>t</title>');
  await page.evaluate(() => {
    const store = {};
    Object.defineProperty(window, 'localStorage', { value: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } } });
  });
  if (chain === false) await page.evaluate(() => localStorage.setItem('ccc-tts-chain', '0'));
  await page.addScriptTag({ content: harness });
  return page;
}

// Blocks the main thread across the first boundary; returns how far the audio
// clock moved while no JavaScript could run.
const crossBoundaryBlocked = () => page => page.evaluate(async () => {
  speak([0.8, 3, 1]);
  await until(() => audio()?.currentTime > 0.3, 5000);
  const before = audio().currentTime;
  block(1500);
  // currentTime is cached per task: read it from a fresh one.
  await new Promise(r => setTimeout(r, 0));
  return { before, after: audio().currentTime };
});

test('the audio crosses a clip boundary while the main thread is blocked', async () => {
  const page = await freshPage();
  const moved = await crossBoundaryBlocked()(page);
  assert.ok(moved.before < 0.8, 'block began before the boundary: ' + JSON.stringify(moved));
  assert.ok(moved.after > 1.3, 'audio kept playing past the boundary: ' + JSON.stringify(moved));
  await page.close();
});

test('the per-clip player stops at that boundary, so the check above is real', async () => {
  const page = await freshPage(false);
  const moved = await crossBoundaryBlocked()(page);
  assert.ok(moved.after <= 0.85, 'per-clip audio waits for the page: ' + JSON.stringify(moved));
  await page.close();
});

test('a whole read plays every clip once, then finishes with no silence', async () => {
  const page = await freshPage();
  const result = await page.evaluate(async () => {
    speak([0.4, 0.4, 0.4]);
    await until(() => window.finished, 8000);
    return { logs: window.logs, silences: window.silences, fetched: window.fetched, index: readState().index };
  });
  assert.deepEqual(result.fetched, ['clip0', 'clip1', 'clip2']);
  assert.equal(result.index, 3);
  assert.deepEqual(result.silences, []);
  assert.equal(result.logs.filter(l => l.startsWith('PLAY')).length, 3, result.logs.join('\n'));
  assert.ok(result.logs.some(l => l.startsWith('ENDED chained read')));
  await page.close();
});

test('clips are requested only a bounded distance ahead of the listener', async () => {
  const page = await freshPage();
  const fetched = await page.evaluate(async () => {
    speak([30, 30, 30, 30]);
    await until(() => window.fetched.length >= 2 && audio().currentTime > 0, 5000);
    await new Promise(r => setTimeout(r, 1200));
    const seen = window.fetched.slice();
    stopRead();
    return seen;
  });
  assert.deepEqual(fetched, ['clip0', 'clip1']);
  await page.close();
});

test('a clip that fails hands over to the per-clip player at that clip', async () => {
  const page = await freshPage();
  const result = await page.evaluate(async () => {
    window.failClip = 'clip1';
    speak([0.3, 0.3, 0.3]);
    await until(() => window.gaveUp || window.finished, 6000);
    return { logs: window.logs, noChain: readState().noChain, index: readState().index };
  });
  assert.equal(result.noChain, true);
  assert.equal(result.index, 1);
  assert.ok(result.logs.some(l => l.startsWith('CHAIN per-clip from clip 1')), result.logs.join('\n'));
  await page.close();
});

test('a seek inside made audio moves in the stream without a new request', async () => {
  const page = await freshPage();
  const result = await page.evaluate(async () => {
    speak([1, 1, 3]);
    await until(() => window.fetched.length === 3 && audio().currentTime > 0, 5000);
    await new Promise(r => setTimeout(r, 200));
    _ttsNeural.seek(3);   // half way through clip0's 6 characters
    const at = audio().currentTime, fetched = window.fetched.length;
    stopRead();
    return { at, fetched };
  });
  assert.ok(Math.abs(result.at - 0.5) < 0.1, 'seek position: ' + result.at);
  assert.equal(result.fetched, 3);
  await page.close();
});
