// Ch6 — Your fleet across machines.
'use strict';
const F = require('./prep.js');
module.exports = { path: '/', lead: 1200, tail: 2500, async run(ctx) {
  await F.prep(ctx);
  await F.caption(ctx, 'CCC on each machine knows about the others', { hold: 3200 });
  await F.caption(ctx, '1. Other machines are listed right in the sidebar', { hold: 300 });
  const m = await F.boxOf(ctx.page, 'OTHER MACHINES') || await F.boxOf(ctx.page, 'Other machines');
  if (m) { await F.highlight(ctx, { x: m.x - 30, y: m.y - 10, w: 590, h: m.h + 20 }); await ctx.pause(3500); await F.unhighlight(ctx);
    await ctx.click({ x: m.cx, y: m.cy }, { duration: 900 }); await ctx.pause(3000); }
  await F.caption(ctx, '2. Open a session, then move it to another machine', { hold: 600 });
  await F.clickText(ctx, 'Open bookings from push notifications'); await ctx.pause(3000);
  await ctx.click('#convOverflowBtn', { duration: 1000 }); await ctx.pause(1800);
  const c = await F.boxOf(ctx.page, 'Continue on');
  if (c) { await F.highlight(ctx, { x: c.x - 10, y: c.y - 6, w: c.w + 20, h: c.h + 12 }); await ctx.pause(2500); await F.unhighlight(ctx);
    await ctx.click({ x: c.cx, y: c.cy }, { duration: 800 }); await ctx.pause(4500); }
  await F.caption(ctx, '3. Pick the machine. The session continues there with its history', { hold: 3500 });
}};
