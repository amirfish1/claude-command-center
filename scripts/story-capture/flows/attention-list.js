// V-03, list view — spot the session that's waiting on you.
//
// LIST view (2026-07-16 direction). Sweep the fleet, scroll to the
// NEEDS-APPROVAL / question-waiting session ("Migrate blog to Eleventy" — the
// agent asked for confirmation before deleting the build dir), rest on it so
// its badge reads, then open a session so the transcript + answer composer
// ("Resume and send…") appear on the right.
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...LIST, 'ccc-sidebar-width': '470' },
  lead: 1400,
  tail: 1800,
  async run(ctx) {
    // Sweep the fleet.
    await ctx.move('.conv-item', { duration: 650 });
    await ctx.pause(420);
    await ctx.move('.conv-item:nth-of-type(4)', { duration: 520 }).catch(() => {});
    await ctx.pause(420);
    // Bring the NEEDS-APPROVAL / question-waiting row into view.
    await ctx.eval(() => {
      const list = document.getElementById('convList');
      const row = list && list.querySelector('.conv-item[data-id^="77777777"]');
      if (row) row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
    await ctx.pause(700);
    await ctx.move('#convList .conv-item[data-id^="77777777"], .conv-list .conv-item[data-id^="77777777"]',
      { duration: 650 }).catch(() => {});
    await ctx.pause(1100); // let the NEEDS-APPROVAL badge read
    // Open a session to answer — transcript + composer load.
    await ctx.click('.conv-item[data-id^="22222222"]', { duration: 350 }).catch(() => {});
    await ctx.pause(1800);
    await ctx.suppressBanner();
  },
};
