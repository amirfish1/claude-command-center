const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/browser-voice-worklet.js', 'utf8');

function harness(rate = 1.25) {
  const events = [], credits = [];
  let Player;
  const ctx = vm.createContext({ Float32Array, sampleRate: 48000, currentTime: 0,
    AudioWorkletProcessor: class { constructor() { this.port = {postMessage: m => events.push(m)}; } },
    registerProcessor: (name, ctor) => { Player = ctor; },
  });
  vm.runInContext(source, ctx);
  const p = new Player({processorOptions: {sourceRate: 24000, rate}});
  p.port.onmessage({data: {type: 'connect', port: {start() {}, close() {}, postMessage: d => credits.push(d.samples)}}});
  return {p, events, credits, tick(count = 1) {
    const blocks = [];
    for (let i = 0; i < count; i++) {
      ctx.currentTime += 128 / 48000;
      const samples = new Float32Array(128);
      const running = p.process([], [[samples]]);
      blocks.push({samples, running});
    }
    return blocks;
  }};
}

test('ring stays bounded and credit accounts for exactly consumed source samples', () => {
  const h = harness();
  h.p.receive({type: 'pcm', pcm: new Float32Array(24000).fill(0.5)});
  h.tick(120);
  assert.ok(h.p.count <= 24000 && h.p.count > 0);
  assert.equal(h.credits.reduce((n, x) => n + x, 0) + h.p.credit, h.p.consumed);
  const [block] = h.tick();
  assert.ok([...block.samples].every(x => x === 0.5), 'resampling preserves a constant signal');
  h.p.receive({type: 'pcm', pcm: new Float32Array(24000)});
  assert.equal(h.p.stopped, true);
  assert.equal(h.events.at(-1).type, 'error');
});

test('pause applies backpressure and never advances the read or underrun clock', () => {
  const h = harness();
  h.p.receive({type: 'pcm', pcm: new Float32Array(10000).fill(0.25)});
  h.p.port.onmessage({data: {type: 'pause', paused: true}});
  h.tick(1000);
  assert.equal(h.p.consumed, 0);
  assert.equal(h.p.underruns, 0);
  assert.equal(h.credits.length, 0);
  h.p.port.onmessage({data: {type: 'pause', paused: false}});
  h.tick();
  assert.ok(h.p.consumed > 0);
});

test('short and empty final audio terminate, and silence is not an audible start', () => {
  for (const size of [0, 100]) {
    const h = harness(1);
    h.p.receive({type: 'pcm', pcm: new Float32Array(size)});
    h.p.receive({type: 'end', samples: size});
    assert.equal(h.tick(5).some(b => !b.running), true);
    assert.equal(h.events.some(e => e.type === 'started'), false);
    assert.equal(h.events.some(e => e.type === 'ended'), true);
  }
});

test('underruns retain count, longest gap and total lost audio-clock time', () => {
  const h = harness(1);
  h.p.receive({type: 'pcm', pcm: new Float32Array(4800).fill(0.1)});
  h.tick(200);
  assert.equal(h.p.underruns, 1);
  assert.ok(h.p.maxGapMs > 300);
  h.p.receive({type: 'pcm', pcm: new Float32Array(4800).fill(0.1)});
  h.tick();
  const event = h.events.find(e => e.type === 'underrun');
  assert.ok(event.totalUnderrunMs >= event.maxGapMs);
  assert.ok(event.totalUnderrunMs < 400);
});
