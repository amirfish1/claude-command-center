// V-01 (HERO): scan the fleet.
//
// Scene 1 — list view: live sessions across engines (Claude / Codex / Gemini
// icons), status pills, branch chips, project tree. Cursor sweeps the rows.
// Scene 2 — (scene cut; the current UI has no click affordance for the
// persisted list->board preference) the same fleet as a kanban board:
// pan across columns, then open a live session — transcript + composer load.
'use strict';
const { LIST, BOARD } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: LIST,
  lead: 1700,
  tail: 1700,
  async run(ctx) {
    // Scene 1: sweep the live rows (engine icons + status pills).
    const rows = '.conv-item';
    await ctx.move(rows, { duration: 700 });               // first row
    await ctx.pause(600);
    await ctx.move('.conv-item:nth-of-type(3)', { duration: 650 }).catch(() => {});
    await ctx.pause(500);
    // Scroll the sidebar list to show more of the fleet + project tree.
    await ctx.scrollEl('.conv-list, #convList', 260, { duration: 900 });
    await ctx.pause(700);
    await ctx.scrollEl('.conv-list, #convList', -260, { duration: 700 });
    await ctx.pause(400);

    // Scene 2: same fleet as a board.
    await ctx.reloadWith(BOARD, { cursorAt: { x: 500, y: 420 } });
    await ctx.pause(900);
    // Pan across the columns (Icebox / In Progress / Waiting come into view).
    await ctx.scrollEl('#kanbanBoard', 0, { dx: 430, duration: 1400 });
    await ctx.pause(700);
    // Hover a live card, then open it.
    const card = '#kanbanBoard .kanban-card[data-id="11111111-1111-4aaa-aaaa-000000000001"]';
    await ctx.move(card, { duration: 700 });
    await ctx.pause(500);
    await ctx.click(card, { duration: 250 });
    await ctx.pause(1900);
    await ctx.suppressBanner();
  },
};
