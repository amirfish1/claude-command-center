// Ch5 — Remember everything: search across sessions.
'use strict';
const F = require('./prep.js');
module.exports = { path: '/', lead: 1200, tail: 2500, async run(ctx) {
  await F.prep(ctx);
  await ctx.eval(() => { window._historyIndexStatus = { exists: true }; });
  await F.caption(ctx, 'Find any past session, whichever engine ran it', { hold: 3200 });
  await F.caption(ctx, '1. Type what you remember', { hold: 600 });
  await ctx.click('#convSearch', { duration: 900 }); await ctx.pause(700);
  await F.prep(ctx); // the one-time "Build a history index?" popover
  await ctx.page.type('#convSearch', 'refund', { delay: 160 }); await ctx.pause(3500);
  await F.caption(ctx, '2. Claude, Codex and group-chat matches, all together', { hold: 4000 });
  await F.caption(ctx, '3. Open one to jump straight into it', { hold: 600 });
  await F.clickText(ctx, 'Review refunds PR'); await ctx.pause(4500);
}};
