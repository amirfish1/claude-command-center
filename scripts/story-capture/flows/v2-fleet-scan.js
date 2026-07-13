// V-01 (HERO) v2 — scan the fleet, tightened to the 15-25s hero budget.
//
// Same two-scene story as flows/fleet-scan.js (list scan -> kanban board) but
// with trimmed pauses and a shorter reload settle so the clip lands ~18-20s
// with no dead time. Scene 1 sweeps the live rows (Claude / Codex / Gemini
// engine icons, status pills, repo chips, project tree); Scene 2 cuts to the
// same fleet as a board, pans the columns, and opens one live session.
'use strict';
const { LIST, BOARD } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: LIST,
  lead: 1300,
  tail: 1400,
  async run(ctx) {
    // Scene 1: sweep the fleet rows (engine icons + status pills + repo chips).
    await ctx.move('.conv-item', { duration: 600 });
    await ctx.pause(420);
    await ctx.move('.conv-item:nth-of-type(3)', { duration: 520 }).catch(() => {});
    await ctx.pause(380);
    await ctx.move('.conv-item:nth-of-type(5)', { duration: 520 }).catch(() => {});
    await ctx.pause(360);
    // Scroll the sidebar list to reveal more of the fleet + project tree.
    await ctx.scrollEl('.conv-list, #convList', 300, { duration: 850 });
    await ctx.pause(520);
    await ctx.scrollEl('.conv-list, #convList', -300, { duration: 650 });
    await ctx.pause(300);

    // Scene 2: same fleet as a board (short settle — the demo polls forever).
    await ctx.reloadWith(BOARD, { cursorAt: { x: 500, y: 420 }, settleMs: 2600 });
    await ctx.pause(700);
    // Pan across the columns (Icebox / In Progress / Waiting come into view).
    await ctx.scrollEl('#kanbanBoard', 0, { dx: 430, duration: 1300 });
    await ctx.pause(560);
    // Hover a live card, then open it — transcript + composer load.
    const card = '#kanbanBoard .kanban-card[data-id="11111111-1111-4aaa-aaaa-000000000001"]';
    await ctx.move(card, { duration: 600 });
    await ctx.pause(400);
    await ctx.click(card, { duration: 250 });
    await ctx.pause(1500);
    await ctx.suppressBanner();
  },
};
