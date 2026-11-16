// Open an app independently of the dashboard's styles, scripts and saved rail.
(function () {
  'use strict';
  if (window.self !== window.top) return;
  const params = new URLSearchParams(location.search);
  const id = params.get('ccc_app');
  if (!id || id === 'sessions' || id === 'queues'
      || params.has('ccc_popout') || params.has('popout')) return;
  let finish;
  window.__cccAppFrameReady = new Promise(resolve => { finish = resolve; });
  const originalFetch = window.fetch;
  let release;
  let released = false;
  const dashboardReady = new Promise(resolve => { release = resolve; });
  const timer = setTimeout(releaseDashboard, 5000);
  function releaseDashboard() {
    if (released) return;
    released = true;
    clearTimeout(timer);
    if (window.fetch === heldFetch) window.fetch = originalFetch;
    release();
  }
  function heldFetch(input, init) {
    let hold = false;
    try {
      const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.origin);
      const method = String(init?.method || input?.method || 'GET').toUpperCase();
      hold = !released && method === 'GET' && url.origin === location.origin
        && url.pathname.startsWith('/api/') && url.pathname !== '/api/apps';
    } catch (_) {}
    if (hold) return dashboardReady.then(() => originalFetch.call(window, input, init));
    return originalFetch.call(window, input, init);
  }
  window.fetch = heldFetch;
  window.__cccAppFrameReady.then(releaseDashboard, releaseDashboard);
  window.__cccDeferDashboardBoot = callback => dashboardReady.then(() => {
    delete window.__cccDeferDashboardBoot;
    callback();
    window.dispatchEvent(new Event('ccc:dashboard-ready'));
  });
  function open(app) {
    if (!app || app.enabled === false) { finish(); return; }
    let url;
    try { url = new URL(app.nav || app.url, location.origin); }
    catch (_) { finish(); return; }
    if (url.origin !== location.origin || url.pathname === '/') { finish(); return; }
    if (document.getElementById('cccMobileAppFrame')) return;
    const frame = document.createElement('iframe');
    frame.id = 'cccMobileAppFrame';
    frame.title = app.label || id;
    frame.dataset.appUrl = url.pathname + url.search + url.hash;
    frame.dataset.early = '1';
    frame.dataset.waitFrom = '0';
    frame.style.cssText = matchMedia('(max-width: 1200px)').matches
      ? 'position:fixed;top:0;left:0;width:100%;height:calc(100% - 70px);border:0;z-index:900;background:var(--bg,#0f1115)'
      : 'position:fixed;top:0;left:var(--ccc-rail-w,76px);width:calc(100% - var(--ccc-rail-w,76px));height:100%;border:0;z-index:900;background:var(--bg,#0f1115)';
    window.addEventListener('message', function ready(event) {
      if (event.origin !== location.origin || event.source !== frame.contentWindow
          || event.data?.type !== 'ccc:app-fully-ready') return;
      window.removeEventListener('message', ready);
      finish();
    });
    frame.addEventListener('error', finish, {once:true});
    frame.src = frame.dataset.appUrl;
    // The body may not exist yet. Keep this frame on its original parent:
    // moving an iframe into the body would restart its browsing context.
    (document.body || document.documentElement).appendChild(frame);
  }
  let saved;
  try { saved = JSON.parse(localStorage.getItem('ccc-rail-apps-v1') || '[]'); }
  catch (_) {}
  const cached = Array.isArray(saved) && saved.find(app => app?.id === id);
  if (cached) { open(cached); return; }
  // This starts before app.js installs its background-read budget. A fresh
  // browser has no saved rail and must discover the app just as early.
  fetch('/api/apps', {cache:'no-store', signal:AbortSignal.timeout(10000)})
    .then(response => response.ok ? response.json() : null)
    .then(data => {
      if (!Array.isArray(data?.apps)) { finish(); return; }
      try { localStorage.setItem('ccc-rail-apps-v1', JSON.stringify(data.apps)); }
      catch (_) {}
      open(data.apps.find(app => app?.id === id));
    }).catch(finish);
})();
