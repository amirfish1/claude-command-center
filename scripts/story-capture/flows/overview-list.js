// S-OVR (list-view hero): full dashboard overview, LIST view as the primary
// surface (2026-07-16 direction: list is the hero, not kanban).
//
// List view in a readable sidebar shows the fleet — engine badges, status
// pills, branch chips, repo groups — beside an open session transcript with
// composer and health footer.
//
// Capture: node scripts/story-capture/shot.js --flow scripts/story-capture/flows/overview-list.js \
//            --out docs/product-story/assets/shots/S-OVR.png
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...LIST, 'ccc-sidebar-width': '560' },
  async run(ctx) {
    await ctx.pause(700);
    // Open a seeded session so the conversation pane is populated.
    await ctx.click('.conv-item[data-id^="22222222"]', { duration: 400 });
    await ctx.pause(1600);
    await ctx.suppressBanner();
  },
};
