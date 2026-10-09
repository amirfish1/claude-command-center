// Promo-film staging for the CCC static demo. Runs at document start on every page.
// __SEEDS__ is replaced by build-capture.py with the film's localStorage seeds.
(function () {
  window.__CCC_DEMO__ = true;
  window.__CCC_DEMO_FIXTURE_BASE__ = '/api';
  var seeds = __SEEDS__;
  try { Object.keys(seeds).forEach(function (k) { localStorage.setItem(k, seeds[k]); }); } catch (e) {}
  var nativeFetch = window.fetch.bind(window);
  var UUID = /^\/api\/conversations\/([0-9a-f]{8})-[0-9a-f-]{27}$/i;
  document.addEventListener('DOMContentLoaded', function () {
    // Per-session transcripts: /api/conversations/<uuid> -> staged t-<prefix>.json (falls back to the demo mapping).
    var demoFetch = window.fetch;
    window.fetch = function (input, init) {
      try {
        var u = typeof input === 'string' ? input : (input && input.url) || '';
        var m = UUID.exec(u.split('?')[0]);
        if (m && (!init || !init.method || init.method === 'GET')) {
          var staged = '/api/conversations/t-' + m[1].toLowerCase() + '.json';
          return nativeFetch(staged).then(function (r) { return r.ok ? r : demoFetch(input, init); });
        }
      } catch (e) {}
      return demoFetch(input, init);
    };
    var s = document.createElement('style');
    s.textContent = '[data-hf-overlay]{display:none!important}' +
      '#__ccc_demo_ro_banner__,#hiOobePrompt,.hi-oobe,.hi-retention-warning{display:none!important}' +
      'html,body,*{scroll-behavior:auto!important}' +
      '*{caret-color:auto}' +
      // CSS zoom breaks sticky offsets inside the scrolled list; the film never scrolls it.
      '#convList .conv-archived-tools{position:static!important}' +
      // The optimistic "Thinking…" pill after a send never resolves in the static demo.
      '.conv-live-tool-inline.optimistic{display:none!important}' +
      // Search-progress banner under the sidebar search while typing.
      '#convSearchStatus{display:none!important}';
    document.head.appendChild(s);
    document.documentElement.style.zoom = '3';
    // The demo repo list renders one unlabeled folder chip; drop it.
    var scrub = function () {
      document.querySelectorAll('.spawn-cwd-chip').forEach(function (b) {
        if (b.textContent.trim() === '[object Object]') b.remove();
      });
    };
    new MutationObserver(scrub).observe(document.body, { childList: true, subtree: true });
    scrub();
  });
})();
