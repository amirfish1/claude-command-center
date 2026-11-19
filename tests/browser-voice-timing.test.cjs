const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/browser-voice-worker.js', 'utf8');
const functions = source.slice(source.indexOf('function* voicePhrases'), source.indexOf('\nself.onmessage ='));

function harness() {
  const messages = [], spoken = [];
  let remaining = 0, chunk = false;
  const c = vm.createContext({Float32Array, performance, current: null, rate: 24000, frameSize: 1920,
    voice: 0, voiceFor: async () => 0, yieldTask: async () => {},
    self: {postMessage: m => messages.push(m)},
    model: {
      start_generation: (_, text) => { spoken.push(text); remaining = spoken.length * 3; chunk = false; },
      next_chunk: () => { if (chunk) return undefined; chunk = true; return 10; },
      generation_step: async () => remaining-- > 0 ? new Float32Array(1920).fill(0.1) : undefined,
      stop_generation() {},
    },
  });
  vm.runInContext(functions, c);
  return {c, messages, spoken};
}

test('phrase offsets preserve Unicode, whitespace and every word across a long read', () => {
  const {c} = harness();
  const text = '  Hello 🌍!\n' + 'A varied phrase with punctuation, numbers 123, and longer words. '.repeat(30);
  c.text = text;
  const phrases = vm.runInContext('[...voicePhrases(text)]', c);
  assert.ok(phrases.length > 20);
  for (const p of phrases) assert.equal(text.slice(p.start, p.end), p.text);
  assert.deepEqual(Array.from(phrases).flatMap(p => p.text.match(/\S+/g)), text.match(/\S+/g));
  assert.ok(phrases.every(p => p.text.length < 120));
});

test('phrase boundaries precede streaming PCM and completed durations add up to the read', async () => {
  const h = harness();
  const sent = [];
  const job = {id: 1, text: 'First phrase. Then a much longer second phrase. Last.', credit: 24000,
    port: {start() {}, close() {}, postMessage(m) {
      sent.push(m);
      if (m.type === 'pcm') queueMicrotask(() => job.port.onmessage({data: {type: 'credit', samples: m.pcm.length}}));
    }}};
  await h.c.generate(job);
  const phrases = sent.filter(m => m.type === 'phrase' && m.to !== null);
  assert.equal(phrases.length, 3);
  assert.deepEqual(phrases.map(p => p.to - p.from), [5760, 11520, 17280]);
  let samples = 0;
  for (const m of sent) {
    if (m.type === 'phrase' && m.to === null) assert.equal(m.from, samples, 'start metadata arrives before its audio');
    if (m.type === 'phrase' && m.to !== null) assert.equal(m.to, samples, 'completion metadata measures emitted audio');
    if (m.type === 'pcm') samples += m.pcm.length;
  }
  assert.equal(sent.at(-1).type, 'end');
  assert.equal(sent.at(-1).samples, samples);
  assert.equal(phrases.at(-1).to, samples);
});

test('cancellation during streaming stops after the current frame and closes the producer', async () => {
  const h = harness();
  let closed = false;
  const sent = [];
  const job = {id: 1, text: 'Cancel this phrase.', credit: 24000,
    port: {start() {}, close() {closed = true;}, postMessage: m => sent.push(m)}};
  h.c.yieldTask = async () => { job.cancelled = true; };
  await h.c.generate(job);
  assert.equal(sent.filter(m => m.type === 'pcm').length, 1);
  assert.equal(sent.some(m => m.type === 'end'), false);
  assert.equal(closed, true);
});
