// Audio-thread PCM ring: no shared memory, page timers, or unbounded frame queue.
class BrowserVoicePlayer extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = options.processorOptions;
    this.sourceRate = opts.sourceRate;
    this.speed = Math.max(0.5, Math.min(2, opts.rate || 1));
    this.ring = new Float32Array(this.sourceRate); // Hard limit: one source second.
    this.read = 0; this.write = 0; this.count = 0; this.fraction = 0;
    this.consumed = 0; this.credit = 0; this.generated = 0;
    this.paused = !!opts.paused; this.started = false; this.audible = false;
    this.ended = false; this.stopped = false; this.waiting = 0; this.gapSamples = 0;
    this.maxGapMs = 0; this.underruns = 0; this.maxBuffered = 0; this.tick = 0;
    this.totalMissing = 0;
    // Speed changes use WSOLA time-stretching at the source rate, so pitch is preserved.
    // 1x bypasses it entirely and keeps the original sample path.
    if (this.speed !== 1) {
      this.frame = 960; this.hop = 480; this.delta = 192; // 40 ms frames, 20 ms hop, 8 ms search
      this.window = new Float32Array(this.frame);
      for (let k = 0; k < this.frame; k++) this.window[k] = 0.5 - 0.5 * Math.cos(2 * Math.PI * k / this.frame);
      this.acc = new Float32Array(this.frame);
      this.fifo = new Float32Array(4096); this.fifoRead = 0; this.fifoCount = 0;
      this.pos = 0; this.prev = -1; this.stretchDone = false;
    }
    this.port.onmessage = ({ data }) => {
      if (data.type === 'connect') {
        this.producer = data.port;
        this.producer.onmessage = event => this.receive(event.data);
        this.producer.start();
      } else if (data.type === 'pause') this.paused = data.paused;
      else if (data.type === 'stop') { this.stopped = true; this.producer?.close(); }
    };
  }
  receive(data) {
    if (data.type === 'end') { this.ended = true; this.generated = data.samples; return; }
    if (data.type !== 'pcm' || this.stopped) return;
    if (!(data.pcm instanceof Float32Array) || data.pcm.length > this.ring.length - this.count) {
      this.port.postMessage({ type: 'error', message: 'Voice PCM ring overflow' });
      this.stopped = true; return;
    }
    for (const sample of data.pcm) { this.ring[this.write] = sample; this.write = (this.write + 1) % this.ring.length; }
    this.count += data.pcm.length;
    this.maxBuffered = Math.max(this.maxBuffered, this.count);
  }
  at(j) { return j < this.count ? this.ring[(this.read + j) % this.ring.length] : 0; }
  release(n) {
    n = Math.min(n, this.count);
    if (n <= 0) return;
    this.read = (this.read + n) % this.ring.length;
    this.count -= n; this.consumed += n; this.credit += n;
    this.pos -= n; if (this.prev >= 0) this.prev -= n;
  }
  // Produce one WSOLA hop into the FIFO if enough input is buffered. Returns false when it must wait.
  stretchStep() {
    if (this.stretchDone || this.fifo.length - this.fifoCount < this.hop) return false;
    const N = this.frame, H = this.hop, D = this.delta;
    const nominal = Math.round(this.pos);
    if (this.ended && nominal >= this.count) {
      for (let k = 0; k < H; k++) this.push(this.acc[k]);
      this.acc.fill(0); this.stretchDone = true; this.release(this.count); return true;
    }
    if (!this.ended && nominal + D + N > this.count) return false;
    let start = nominal;
    if (this.prev >= 0) {
      // Pick the offset whose start best continues the previous frame's natural waveform.
      const ref = this.prev + H, L = N - H;
      let best = -Infinity;
      for (let d = -D; d <= D; d++) {
        const c = nominal + d;
        if (c < 0) continue;
        let dot = 0, energy = 1e-9;
        for (let k = 0; k < L; k += 2) { const x = this.at(c + k); dot += x * this.at(ref + k); energy += x * x; }
        const score = dot / Math.sqrt(energy);
        if (score > best) { best = score; start = c; }
      }
    }
    for (let k = 0; k < N; k++) this.acc[k] += this.window[k] * this.at(start + k);
    for (let k = 0; k < H; k++) this.push(this.acc[k]);
    this.acc.copyWithin(0, H); this.acc.fill(0, N - H);
    this.prev = start; this.pos += H * this.speed;
    this.release(Math.min(this.prev + H, Math.floor(this.pos) - D));
    return true;
  }
  push(sample) { this.fifo[(this.fifoRead + this.fifoCount) % this.fifo.length] = sample; this.fifoCount++; }
  report(type) {
    this.port.postMessage({ type, played: this.consumed / this.sourceRate,
      audioTime: currentTime,
      duration: this.ended ? this.generated / this.sourceRate : null,
      underruns: this.underruns, maxGapMs: this.maxGapMs,
      totalUnderrunMs: this.totalMissing / sampleRate * 1000,
      maxBufferedMs: this.maxBuffered / this.sourceRate * 1000 });
  }
  process(inputs, outputs) {
    if (this.stopped) return false;
    const output = outputs[0][0];
    if (this.paused || !this.producer) return true;
    if (this.ended && !this.count && !(this.fifo && (this.fifoCount || !this.stretchDone))) { this.report('ended'); this.producer.close(); return false; }
    // Start with 160 ms; a short final utterance may have less.
    if (!this.started && (this.count >= this.sourceRate * 0.16 || (this.ended && (this.count || this.fifo)))) this.started = true;
    if (!this.started) return true;
    let missing = 0;
    const stretch = !!this.fifo;
    for (let i = 0; i < output.length; i++) {
      if (stretch) while (this.fifoCount < 2 && this.stretchStep()) {}
      const avail = stretch ? this.fifoCount : this.count;
      const final = stretch ? this.stretchDone : this.ended;
      if (avail < 2 && !final) { missing++; continue; }
      if (!avail) {
        if (final) { this.report('ended'); this.producer.close(); return false; }
        missing++; continue;
      }
      const buf = stretch ? this.fifo : this.ring, at = stretch ? this.fifoRead : this.read;
      const a = buf[at], b = avail > 1 ? buf[(at + 1) % buf.length] : a;
      const sample = a + (b - a) * this.fraction;
      output[i] = sample;
      if (!this.audible && Math.abs(sample) >= 0.0001) { this.audible = true; this.report('started'); }
      this.fraction += this.sourceRate / sampleRate * (stretch ? 1 : this.speed);
      const step = Math.min(avail, Math.floor(this.fraction));
      this.fraction -= step;
      if (stretch) { this.fifoRead = (this.fifoRead + step) % this.fifo.length; this.fifoCount -= step; }
      else {
        this.read = (this.read + step) % this.ring.length;
        this.count -= step; this.consumed += step; this.credit += step;
      }
    }
    if (this.credit >= this.sourceRate * 0.08) {
      this.producer.postMessage({ type: 'credit', samples: this.credit }); this.credit = 0;
    }
    if (missing) {
      this.totalMissing += missing;
      if (!this.waiting) this.underruns++;
      this.waiting += missing;
      this.maxGapMs = Math.max(this.maxGapMs, this.waiting / sampleRate * 1000);
    } else if (this.waiting) { this.report('underrun'); this.waiting = 0; }
    if (++this.tick >= Math.max(1, Math.floor(sampleRate / output.length / 10))) {
      this.tick = 0; this.report('position');
    }
    return true;
  }
}
registerProcessor('ccc-browser-voice', BrowserVoicePlayer);
