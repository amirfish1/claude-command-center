/* Navigation belongs to this window, not every open dashboard. */
(function () {
  'use strict';
  const key = 'ccc-sidebar-tab';
  let tab = null;
  try { tab = sessionStorage.getItem(key); } catch (_) {}
  if (tab === null) {
    // One-time migration preserves this window's starting view. Later writes
    // to the legacy shared preference never steer an already open window.
    try { tab = localStorage.getItem(key); } catch (_) {}
    try { sessionStorage.setItem(key, tab || ''); } catch (_) {}
  }
  window.CCCNavigation = {
    getTab: () => tab,
    setTab(value) {
      tab = String(value);
      try { sessionStorage.setItem(key, tab); } catch (_) {}
    }
  };
})();
