// Deterministic PCM producer for the real AudioWorklet/controller browser tests.
// This tests transport, cancellation and page-stall isolation, not model inference.
let active;
self.onmessage = ({data}) => {
  if (data.type === 'init') self.postMessage({type: 'ready', sampleRate: 24000, threads: 1});
  if (data.type === 'cancel' && active?.id === data.id) active.cancelled = true;
  if (data.type !== 'speak') return;
  const job = active = {...data, credit: 24000, samples: 0};
  const delay = data.text.includes('slow producer') ? 120 : 0;
  const schedule = () => {
    if (job.scheduled || job.cancelled) return;
    job.scheduled = true;
    setTimeout(() => { job.scheduled = false; produce(); }, delay);
  };
  const produce = () => {
    if (job.cancelled) { job.port.close(); return; }
    if (job.credit < 1920) return;
    if (job.samples >= 24000 * 6) { job.port.postMessage({type: 'end', samples: job.samples}); return; }
    const pcm = Float32Array.from({length: 1920}, (_, i) => Math.sin((job.samples + i) / 24000 * Math.PI * 440) * 0.1);
    job.credit -= pcm.length; job.samples += pcm.length;
    job.port.postMessage({type: 'pcm', pcm}, [pcm.buffer]);
    schedule();
  };
  job.port.onmessage = ({data}) => { if (data.type === 'credit') { job.credit += data.samples; schedule(); } };
  job.port.start(); produce();
};
