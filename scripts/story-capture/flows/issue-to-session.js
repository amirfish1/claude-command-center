// V-14: from GitHub issue to working session.
//
// Board view: hover an open GH issue card in the backlog column, then click
// its "Start session" action. In the static demo the spawn is stubbed — the
// read-only banner appears and says so honestly; the video keeps it visible.
'use strict';
const { BOARD } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: BOARD,
  lead: 1500,
  tail: 2600, // hold on the banner
  async run(ctx) {
    const card = '#kanbanBoard .kanban-column[data-col="backlog"] .kanban-card';
    await ctx.move(card, { duration: 800 });
    await ctx.pause(900); // issue number, labels, actions read
    // The demo is read-only: let its banner show when the spawn is stubbed.
    await ctx.allowBanner();
    const btn = await ctx.findByText(
      '#kanbanBoard .kanban-column[data-col="backlog"] .kanban-card button', 'Start session');
    await ctx.click(btn, { duration: 500 });
    await ctx.pause(2200); // banner: "Action skipped (…) Install CCC to run sessions…"
  },
};
