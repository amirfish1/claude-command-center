/* Marks the sidebar .is-narrow while it is dragged below NARROW_PX on a
 * desktop window, so sidebar-narrow.css can lay out for the column's real
 * width. Viewport media queries cannot see a resized column, and container
 * queries on the sidebar would trap its fixed-position dialogs and menus. */
(function (root) {
  'use strict';
  var NARROW_PX = 320;
  var DESKTOP_QUERY = '(min-width: 1201px)';

  function isNarrow(width, desktop) {
    return !!desktop && width > 0 && width < NARROW_PX;
  }

  function init() {
    var doc = root.document;
    var sidebar = doc && doc.querySelector('.sidebar');
    if (!sidebar || typeof root.ResizeObserver !== 'function') return;
    var mq = root.matchMedia ? root.matchMedia(DESKTOP_QUERY) : null;
    var last = null;
    var width = 0;
    function apply() {
      var next = isNarrow(width, mq ? mq.matches : true);
      if (next === last) return;
      last = next;
      sidebar.classList.toggle('is-narrow', next);
    }
    new root.ResizeObserver(function (entries) {
      width = entries[0]?.borderBoxSize?.[0]?.inlineSize ?? sidebar.getBoundingClientRect().width;
      apply();
    }).observe(sidebar, { box: 'border-box' });
    if (mq && mq.addEventListener) mq.addEventListener('change', apply);
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { isNarrow: isNarrow, NARROW_PX: NARROW_PX };
  }
  if (!root.document) return;
  if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})(typeof window !== 'undefined' ? window : globalThis);
