// Start the one transcript read while the shared renderer is downloading.
// Nothing is painted or declared ready here: the shared conversation entry owns the real controls.
(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const id = params.get('conv') || '';
  if (window.parent === window || params.get('embed') !== 'app'
      || !/^[A-Za-z0-9_.:-]{1,200}$/.test(id) || id === '__new__') return;
  const url = '/api/conversations/' + encodeURIComponent(id) + '?tail=120';
  const t0 = performance.now();
  // A shell can carry the same canonical fresh read with the child document.
  // Consume only its exact URL once; subsequent polls keep their normal path.
  try {
    const element = document.getElementById('cccAppInitialTail');
    const initial = element && JSON.parse(element.textContent);
    if (initial && initial.url === url && typeof initial.text === 'string') {
      const data = JSON.parse(initial.text);
      if (data && Array.isArray(data.events) && !data.error) {
        const response = new Response(initial.text, { headers: {
          'Content-Type': 'application/json',
          'Server-Timing': 'cache;desc="page-' + (initial.cache === 'hit' ? 'hit' : 'miss') + '"',
        } });
        window.__cccAppEmbedTail = { url, promise: Promise.resolve({
          response, text: initial.text, data, t0,
          tResp: t0, tBody: t0, tParse: performance.now(),
        }) };
        element.remove();
        return;
      }
    }
  } catch (_) { /* Ineligible or malformed data uses the regular fresh read. */ }
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
