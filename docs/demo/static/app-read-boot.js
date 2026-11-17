// An app's explicit initial reads share its current no-store HTML response.
(function (root) {
  'use strict';
  let initial = {};
  try {
    const element = document.getElementById('cccAppInitialReads');
    const supplied = element && JSON.parse(element.textContent);
    if (supplied && typeof supplied.reads === 'object' && supplied.reads) initial = supplied.reads;
    element?.remove();
  } catch (_) {}
  const clear = () => { initial = {}; };
  // Reads belong to this initial render only. A later refresh or an unopened
  // fold must use the existing APIs, including mutations made after navigation.
  root.addEventListener('ccc:app-ready', clear, {once:true});
  root.addEventListener('ccc:app-fully-ready', clear, {once:true});
  root.addEventListener('pagehide', clear, {once:true});
  async function json(input, options = {}) {
    options.signal?.throwIfAborted();
    let key;
    try {
      const url = new URL(input, location.href);
      if (url.origin === location.origin) key = url.pathname + url.search;
    } catch (_) {}
    if (key && Object.prototype.hasOwnProperty.call(initial, key)) {
      const text = initial[key];
      delete initial[key];
      try { return JSON.parse(text); } catch (_) { /* Fall back to the existing read. */ }
    }
    const response = await fetch(input, {cache:'no-store', ...options, method:'GET'});
    if (!response.ok) throw new Error(String(input) + ' ' + response.status);
    return response.json();
  }
  root.CCCAppReads = Object.freeze({json});
})(window);
