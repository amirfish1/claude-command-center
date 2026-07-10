// V-03: spot the session that's waiting on you.
//
// Board view: pan to the Waiting column, hover the NEEDS APPROVAL card
// ("Migrate blog to Eleventy" — the agent asked for confirmation before a
// destructive step), open it. The transcript pane loads with the composer
// ready to answer.
'use strict';
const { BOARD } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: BOARD,
  lead: 1500,
  tail: 1700,
  async run(ctx) {
    // Bring the Waiting column into view.
    await ctx.scrollEl('#kanbanBoard', 0, { dx: 430, duration: 1200 });
    await ctx.pause(600);
    const card = '#kanbanBoard .kanban-column[data-col="waiting"] .kanban-card';
    await ctx.move(card, { duration: 800 });
    await ctx.pause(1100); // let NEEDS APPROVAL badge + inline answer box read
    await ctx.click(card, { duration: 250 });
    await ctx.pause(1900);
    await ctx.suppressBanner();
  },
};
