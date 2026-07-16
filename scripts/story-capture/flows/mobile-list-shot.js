// M-01: mobile session list (390x844), LIST view.
//
// Phone-width viewport with the fleet list filling the screen: engine badges,
// status pills, and repo chips on stacked rows. A still (no open session) so
// the list itself is the subject.
'use strict';
const { CLEAN } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  viewport: '390x844',
  localStorage: { ...CLEAN, 'ccc-session-view': 'list', 'ccc-kanban-view': 'false', 'ccc-status-rail-collapsed': '1' },
  async run(ctx) {
    await ctx.pause(900);
    await ctx.suppressBanner();
  },
};
