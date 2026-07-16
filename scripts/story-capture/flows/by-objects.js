// V-17 (new): "By objects" — reshape the flat list into grouped work.
//
// LIST view (2026-07-16 direction). One click on "By objects" regroups the
// flat current-sessions list into repo/object clusters, so strategy and
// execution stop competing in one undifferentiated stream. Pain row 13 (F3).
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...LIST, 'ccc-sidebar-width': '470' },
  lead: 1400,
  tail: 1800,
  async run(ctx) {
    // Establish the flat list.
    await ctx.move('.conv-item', { duration: 650 });
    await ctx.pause(500);
    await ctx.move('.conv-item:nth-of-type(3)', { duration: 500 }).catch(() => {});
    await ctx.pause(500);
    // One click: regroup by objects/repos.
    const byObjects = '[data-current-sessions-mode="objects"]';
    await ctx.move(byObjects, { duration: 600 }).catch(() => {});
    await ctx.pause(400);
    await ctx.click(byObjects, { duration: 300 });
    await ctx.pause(1400); // groups animate in
    // Sweep the newly grouped clusters.
    await ctx.scrollEl('#convList, .conv-list', 260, { duration: 900 }).catch(() => {});
    await ctx.pause(700);
    await ctx.scrollEl('#convList, .conv-list', -260, { duration: 700 }).catch(() => {});
    await ctx.pause(600);
    await ctx.suppressBanner();
  },
};
