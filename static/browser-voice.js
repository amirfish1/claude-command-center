// Optional listener-side English voice. Off until this browser explicitly opts in.
(() => {
  'use strict';
  const key = 'ccc-browser-voice-pilot';
  let enabled = false;
  try { enabled = localStorage.getItem(key) === '1'; } catch (_) {}
  let worker, context, loading, ready = false, epoch = 0, nextId = 0, active = null;
  let message = enabled ? 'Preparing browser voice…' : 'Off. Your current voice is used.';
  const emit = detail => window.dispatchEvent(new CustomEvent('ccc:browser-voice', { detail }));
  function paint() {
    const toggle = document.getElementById('settingsBrowserVoiceToggle');
    if (toggle) { toggle.classList.toggle('is-on', enabled); toggle.setAttribute('aria-checked', String(enabled)); }
    const status = document.getElementById('settingsBrowserVoiceStatus');
    if (status) status.textContent = message;
  }
  function cancel() {
    const job = active;
    active = null;
    if (!job) return;
    clearTimeout(job.timer);
    worker?.postMessage({ type: 'cancel', id: job.id });
    job.node?.port.postMessage({ type: 'stop' });
    job.node?.disconnect();
    job.ontelemetry?.({ ...job.last, cancelled: true });
    emit({ type: 'cancelled', ...job.last, cancelled: true });
  }
  function fail(error) {
    const job = active;
    cancel();
    ready = false;
    enabled = false;
    try { localStorage.setItem(key, '0'); } catch (_) {}
    worker?.terminate(); worker = null;
    if (context) { context.close().catch(() => {}); context = null; }
    message = 'Browser voice stopped: ' + String(error.message || error).slice(0, 160)
      + '. Using your current voice. Switch on to retry.';
    paint(); emit({ type: 'failure', message });
    job?.onerror?.(error);
  }
  function audioContext() {
    if (!context) {
      const Constructor = window.AudioContext || window.webkitAudioContext;
      if (!Constructor || !window.AudioWorkletNode || !window.isSecureContext) throw new Error('AudioWorklet needs a secure, supported browser');
      context = new Constructor();
    }
    return context;
  }
  async function warm() {
    if (!enabled || ready) return;
    try {
      if (localStorage.getItem('ccc-tts-enabled') === '0') {
        message = 'Enable Text-to-speech to prepare browser voice.'; paint(); return;
      }
    } catch (_) {}
    if (loading) return loading;
    const generation = epoch;
    loading = (async () => {
      const ctx = audioContext();
      await ctx.audioWorklet.addModule('/static/browser-voice-worklet.js');
      let response = await fetch('/api/browser-voice/status', { cache: 'no-store', cccPriority: 'user' });
      if (!response.ok) throw new Error('Voice assets are unavailable');
      let status = await response.json();
      if (!status.installed) {
        response = await fetch('/api/browser-voice/install', { method: 'POST', cccPriority: 'user' });
        if (!response.ok) throw new Error('Voice download could not start');
        for (let attempt = 0; attempt < 600; attempt++) {
          if (!enabled || epoch !== generation) return;
          response = await fetch('/api/browser-voice/status', { cache: 'no-store', cccPriority: 'user' });
          if (!response.ok) throw new Error('Voice download status unavailable');
          status = await response.json();
          if (status.installed) break;
          if (status.phase === 'error') throw new Error(status.error || 'Voice download failed');
          message = 'Downloading browser voice (~65 MB). Your current voice is available.'; paint();
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
      }
      if (!enabled || epoch !== generation) return;
      if (!status.installed) throw new Error('Voice download timed out');
      await new Promise((resolve, reject) => {
        worker = new Worker('/static/browser-voice-worker.js', { type: 'module' });
        const initWorker = worker;
        const timer = setTimeout(() => reject(new Error('Voice model initialization timed out')), 60000);
        worker.onerror = event => { clearTimeout(timer); const error = new Error(event.message || 'Voice worker failed'); reject(error); if (ready) fail(error); };
        worker.onmessage = ({ data }) => {
          if (worker !== initWorker || epoch !== generation) return;
          if (data.type === 'ready') { clearTimeout(timer); resolve(); }
          else if (data.type === 'error') {
            clearTimeout(timer);
            const error = new Error(data.message);
            if (ready && (!data.id || data.id === active?.id)) fail(error);
            else reject(error);
          } else if ((data.type === 'generated' || data.type === 'first-pcm') && data.id === active?.id) {
            active.last = { ...active.last, ...data };
            emit(data);
          }
        };
        worker.postMessage({ type: 'init', manifest: status.manifest });
      });
      if (!enabled || epoch !== generation) return;
      ready = true; message = 'Ready. English reads use the browser voice on this device.';
      paint(); emit({ type: 'ready', pack: status.manifest.pack, threads: 1 });
    })().catch(error => { if (enabled && epoch === generation) fail(error); })
      .finally(() => { if (epoch === generation) loading = null; });
    return loading;
  }
  function start(text, options = {}) {
    if (!enabled || !ready || !text.trim()) return null;
    cancel();
    const ctx = audioContext();
    const job = active = { ...options, id: ++nextId, last: {}, paused: !!options.paused };
    const id = job.id;
    const stale = () => active !== job;
    const arm = () => {
      clearTimeout(job.timer);
      if (!job.paused) job.timer = setTimeout(() => { if (!stale()) fail(new Error('Voice could not sustain the bounded audio buffer')); }, 2500);
    };
    // Resume immediately in the speaker's user gesture; never auto-play on opt-in.
    Promise.resolve(ctx.resume()).then(() => {
      if (stale()) return;
      const node = job.node = new AudioWorkletNode(ctx, 'ccc-browser-voice', {
        numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1],
        processorOptions: { sourceRate: 24000, rate: options.rate || 1, paused: job.paused },
      });
      const channel = new MessageChannel();
      node.onprocessorerror = () => { if (!stale()) fail(new Error('Voice audio processor failed')); };
      node.port.onmessage = ({ data }) => {
        if (stale()) return;
        job.last = { ...job.last, ...data };
        if (data.type === 'error') { fail(new Error(data.message)); return; }
        if (data.totalUnderrunMs > 500 || data.maxGapMs > 2000) {
          job.ontelemetry?.(data);
          fail(new Error('Voice cannot sustain playback on this device')); return;
        }
        if (data.type === 'started') { job.started = true; job.onstart?.({ ...data, lagMs: Math.max(0, (ctx.currentTime - data.audioTime) * 1000) }); }
        if (data.type === 'position') { job.onposition?.(data); if (data.played > (job.lastPlayed || 0)) arm(); job.lastPlayed = data.played; }
        if (data.type === 'underrun') { job.ontelemetry?.(data); emit(data); }
        if (data.type === 'ended') {
          if (!job.started) { fail(new Error('Voice produced no audible samples')); return; }
          clearTimeout(job.timer); node.disconnect(); active = null;
          job.ontelemetry?.({ ...job.last, completed: true }); emit({ ...job.last, completed: true }); job.onend?.();
        }
      };
      node.port.postMessage({ type: 'connect', port: channel.port1 }, [channel.port1]);
      node.connect(ctx.destination);
      worker.postMessage({ type: 'speak', id, text, port: channel.port2 }, [channel.port2]);
      arm();
    }).catch(error => { if (!stale()) fail(error); });
    return {
      cancel,
      pause() { if (!stale()) { job.paused = true; clearTimeout(job.timer); job.node?.port.postMessage({ type: 'pause', paused: true }); } },
      resume() { if (!stale()) { job.paused = false; ctx.resume().catch(error => fail(error)); job.node?.port.postMessage({ type: 'pause', paused: false }); arm(); } },
    };
  }
  function setEnabled(value) {
    const interrupted = active;
    epoch++; cancel(); ready = false; loading = null;
    worker?.terminate(); worker = null;
    enabled = !!value;
    try { localStorage.setItem(key, enabled ? '1' : '0'); } catch (_) {}
    message = enabled ? 'Preparing browser voice. Your current voice is available.' : 'Off. Your current voice is used.';
    paint();
    if (enabled) warm();
    else if (context) { context.close().catch(() => {}); context = null; }
    interrupted?.onerror?.(new Error('Browser voice setting changed'));
  }
  function suspend() {
    epoch++; cancel(); ready = false; loading = null;
    worker?.terminate(); worker = null;
    if (context) { context.close().catch(() => {}); context = null; }
    if (enabled) { message = 'Enable Text-to-speech to prepare browser voice.'; paint(); }
  }
  document.getElementById('settingsBrowserVoiceToggle')?.addEventListener('click', () => setEnabled(!enabled));
  window.addEventListener('pagehide', cancel);
  window.cccBrowserVoice = { start, cancel, warm, setEnabled, suspend,
    get ready() { return ready && enabled; }, get enabled() { return enabled; },
    get status() { return message; } };
  paint();
  if (enabled) warm();
})();
