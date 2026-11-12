/* Navigation belongs to this window, not every open dashboard. */
(function () {
  'use strict';
  const key = 'ccc-sidebar-tab';
  // Every opening starts on All, including a reload or a restored phone tab.
  // Saved selections must not make a new visit open on a narrower list.
  let tab = 'alltickets';
  try { sessionStorage.setItem(key, tab); } catch (_) {}
  window.CCCNavigation = {
    getTab: () => tab,
    setTab(value) {
      tab = String(value);
      try { sessionStorage.setItem(key, tab); } catch (_) {}
    }
  };
})();
