// Optional Phonon 31M pilot. Inference and flow control never run on the page.
let model, voice, rate, frameSize, manifest;
let current = null, queue = Promise.resolve();
const cacheName = 'ccc-browser-voice-v1';
const yieldChannel = new MessageChannel();
let yieldResolve;
yieldChannel.port1.onmessage = () => { const resolve = yieldResolve; yieldResolve = null; resolve?.(); };
const yieldTask = () => new Promise(resolve => { yieldResolve = resolve; yieldChannel.port2.postMessage(0); });

async function asset(file) {
  const spec = manifest.files[file];
  const url = new URL(spec.url, self.location.origin).href;
  let cache;
  try { cache = await caches.open(cacheName); } catch (_) {}
  let response = await cache?.match(url);
  let cached = !!response;
  if (!response) { self.postMessage({ type: 'downloading', file }); response = await fetch(url); }
  if (!response.ok) throw new Error('Voice asset unavailable: ' + file);
  let bytes = await response.arrayBuffer();
  const valid = async value => value.byteLength === spec.size
    && Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', value)),
      n => n.toString(16).padStart(2, '0')).join('') === spec.sha256;
  if (!await valid(bytes)) {
    await cache?.delete(url);
    if (!cached) throw new Error('Voice asset integrity check failed: ' + file);
    response = await fetch(url, { cache: 'reload' });
    bytes = await response.arrayBuffer();
    if (!response.ok || !await valid(bytes)) throw new Error('Voice asset integrity check failed: ' + file);
    cached = false;
  }
  if (!cached) { try { await cache?.put(url, new Response(bytes)); } catch (_) {} }
  self.postMessage({ type: 'progress', file, cached });
  return new Uint8Array(bytes);
}

async function init(spec) {
  manifest = spec;
  const base = new URL(spec.base + 'runtime/', self.location.origin);
  const { hasRelaxedSimd } = await import(new URL('simd.js', base));
  const wasm = await import(new URL('wasm/phonon_tts.js', base));
  // Both choices are single-threaded. In particular, never request wasm-threads.
  await wasm.default({ module_or_path: new URL('wasm/phonon_tts'
    + (hasRelaxedSimd() ? '_bg.wasm' : '_simd_bg.wasm'), base) });
  const weights = await asset('model.q8.gguf');
  const tokenizer = await asset('tokenizer.json');
  const config = await asset('config.json');
  model = await wasm.Model.load(weights, tokenizer, config, 'q8', 'en', undefined, [], 'cpu');
  voice = model.add_voice(await asset('default-voice.safetensors'));
  await model.flush();
  rate = model.sample_rate();
  frameSize = model.frame_size();
  self.postMessage({ type: 'ready', sampleRate: rate, frameSize, threads: 1, pack: spec.pack });
}

async function generate(job) {
  current = job;
  let produced = 0, first = null;
  const t0 = performance.now();
  job.port.onmessage = ({ data }) => {
    if (data.type === 'credit') {
      // Credit comes only from the AudioWorklet as it consumes the bounded ring.
      job.credit = Math.min(rate, job.credit + Math.max(0, Number(data.samples) || 0));
      job.wake?.(); job.wake = null;
    }
  };
  job.port.start();
  try {
    model.start_generation(voice, job.text, 0.3, 4242424242424242n);
    outer: while (!job.cancelled) {
      if (model.next_chunk() === undefined) break;
      while (!job.cancelled) {
        while (job.credit < frameSize && !job.cancelled) await new Promise(resolve => { job.wake = resolve; });
        if (job.cancelled) break outer;
        const pcm = await model.generation_step();
        if (!pcm) break;
        if (job.cancelled) break outer;
        if (pcm.length > frameSize || pcm.length > job.credit) throw new Error('Voice frame exceeds buffer credit');
        if (first === null) {
          first = performance.now() - t0;
          self.postMessage({ type: 'first-pcm', id: job.id, firstPcmMs: first });
        }
        produced += pcm.length;
        job.credit -= pcm.length;
        job.port.postMessage({ type: 'pcm', pcm }, [pcm.buffer]);
        // Let cancellation/credits arrive between synchronous inference steps.
        await yieldTask();
      }
    }
    if (!job.cancelled) job.port.postMessage({ type: 'end', samples: produced });
    self.postMessage({ type: 'generated', id: job.id, cancelled: !!job.cancelled,
      samples: produced, firstPcmMs: first, totalMs: performance.now() - t0, sampleRate: rate });
  } catch (error) {
    if (!job.cancelled) self.postMessage({ type: 'error', id: job.id, message: String(error.message || error) });
  } finally {
    try { model.stop_generation(); } catch (_) {}
    job.port.close();
    if (current === job) current = null;
  }
}

self.onmessage = ({ data }) => {
  if (data.type === 'init') {
    init(data.manifest).catch(error => self.postMessage({ type: 'error', message: String(error.message || error) }));
  } else if (data.type === 'cancel') {
    if (current?.id === data.id) { current.cancelled = true; current.wake?.(); }
    for (const job of pending) if (job.id === data.id) { job.cancelled = true; job.port.close(); }
  } else if (data.type === 'speak' && model) {
    const job = { ...data, credit: rate, cancelled: false };
    pending.add(job);
    queue = queue.then(async () => {
      pending.delete(job);
      if (!job.cancelled) await generate(job);
    }).catch(error => self.postMessage({ type: 'error', id: job.id, message: String(error) }));
  }
};
const pending = new Set();
