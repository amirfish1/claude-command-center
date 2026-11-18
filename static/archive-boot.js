// Start the dashboard's recent-row read while its larger scripts load.
(function () {
  'use strict';
  if (window.self !== window.top) return;
  const params = new URLSearchParams(location.search);
  const app = params.get('ccc_app');
  if (params.get('ccc_popout') === 'conversation' || params.get('popout') === 'conversation'
      || (app && app !== 'sessions' && app !== 'queues')) return;
  let windowName = '7d';
  try {
    const saved = localStorage.getItem('ccc-archive-window');
    if (['8h', '1d', '7d', 'all'].includes(saved)) windowName = saved;
  } catch (_) {}
  const url = '/api/conversations/list?window=' + windowName + '&stale_ok=1&initial=1';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  const start = performance.now();
  const promise = window.fetch(url, {signal: controller.signal})
    .then(response => {
      response._cccArchiveTiming = {start, headers: performance.now()};
      return response;
    })
    .catch(() => null).finally(() => clearTimeout(timeout));
  window.__cccArchiveBootstrap = {url, promise};
})();
