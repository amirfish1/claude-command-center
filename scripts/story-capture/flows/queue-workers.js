// V-18: workers that specialize over time.
//
// Scene 1 — Queues tab with the multi-queue health list on
// (ccc-queue-rhs-list): each queue row carries its LIVE badge, auto-drain
// toggle and claim-type cycle, with the live WatchTower workers listed under
// their queue, and the WORKING NOW strip above (three workers across two
// queues). Scene 2 (scene cut — the compact status strip is a persisted
// preference with no click affordance in this layout) — scoped to CONTENT
// with the compact strip: engine · model · effort plan chip plus the
// Learnings link (the shared learnings file a worker reads before it starts
// and writes back when it ends).
// Requires the queue/* + wt/workers fixtures (see flows/queues.js).
'use strict';
const { QUEUE_WORKERS } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: QUEUE_WORKERS,
  lead: 1800,
  tail: 1800,
  async run(ctx) {
    await ctx.waitFor('#queueWorkingStrip .fq-working-row', 9000);
    await ctx.waitFor('#queueHealthStrip .fq-health-row', 9000).catch(() => {});
    await ctx.pause(1200);

    // WORKING NOW: hover each live worker row.
    const workingRows = await ctx.eval(() =>
      [...document.querySelectorAll('#queueWorkingStrip .fq-working-row')].map(r => {
        const b = r.getBoundingClientRect();
        return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
      }));
    for (const pt of workingRows.slice(0, 3)) {
      await ctx.move(pt, { duration: 650 });
      await ctx.pause(800);
    }

    // Down to the health list: hover the queue rows with their LIVE badges
    // and the worker rows nested underneath.
    const healthRows = await ctx.eval(() =>
      [...document.querySelectorAll('#queueHealthStrip .fq-health-row')].map(r => {
        const b = r.getBoundingClientRect();
        return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
      }));
    for (const pt of healthRows.slice(0, 2)) {
      await ctx.move(pt, { duration: 700 });
      await ctx.pause(900);
    }

    // Scene 2: compact per-queue status strip for CONTENT (auto-drain on,
    // 2x workers, the engine/model/effort plan chip, Learnings + Log).
    await ctx.reloadWith({
      'ccc-queue-rhs-list': 'off',
      'ccc-uxq-selected-scope': '{"__queue_global__":"CONTENT"}',
    }, { cursorAt: { x: 350, y: 320 } });
    await ctx.waitFor('#queueStatusStrip:not([hidden])', 9000).catch(() => {});
    await ctx.pause(1200);

    // Hover the status strip's Learnings link — the queue's shared memory.
    const learnings = await ctx.eval(() => {
      const btn = document.querySelector('#queueStatusStrip .fq-status-learnings-toggle');
      if (!btn) return null;
      const b = btn.getBoundingClientRect();
      return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
    });
    if (learnings) {
      await ctx.move(learnings, { duration: 700 });
      await ctx.pause(1400);
    }
    // Then up to the WORKING NOW rows this queue is running.
    const row = await ctx.eval(() => {
      const r = document.querySelector('#queueWorkingStrip .fq-working-row');
      if (!r) return null;
      const b = r.getBoundingClientRect();
      return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
    });
    if (row) {
      await ctx.move(row, { duration: 700 });
      await ctx.pause(1000);
    }
    await ctx.suppressBanner();
  },
};
