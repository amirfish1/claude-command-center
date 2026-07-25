// S-F2a: needs-attention, LIST view.
//
// The fleet in list view; the question-waiting session ("Migrate blog to
// Eleventy" — the agent asked for confirmation before a destructive step) is
// opened so its NEEDS-APPROVAL / question-waiting state and the inline answer
// composer read on the right, while the list shows its WAITING pill.
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...LIST, 'ccc-sidebar-width': '560' },
  async run(ctx) {
    await ctx.pause(700);
    // Populate the right pane with a live transcript (this row opens reliably).
    await ctx.click('.conv-item[data-id^="22222222"]', { duration: 400 });
    await ctx.pause(1400);
    // Scroll the fleet list so the NEEDS-APPROVAL / question-waiting session
    // ("Migrate blog to Eleventy") is in view — that flagged row is the subject.
    // Selecting a session auto-expands the project tree and shortens #convList,
    // so scroll it deterministically to the waiting row.
    await ctx.eval(() => {
      const list = document.getElementById('convList');
      const row = list && list.querySelector('.conv-item[data-id^="77777777"]');
      if (row) row.scrollIntoView({ block: 'center' });
    });
    await ctx.pause(700);
    await ctx.suppressBanner();
  },
};
