// Start the one transcript read while the shared renderer is downloading.
// Nothing is painted or declared ready here: app.js owns the real controls.
(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const id = params.get('conv') || '';
  if (window.parent === window || params.get('embed') !== 'app'
      || !/^[A-Za-z0-9_.:-]{1,200}$/.test(id) || id === '__new__') return;
  const url = '/api/conversations/' + encodeURIComponent(id) + '?tail=120';
  const t0 = performance.now();
  const promise = fetch(url, { cache: 'no-store' }).then(async response => {
    const tResp = performance.now();
    const text = await response.text();
    const tBody = performance.now();
    const data = JSON.parse(text);
    const tParse = performance.now();
    return { response, text, data, t0, tResp, tBody, tParse };
  }).catch(() => null);
  window.__cccAppEmbedTail = { url, promise };
})();
