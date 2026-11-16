// An open app uses the standalone rail. The desktop dashboard is a separate
// controller host: initialize it when one of its controls is requested.
(function () {
  'use strict';
  const scripts = () => Array.from(document.querySelectorAll('script[type="application/ccc-dashboard"]'));
  const isNavigation = script => /\/app-rail\.js(?:\?|$)/.test(script.src);
  let dashboard, navigation, dashboardLoaded = false;
  const pendingControls = new WeakSet();
  function styles(all) {
    document.querySelectorAll('link[data-ccc-href]').forEach(link => {
      if (!all && !/\/app\.css(?:\?|$)/.test(link.dataset.cccHref)) return;
      link.href = link.dataset.cccHref;
      delete link.dataset.cccHref;
    });
  }
  async function replay(originals) {
    for (const original of originals) {
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
  }
  function ensureDashboard() {
    if (dashboard) return dashboard;
    dashboard = (async () => {
      await navigation;
      styles(true);
      const remaining = scripts().filter(script => !isNavigation(script));
      // Download together; execute at the original blocking/defer boundaries.
      remaining.filter(script => script.src).forEach(script => {
        const preload = document.createElement('link');
        preload.rel = 'preload'; preload.as = 'script'; preload.href = script.src;
        document.head.appendChild(preload);
      });
      await replay([...remaining.filter(script => !script.hasAttribute('defer')),
        ...remaining.filter(script => script.hasAttribute('defer'))]);
      dashboardLoaded = true;
      document.documentElement.classList.remove('ccc-app-starting');
      window.dispatchEvent(new Event('ccc:dashboard-assets-ready'));
    })();
    return dashboard;
  }
  window.__cccEnsureDashboard = ensureDashboard;
  // These are the original controls moved into the standalone account rail.
  // Wait for their real listeners, then replay the click on the same node.
  document.addEventListener('click', event => {
    if (dashboardLoaded || event.defaultPrevented || event.button > 0) return;
    const control = event.target.closest('#settingsBtn, .ccc-account-pop button, '
      + '#cccProductivityPill, #cccSystemPill, #cccThroughputPill');
    if (!control) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (pendingControls.has(control)) return;
    pendingControls.add(control);
    control.setAttribute('aria-busy', 'true');
    ensureDashboard().then(() => {
      pendingControls.delete(control);
      control.removeAttribute('aria-busy');
      if (control.isConnected) control.click();
    });
  }, true);
  async function start() {
    if (window.__cccAppFrameReady) await Promise.race([
      window.__cccAppFrameReady,
      new Promise(resolve => setTimeout(resolve, 5000)),
    ]);
    styles(false);
    navigation = replay(scripts().filter(isNavigation));
    await navigation;
    // Mobile retains its existing canonical bottom-nav initialization. A
    // desktop window resized to mobile also adopts that same controller.
    const mobile = matchMedia('(max-width: 1200px)');
    if (!document.getElementById('cccMobileAppFrame') || mobile.matches
        || new URLSearchParams(location.search).get('mobile') === '1') ensureDashboard();
    mobile.addEventListener('change', event => { if (event.matches) ensureDashboard(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
  else start();
})();
