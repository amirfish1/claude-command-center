// Ch3 — Agents working together: spawned children (lanes) and a group chat.
'use strict';
const F = require('./prep.js');
module.exports = { path: '/', lead: 1200, tail: 2500, async run(ctx) {
  await F.prep(ctx);
  await F.caption(ctx, 'One session can hand work to others, on any engine', { hold: 3500 });
  const p = await F.boxOf(ctx.page, 'Add partial refunds to the booking API');
  if (p) {
    await F.caption(ctx, '1. This parent session spawned two helpers: “2 lanes”', { hold: 300 });
    await F.highlight(ctx, { x: p.x, y: p.y - 6, w: 570, h: 270 }); await ctx.pause(4500); await F.unhighlight(ctx);
  }
  await F.caption(ctx, '2. A Claude reviewer reads the diff cold…', { hold: 800 });
  await F.clickText(ctx, 'Review refunds PR'); await ctx.pause(4500);
  await F.caption(ctx, '3. …and a Codex session writes the tests', { hold: 800 });
  await F.clickText(ctx, 'refund tests'); await ctx.pause(4500);
  await F.caption(ctx, '4. Sessions can also talk in a group chat', { hold: 800 });
  await F.clickText(ctx, 'Ship partial refunds'); await ctx.pause(3000);
  await ctx.scrollEl('#gcReaderBody', 400, { duration: 2200 }).catch(() => {}); await ctx.pause(2500);
  await F.caption(ctx, 'You can join in too. One post reaches every member', { hold: 3500 });
}};
