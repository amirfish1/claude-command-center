// Shared localStorage seeds for demo-fixture flows (current static bundle).
'use strict';

// Suppress first-run chrome: What's New modal (keyed to /api/version, which
// the demo fixture reports as "demo"), PWA install card, telemetry bar.
const CLEAN = {
  'ccc-last-seen-version': 'demo',
  'ccc-whats-new-dismissed-version': 'demo',
  'ccc-pwa-install-dismissed': '9999999999999',
  'ccc-telemetry-bar-dismissed': '1',
};

// Kanban board view in a widened sidebar, right utilities rail collapsed.
const BOARD = {
  ...CLEAN,
  'ccc-session-view': 'board',
  'ccc-kanban-view': 'true',
  'ccc-sidebar-width': '1020',
  'ccc-status-rail-collapsed': '1',
};

// List view with a slightly wider sidebar so titles/search read well.
const LIST = {
  ...CLEAN,
  'ccc-session-view': 'list',
  'ccc-kanban-view': 'false',
  'ccc-sidebar-width': '470',
  'ccc-status-rail-collapsed': '1',
};

module.exports = { CLEAN, BOARD, LIST };
