// Live workers grouped by queue or working folder.
(function initWorkerActivity() {
  'use strict';
  const sidebar = document.querySelector('.sidebar');
  const header = sidebar?.querySelector('.sidebar-header');
  if (!header) return;
  const line = document.createElement('div');
  line.className = 'worker-activity-line';
  line.setAttribute('aria-label', 'Live workers by queue');
  line.tabIndex = 0;
  const icon = '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M1 8h3l2-5 4 10 2-5h3"/></svg>';
  let snapshot = null, busy = false, failed = false;
  const query = matchMedia('(max-width: 1200px)');
  function mount() {
    const simple = query.matches && document.body.classList.contains('ccc-simple-mode')
      && document.getElementById('mobileSimpleHeader');
    const destination = simple || header;
    if (line.parentNode !== destination) destination.append(line);
  }
  function span(className, text, title) {
    const el = document.createElement('span');
    el.className = className;
    el.textContent = text;
    if (title) el.title = title;
    return el;
  }
  function render() {
    mount();
    const fresh = snapshot && Number.isFinite(snapshot.sampled_at)
      && Date.now() / 1000 - snapshot.sampled_at < 45;
    const lead = span('worker-activity-label', 'Workers (all queues): ');
    lead.insertAdjacentHTML('afterbegin', icon);
    const parts = [lead];
    if (!snapshot || snapshot.pending || !fresh) {
      parts.push(span('worker-activity-muted', failed || snapshot && !snapshot.pending ? 'Activity unavailable' : 'Checking workers…'));
      line.replaceChildren(...parts);
      line.title = 'Waiting for a recent worker usage snapshot. Live workers include those without measured token usage.';
      return;
    }
    const workers = snapshot.workers || [];
    parts.push(span('worker-activity-live', workers.length + ' live', 'Live workers on this machine, including workers without measured token usage'));
    const groups = new Map();
    for (const worker of workers) {
      const key = worker.queue || worker.repo_path || 'Unknown queue';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(worker);
    }
    for (const [repo, workers] of [...groups].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))) {
      const name = repo.replace(/[\\/]+$/, '').split(/[\\/]/).pop();
      const plans = workers.map(w => w.plan).filter(p => p && Number.isInteger(p.done)
        && Number.isInteger(p.total) && p.total > 0 && p.done >= 0 && p.done <= p.total);
      const done = plans.reduce((n, p) => n + p.done, 0);
      const total = plans.reduce((n, p) => n + p.total, 0);
      const progress = total ? ' · plan ' + Math.round(100 * done / total) + '%' : '';
      const title = [repo, workers.length + ' live workers across this queue or working folder',
        total ? done + '/' + total + ' reported plan steps complete (' + plans.length + '/' + workers.length + ' workers report a plan). This measures checklist steps, not time remaining or ticket completion.'
          : 'Task progress unavailable: these workers have no reported checklist in their recent transcript.',
        ...workers.map(w => [w.queue, w.engine, w.worker_id].filter(Boolean).join(' · '))].join('\n');
      parts.push(span('worker-activity-repo', name + ' ' + workers.length + progress, title));
    }
    line.replaceChildren(...parts);
    line.title = 'All queues on this machine. Live counts include every worker in the Workers tab; ticket rows are not a worker count. Refreshed every 10s.';
  }
  async function refresh() {
    if (document.hidden || busy) return;
    busy = true;
    try {
      const response = await fetch('/api/wt/activity', {cache:'no-store', cccPriority:'user', signal:AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error('Worker activity unavailable');
      snapshot = await response.json();
      failed = false;
    } catch (_) { failed = true; }
    finally { busy = false; render(); }
  }
  line.addEventListener('wheel', event => {
    if (line.scrollWidth <= line.clientWidth || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    line.scrollLeft += event.deltaY;
    event.preventDefault();
  }, {passive:false});
  query.addEventListener('change', mount);
  new MutationObserver(mount).observe(document.body, {attributes:true, attributeFilter:['class']});
  document.addEventListener('visibilitychange', refresh);
  render();
  refresh();
  setInterval(refresh, 10000);
  setInterval(render, 5000);
})();
