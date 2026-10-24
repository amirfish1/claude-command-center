// Ch2 — Keeping sessions healthy: context %, compact, usage.
'use strict';
const F = require('./prep.js');
module.exports = { path: '/', lead: 1200, tail: 2500, async run(ctx) {
  await F.prep(ctx);
  await F.caption(ctx, 'Long sessions fill their context window. CCC shows how full each one is', { hold: 3500 });
  await ctx.eval(() => {
    const el = [...document.querySelectorAll('.conv-item')].find(e => /Investigate slow/.test(e.textContent));
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
  await ctx.pause(1800);
  const b = await F.boxOf(ctx.page, 'Investigate slow /availability query');
  if (b) {
    await F.caption(ctx, '1. This one is 84% full. The badge turns red as it fills', { hold: 300 });
    await F.highlight(ctx, { x: b.x, y: b.y - 8, w: 560, h: b.h + 50 }); await ctx.pause(3800); await F.unhighlight(ctx);
    await F.caption(ctx, '2. Open it', { hold: 600 });
    await ctx.click({ x: b.cx, y: b.cy }, { duration: 1100 }); await ctx.pause(3500);
  }
  await F.caption(ctx, '3. The input bar shows the same gauge, with one-click Compact', { hold: 300 });
  await F.highlight(ctx, '.compact-btn, #convCompactBtn'); await ctx.pause(4000); await F.unhighlight(ctx);
  await F.caption(ctx, '4. Up top: how much of your plan is left, and when it resets', { hold: 300 });
  const u = await F.boxOf(ctx.page, 'Usage left');
  if (u) { await F.highlight(ctx, { x: u.x, y: u.y, w: 400, h: 80 }); await ctx.pause(4000); await F.unhighlight(ctx); }
  await F.caption(ctx, '', {});
}};
