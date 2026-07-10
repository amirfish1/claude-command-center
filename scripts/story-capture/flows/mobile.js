// V-15: mobile walkthrough (390x844).
//
// Phone viewport: the session list fills the screen; tap a session, the
// transcript slides in full-screen; scroll it; tap Back to return to the list.
'use strict';
const { CLEAN } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  viewport: '390x844',
  localStorage: { ...CLEAN, 'ccc-status-rail-collapsed': '1' },
  lead: 1500,
  tail: 1700,
  async run(ctx) {
    // Scan the list.
    await ctx.scrollEl('.conv-list, #convList', 220, { duration: 900 });
    await ctx.pause(500);
    await ctx.scrollEl('.conv-list, #convList', -220, { duration: 700 });
    await ctx.pause(400);
    // Open a session.
    await ctx.click('.conv-item[data-id^="22222222"]', { duration: 700 });
    await ctx.pause(1800);
    // Read a bit of the transcript.
    await ctx.scrollEl('.conv-scroll, .cp-scroll, .main', 240, { duration: 900 });
    await ctx.pause(700);
    // Back to the fleet.
    const back = await ctx.findByText('button', 'Back');
    await ctx.click(back, { duration: 600 });
    await ctx.pause(1100);
    await ctx.suppressBanner();
  },
};
