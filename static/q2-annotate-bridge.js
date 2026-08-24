/* q2's top-bar control delegates to the shared WatchTower picker. */
(function () {
  'use strict';

  var topbarButton = document.getElementById('q2AnnotateBtn');
  var widgetButton = document.querySelector('[title="WatchTower: annotate element"]');
  if (!topbarButton || !widgetButton) return;

  // q2 owns the visible affordance; the shared widget still owns the picker,
  // modal, and queue submission behavior.
  widgetButton.style.display = 'none';
  topbarButton.addEventListener('click', function () { widgetButton.click(); });
})();
