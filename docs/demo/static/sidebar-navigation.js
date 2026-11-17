/* Navigation belongs to this window, not every open dashboard. */
(function () {
  'use strict';
  const key = 'ccc-sidebar-tab';
  // Every opening starts on All, including a reload or a restored phone tab.
  // Saved selections must not make a new visit open on a narrower list.
  let tab = 'alltickets';
  try { sessionStorage.setItem(key, tab); } catch (_) {}
  window.CCCNavigation = {
    // A view can retire tabs (Ticket view folds the ticket tabs into Workers).
    // Readers get the tab that is on screen; the stored one is kept so
    // turning the view off returns to it.
    mapTab: null,
    getTab() {
      if (typeof this.mapTab !== 'function') return tab;
      try { return String(this.mapTab(tab) || tab); } catch (_) { return tab; }
    },
    setTab(value) {
      tab = String(value);
      try { sessionStorage.setItem(key, tab); } catch (_) {}
    }
  };
})();
