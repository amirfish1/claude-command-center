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
    if (this.ended && !this.count) { this.report('ended'); this.producer.close(); return false; }
    // Start with 160 ms; a short final utterance may have less.
    if (!this.started && (this.count >= this.sourceRate * 0.16 || (this.ended && this.count))) this.started = true;
    if (!this.started) return true;
    let missing = 0;
    for (let i = 0; i < output.length; i++) {
      if (this.count < 2 && !this.ended) { missing++; continue; }
      if (!this.count) {
        if (this.ended) { this.report('ended'); this.producer.close(); return false; }
        missing++; continue;
      }
      const a = this.ring[this.read], b = this.count > 1 ? this.ring[(this.read + 1) % this.ring.length] : a;
      const sample = a + (b - a) * this.fraction;
      output[i] = sample;
      if (!this.audible && Math.abs(sample) >= 0.0001) { this.audible = true; this.report('started'); }
      this.fraction += this.sourceRate / sampleRate * this.speed;
      const step = Math.min(this.count, Math.floor(this.fraction));
      this.fraction -= step;
      this.read = (this.read + step) % this.ring.length;
      this.count -= step; this.consumed += step; this.credit += step;
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
