// Shared localStorage seeds for demo-fixture flows (current static bundle).
'use strict';

// Suppress first-run chrome: What's New modal (keyed to /api/version, which
// the demo fixture reports as "demo"), PWA install card, telemetry bar.
const CLEAN = {
  'ccc-last-seen-version': 'demo',
  'ccc-whats-new-dismissed-version': 'demo',
  'ccc-pwa-install-dismissed': '9999999999999',
  'ccc-telemetry-bar-dismissed': '1',
  // FIRST FLIGHT tour auto-starts on fresh installs; suppress for captures.
  'ccc-tour-done': '1',
  // The demo fixtures have fixed July timestamps; the default 7d archive
  // window filters them all out once the fixtures age past a week. 'all'
  // keeps every seeded row visible regardless of capture date.
  'ccc-archive-window': 'all',
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

// Sidebar Queues tab as the whole inbox (WatchTower queues): separate
// Issues/Queues tabs on (CCC-778's collapse is the default), the queue panel
// mounted in the sidebar, scope pinned to All queues. Sidebar > 620px keeps
// the queue picker in its desktop (non-fq-mobile) treatment.
const QUEUES = {
  ...CLEAN,
  'ccc-separate-tabs': 'on',
  'ccc-sidebar-tab': 'queues',
  'ccc-uxq-selected-scope': '{"__queue_global__":"ALL"}',
  'ccc-sidebar-width': '700',
  'ccc-status-rail-collapsed': '1',
};

// QUEUES + the multi-queue health list (CCC-781's compact single-queue strip
// is the default; the full per-queue list with worker rows is opt-in).
const QUEUE_WORKERS = {
  ...QUEUES,
  'ccc-queue-rhs-list': 'on',
};

module.exports = { CLEAN, BOARD, LIST, QUEUES, QUEUE_WORKERS };
