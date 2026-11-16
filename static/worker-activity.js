// Only measured model usage belongs in this rollup; idle processes do not.
(function initWorkerActivity() {
  'use strict';
  const sidebar = document.querySelector('.sidebar');
  const header = sidebar?.querySelector('.sidebar-header');
  if (!header) return;
  const line = document.createElement('div');
  line.className = 'worker-activity-line';
  line.setAttribute('aria-label', 'Worker token activity');
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
    const lead = span('worker-activity-label', 'Workers: ');
    lead.insertAdjacentHTML('afterbegin', icon);
    const parts = [lead];
    if (!snapshot || snapshot.pending || !fresh) {
      parts.push(span('worker-activity-muted', failed || snapshot && !snapshot.pending ? 'Activity unavailable' : 'Checking token activity…'));
      line.replaceChildren(...parts);
      line.title = 'Waiting for a recent worker usage snapshot. Idle processes are never counted as active.';
      return;
    }
    const now = Date.now() / 1000;
    const rows = (snapshot.workers || []).filter(w => Number.isFinite(w.last_token_at)
      && w.last_token_at <= now + 5 && w.last_token_at >= now - 300);
    const active = rows.filter(w => w.last_token_at >= now - 60).length;
    parts.push(span('worker-activity-now', active + ' now', 'Workers with a token receipt in the last 60 seconds'));
    parts.push(span('worker-activity-recent', '+ ' + (rows.length - active) + ' recent', 'Additional workers with a token receipt 1–5 minutes ago'));
    const groups = new Map();
    for (const worker of rows) {
      const key = worker.repo_path || worker.queue || 'Unknown repo';
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
      const title = [repo, workers.length + ' workers with usage in 5m',
        total ? done + '/' + total + ' reported plan steps complete (' + plans.length + '/' + workers.length + ' workers report a plan). This measures checklist steps, not time remaining or ticket completion.'
          : 'Task progress unavailable: these workers have no reported checklist in their recent transcript.',
        ...workers.map(w => [w.queue, w.engine, w.worker_id].filter(Boolean).join(' · '))].join('\n');
      parts.push(span('worker-activity-repo', name + ' ' + workers.length + progress, title));
    }
    if (!rows.length) parts.push(span('worker-activity-muted', 'No measured usage in 5m'));
    line.replaceChildren(...parts);
    const unmeasured = (snapshot.workers || []).filter(w => !Number.isFinite(w.last_token_at)).length;
    line.title = 'Measured token receipts: now = last 60s; recent = 1–5m. Refreshed every 10s. Tool output and process uptime do not count.'
      + (unmeasured ? '\n' + unmeasured + ' live workers have no measurable usage in the bounded transcript tail; excluded from counts.' : '');
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
