// V-08: split pane — two transcripts side by side.
//
// List view: open one session, then drag a second session's row onto the
// right edge of the open conversation. The `.drop-zone.right` target appears
// mid-drag; dropping opens both transcripts in a split reader.
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: LIST,
  lead: 1500,
  tail: 2200,
  async run(ctx) {
    const rowA = '.conv-item[data-id^="22222222"]'; // Fix auth flow…
    const rowB = '.conv-item[data-id^="11111111"]'; // Add /healthz…
    await ctx.click(rowA, { duration: 700 });
    await ctx.pause(1600);
    // Route the drag across the open conversation — the right drop zone
    // becomes visible once the drag is over the main pane.
    await ctx.dragHTML5(rowB, '.drop-zone.right', { duration: 1500, hold: 450, via: '.main' });
    await ctx.pause(2000);
    await ctx.suppressBanner();
  },
};
