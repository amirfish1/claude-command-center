// V-09: search — type a query, land in the right session.
//
// List view: type "stripe" in the sidebar search; 24 rows filter down to the
// two Stripe sessions; open "Scaffold Stripe webhooks".
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: LIST,
  lead: 1400,
  tail: 1800,
  async run(ctx) {
    // Pretend the history index exists so the one-time "Build a history
    // index?" OOBE popover doesn't cover the search box mid-typing.
    await ctx.eval(() => { window._historyIndexStatus = { exists: true }; });
    await ctx.type('#convSearch', 'stripe', { perChar: 95 });
    await ctx.pause(1100); // filtered rows settle
    await ctx.click('.conv-item[data-id^="ffffffff"]', { duration: 800 }); // Scaffold Stripe webhooks
    await ctx.pause(1800);
    await ctx.suppressBanner();
  },
};
