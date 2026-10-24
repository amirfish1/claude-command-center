// Ch4 — Queues that run themselves.
'use strict';
const F = require('./prep.js');
module.exports = { path: '/', lead: 1200, tail: 2500, async run(ctx) {
  await F.prep(ctx);
  await F.caption(ctx, 'Queues: a to-do list your agents work through on their own', { hold: 3500 });
  const q = await ctx.eval(() => { const el = [...document.querySelectorAll('div,section')].find(e => e.offsetParent && /Search issues/.test(e.innerHTML) && e.getBoundingClientRect().width < 700 && e.getBoundingClientRect().height > 300); if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: Math.min(r.height, 560) }; });
  await F.caption(ctx, '1. Each ticket is a bug or a task, tagged by queue and priority', { hold: 300 });
  if (q) { await F.highlight(ctx, q); }
  await ctx.pause(4200); await F.unhighlight(ctx);
  await F.caption(ctx, '2. Orange ring: a worker agent is fixing #40 right now', { hold: 300 });
  const t = await F.boxOf(ctx.page, 'Host payout CSV');
  if (t) { await F.highlight(ctx, { x: t.x - 280, y: t.y - 8, w: 560, h: t.h + 16 }); await ctx.pause(4200); await F.unhighlight(ctx); }
  await F.caption(ctx, '3. Tickets also arrive from WhatsApp, iMessage and GitHub', { hold: 300 });
  const w = await F.boxOf(ctx.page, 'WhatsApp from a');
  if (w) { await F.highlight(ctx, { x: w.x - 280, y: w.y - 8, w: 560, h: w.h + 16 }); await ctx.pause(4200); await F.unhighlight(ctx); }
  await F.caption(ctx, '4. Open a ticket to see its details', { hold: 600 });
  await F.clickText(ctx, 'Booking calendar'); await ctx.pause(4000);
}};
