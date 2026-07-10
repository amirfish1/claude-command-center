// S-OVR: full dashboard overview.
//
// Runs the CURRENT static bundle against the seeded demo fixtures (serve the
// repo root, see README). Board view in a widened sidebar shows the seeded
// GH Issues / Needs Attention / Icebox / In Progress columns; a session card
// is opened so the right pane shows a live-looking transcript + composer.
//
// Capture: node scripts/story-capture/shot.js --flow scripts/story-capture/flows/overview.js \
//            --out docs/product-story/assets/shots/S-OVR.png
'use strict';

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: {
    // suppress first-run chrome (What's New modal, PWA install card, telemetry bar)
    'ccc-last-seen-version': 'demo',
    'ccc-whats-new-dismissed-version': 'demo',
    'ccc-pwa-install-dismissed': '9999999999999',
    'ccc-telemetry-bar-dismissed': '1',
    // board view, wide enough for ~4 columns, right utilities rail collapsed
    'ccc-session-view': 'board',
    'ccc-kanban-view': 'true',
    'ccc-sidebar-width': '1020',
    'ccc-status-rail-collapsed': '1',
  },
  async run(ctx) {
    await ctx.pause(600);
    // Open a seeded session so the conversation pane is populated.
    await ctx.click('#kanbanBoard .kanban-card[data-id="22222222-2222-4aaa-aaaa-000000000002"]', { duration: 400 });
    await ctx.pause(1500);
    await ctx.suppressBanner();
  },
};
