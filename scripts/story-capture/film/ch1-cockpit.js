// Ch1 — One cockpit for every agent: engines side by side, same UI.
'use strict';
const F = require('./prep.js');
module.exports = { path: '/', lead: 1200, tail: 2500, async run(ctx) {
  await F.prep(ctx);
  await F.caption(ctx, 'This is CCC: every coding agent you run, in one sidebar', { hold: 3500 });
  await F.caption(ctx, '1. Each row is one session, from any engine', { hold: 600 });
  const sb = await ctx.eval(() => { const r = document.querySelector('#convList').getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: Math.min(r.height, 640) }; });
  await F.highlight(ctx, sb); await ctx.pause(3200); await F.unhighlight(ctx);
  await F.caption(ctx, '2. Open a Claude session…', { hold: 1200 });
  await F.clickText(ctx, 'Add partial refunds to the booking API'); await ctx.pause(4000);
  await F.caption(ctx, '3. …then a Codex session. Same view, same controls', { hold: 1200 });
  await F.clickText(ctx, 'refund tests'); await ctx.pause(4000);
  await F.caption(ctx, '4. Kimi and Gemini work the same way', { hold: 1000 });
  await F.clickText(ctx, 'Upgrade staging to Postgres 17'); await ctx.pause(3500);
  await F.clickText(ctx, 'Checkout: phone layout'); await ctx.pause(3500);
  await F.caption(ctx, 'You pick the engine and model per task. CCC stays the same', { hold: 3500 });
}};
