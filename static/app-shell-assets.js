// The selected app gets the connection slots and main thread first.
// Replay the existing dashboard scripts, in their original blocking/defer
// order, after the app is painted (or the startup timeout has expired).
(function () {
  'use strict';
  async function start() {
    if (window.__cccAppFrameReady) await Promise.race([
      window.__cccAppFrameReady,
      new Promise(resolve => setTimeout(resolve, 5000)),
    ]);
    document.querySelectorAll('link[data-ccc-href]').forEach(link => {
      link.href = link.dataset.cccHref;
      delete link.dataset.cccHref;
    });
    const scripts = Array.from(document.querySelectorAll('script[type="application/ccc-dashboard"]'));
    const blocking = scripts.filter(script => !script.hasAttribute('defer'));
    const deferred = scripts.filter(script => script.hasAttribute('defer'));
    // Begin downloads together, but retain execution order and currentScript.
    scripts.filter(script => script.src).forEach(script => {
      const preload = document.createElement('link');
      preload.rel = 'preload'; preload.as = 'script'; preload.href = script.src;
      document.head.appendChild(preload);
    });
    for (const original of [...blocking, ...deferred]) {
      const script = document.createElement('script');
      for (const attribute of original.attributes) {
        if (attribute.name !== 'type' && attribute.name !== 'defer') {
          script.setAttribute(attribute.name, attribute.value);
        }
      }
      if (original.src) {
        await new Promise(resolve => {
          script.onload = script.onerror = resolve;
          original.replaceWith(script);
        });
      } else {
        script.textContent = original.textContent;
        original.replaceWith(script);
      }
    }
    window.dispatchEvent(new Event('ccc:dashboard-assets-ready'));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
  else start();
})();
