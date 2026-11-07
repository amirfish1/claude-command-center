// Runs inside an embedded page (the Decisions tab). Its fetches go to the parent
// page's in-browser backend, so the frame needs no server either.
(function () {
  'use strict';
  const parentWin = window.parent;
  if (!parentWin || parentWin === window || typeof parentWin.fetch !== 'function') return;
  const realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    let u;
    try { u = new URL(url, location.href); } catch (_) { return realFetch(input, init); }
    if (u.origin !== location.origin || !u.pathname.includes('/api/')) return realFetch(input, init);
    return parentWin.fetch(u.href, init ? { method: init.method, body: init.body } : undefined);
  };
})();
