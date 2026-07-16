// S-F6a: continue-on-another-machine handoff dialog.
//
// LIST view. Select a session, open the conversation overflow (⋯) menu, and
// pick "Continue on…" to open the handoff modal ("Continue on another node")
// with a destination picker. Paired nodes come from the seeded federation
// fixture (docs/demo/api/federation/peers.json). The status rail is left
// expanded so the ⋯ overflow button is visible.
'use strict';
const { CLEAN } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...CLEAN, 'ccc-session-view': 'list', 'ccc-kanban-view': 'false', 'ccc-sidebar-width': '470' },
  async run(ctx) {
    await ctx.pause(700);
    // Select a session (real mouse events set currentSession, which gates the
    // overflow menu's actions).
    await ctx.click('.conv-item[data-id^="22222222"]', { duration: 400 });
    await ctx.pause(1300);
    // Open the ⋯ overflow menu, then pick "Continue on…".
    await ctx.click('#convOverflowBtn', { duration: 350 });
    await ctx.waitFor('[data-handoff-continue]').catch(() => {});
    await ctx.pause(400);
    await ctx.click('[data-handoff-continue]', { duration: 350 });
    await ctx.waitFor('#handoffModal.open').catch(() => {});
    await ctx.pause(1500);
    await ctx.suppressBanner();
  },
};
