// V-06: kanban drag.
//
// Cursor-led clip on the seeded demo fixtures (current static bundle):
//   1. establish the board (GH Issues / Needs Attention / Icebox / In Progress),
//   2. drag the parked Icebox card into In Progress (client-side columnOverrides
//      move — genuinely works in demo mode, persists to localStorage),
//   3. open a session card so the transcript pane loads.
//
// Capture: node scripts/story-capture/record.js --flow scripts/story-capture/flows/kanban-drag.js \
//            --out docs/product-story/assets/video/V-06-kanban-drag.mp4 \
//            --poster docs/product-story/assets/video/posters/V-06.png
'use strict';

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  lead: 1600,
  tail: 1600,
  localStorage: {
    'ccc-last-seen-version': 'demo',
    'ccc-whats-new-dismissed-version': 'demo',
    'ccc-pwa-install-dismissed': '9999999999999',
    'ccc-telemetry-bar-dismissed': '1',
    'ccc-session-view': 'board',
    'ccc-kanban-view': 'true',
    'ccc-sidebar-width': '1020',
    'ccc-status-rail-collapsed': '1',
  },
  async run(ctx) {
    const iceCard = '#kanbanBoard .kanban-column[data-col="icebox"] .kanban-card';
    const workCol = '#kanbanBoard .kanban-column[data-col="working"]';
    const healthzCard = '#kanbanBoard .kanban-card[data-id="11111111-1111-4aaa-aaaa-000000000001"]';

    // Beat 1: hover the parked Icebox card.
    await ctx.move(iceCard, { duration: 900 });
    await ctx.pause(450);
    // Beat 2: drag it into In Progress.
    await ctx.dragHTML5(iceCard, workCol, { duration: 1300, hold: 400 });
    await ctx.pause(900);
    // Beat 3: open a session — transcript + composer load in the right pane.
    await ctx.click(healthzCard, { duration: 800 });
    await ctx.pause(1700);
    await ctx.suppressBanner();
  },
};
