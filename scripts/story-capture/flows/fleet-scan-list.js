// V-01 (HERO), list-primary — scan the fleet in LIST view.
//
// 2026-07-16 direction: the LIST view is the hero (not kanban). Single-scene
// list scan: live sessions across engines (Claude / Codex / Gemini icons),
// status pills, repo chips, and the project tree; cursor sweeps the rows,
// scrolls the fleet, then opens a live session — transcript + composer load.
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...LIST, 'ccc-sidebar-width': '470' },
  lead: 1400,
  tail: 1700,
  async run(ctx) {
    // Sweep the live rows (engine icons + status pills + repo chips).
    await ctx.move('.conv-item', { duration: 650 });
    await ctx.pause(450);
    await ctx.move('.conv-item:nth-of-type(3)', { duration: 520 }).catch(() => {});
    await ctx.pause(380);
    await ctx.move('.conv-item:nth-of-type(5)', { duration: 520 }).catch(() => {});
    await ctx.pause(360);
    // Scroll the fleet list to reveal more sessions + the project tree.
    await ctx.scrollEl('#convList, .conv-list', 300, { duration: 900 });
    await ctx.pause(520);
    await ctx.scrollEl('#convList, .conv-list', -300, { duration: 700 });
    await ctx.pause(360);
    // Open a live session — transcript + composer load in the right pane.
    await ctx.move('.conv-item[data-id^="22222222"]', { duration: 550 }).catch(() => {});
    await ctx.pause(300);
    await ctx.click('.conv-item[data-id^="22222222"]', { duration: 300 });
    await ctx.pause(1900);
    await ctx.suppressBanner();
  },
};
