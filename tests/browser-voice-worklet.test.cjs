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

function playSine(rate, hz = 200, seconds = 0.9) {
  const h = harness(rate);
  const n = Math.round(24000 * seconds);
  const pcm = new Float32Array(n);
  for (let i = 0; i < n; i++) pcm[i] = 0.5 * Math.sin(2 * Math.PI * hz * i / 24000);
  h.p.receive({type: 'pcm', pcm});
  h.p.receive({type: 'end', samples: n});
  const out = [];
  for (let i = 0; i < 2000; i++) {
    const [block] = h.tick();
    out.push(...block.samples);
    if (!block.running) break;
  }
  return {h, out, n};
}

test('speed changes keep pitch: a 200 Hz tone stays 200 Hz and only duration scales', () => {
  for (const rate of [0.75, 1.5, 2]) {
    const {h, out, n} = playSine(rate);
    assert.equal(h.events.some(e => e.type === 'ended'), true, `rate ${rate} ends`);
    // Measure pitch in the steady middle of the output via zero crossings.
    const mid = out.slice(Math.floor(out.length * 0.25), Math.floor(out.length * 0.75));
    let crossings = 0;
    for (let i = 1; i < mid.length; i++) if ((mid[i - 1] < 0) !== (mid[i] < 0)) crossings++;
    const hz = crossings / 2 / (mid.length / 48000);
    assert.ok(Math.abs(hz - 200) < 8, `rate ${rate}: measured ${hz.toFixed(1)} Hz`);
    const expected = n / 24000 / rate, actual = out.length / 48000;
    assert.ok(Math.abs(actual - expected) < 0.1, `rate ${rate}: ${actual.toFixed(3)}s vs ${expected.toFixed(3)}s`);
    assert.equal(h.p.consumed, n, 'every source sample is consumed and credited');
    assert.equal(h.credits.reduce((s, x) => s + x, 0) + h.p.credit, h.p.consumed);
  }
});

test('stretched playback handles empty and short final audio', () => {
  for (const size of [0, 100]) {
    const h = harness(1.5);
    h.p.receive({type: 'pcm', pcm: new Float32Array(size)});
    h.p.receive({type: 'end', samples: size});
    assert.equal(h.tick(20).some(b => !b.running), true);
    assert.equal(h.events.some(e => e.type === 'ended'), true);
  }
});

test('stretching aligns frames so a gliding voiced tone keeps one clear pitch (no doubling)', () => {
  // Buzzy 100->160 Hz glide, like a low speaking voice. Misaligned overlap-add makes two
  // pitches sound at once and collapses periodicity; aligned WSOLA keeps it.
  const n = 24000;
  const pcm = new Float32Array(n);
  let phase = 0;
  for (let i = 0; i < n; i++) {
    phase += 2 * Math.PI * (100 + 60 * i / n) / 24000;
    for (let h = 1; h <= 8; h++) pcm[i] += 0.3 / h * Math.sin(h * phase);
  }
  const periodicity = x => {
    const N = 1920, vals = [];
    for (let s = 4800; s + N + 1000 < x.length - 4800; s += 1920) {
      let best = 0;
      for (let lag = 240; lag <= 1000; lag += 2) {
        let d = 0, e1 = 0, e2 = 0;
        for (let k = 0; k < N; k += 2) { const a = x[s + k], b = x[s + k + lag]; d += a * b; e1 += a * a; e2 += b * b; }
        best = Math.max(best, d / Math.sqrt(e1 * e2 + 1e-12));
      }
      vals.push(best);
    }
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  };
  const play = rate => {
    const h = harness(rate);
    h.p.receive({type: 'pcm', pcm: pcm.slice(0, 20000)});
    const out = [];
    for (let i = 0; i < 4000; i++) {
      if (h.p.count < 4000 && !h.p.ended) { h.p.receive({type: 'pcm', pcm: pcm.slice(20000)}); h.p.receive({type: 'end', samples: n}); }
      const [block] = h.tick(); out.push(...block.samples); if (!block.running) break;
    }
    return Float32Array.from(out);
  };
  const ref = periodicity(play(1));
  for (const rate of [1.5, 2]) {
    const out = periodicity(play(rate));
    assert.ok(out > ref - 0.06, `rate ${rate}: periodicity ${out.toFixed(3)} vs 1x ${ref.toFixed(3)}`);
  }
});

test('highlight timing follows the current phrase in source audio seconds at every rate', () => {
  for (const rate of [0.75, 1, 1.5, 2]) {
    const h = harness(rate);
    h.p.receive({type: 'phrase', start: 0, end: 20, from: 0, to: 4800});
    h.p.receive({type: 'phrase', start: 22, end: 60, from: 4800, to: 14400});
    h.p.receive({type: 'pcm', pcm: new Float32Array(14400).fill(0.2)});
    h.p.receive({type: 'end', samples: 14400});
    h.tick(200);
    const reports = h.events.filter(e => e.type === 'position');
    assert.ok(reports.some(e => e.textStart === 0));
    assert.ok(reports.some(e => e.textStart === 22));
    for (const report of reports) {
      assert.equal(report.phraseDuration, report.textStart === 0 ? 0.2 : 0.4);
      assert.ok(Math.abs(report.phrasePlayed - (report.played - (report.textStart === 0 ? 0 : 0.2))) < 1e-9);
    }
    h.p.port.onmessage({data: {type: 'pause', paused: true}});
    const count = h.events.length;
    h.tick(50);
    assert.equal(h.events.length, count, 'paused audio never moves the highlight');
  }
});

test('streamed phrase duration updates replace estimates without queuing duplicate phrases', () => {
  const h = harness(1);
  h.p.receive({type: 'phrase', start: 0, end: 60, from: 0, to: null});
  h.p.receive({type: 'pcm', pcm: new Float32Array(12000).fill(0.1)});
  h.tick(40);
  assert.equal(h.events.at(-1).phraseDuration, 4);
  h.p.receive({type: 'phrase', start: 0, end: 60, from: 0, to: 24000});
  h.tick(40);
  assert.equal(h.events.at(-1).phraseDuration, 1);
  assert.equal(h.p.phrases.length, 1);
  h.p.receive({type: 'phrase', start: 61, end: 91, from: 24000, to: null});
  assert.equal(h.p.charsPerSecond, 60, 'the next estimate uses this voice’s measured pace');
});
