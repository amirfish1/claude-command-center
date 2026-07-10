// V-07: Flow canvas — expand, organize, drag a node, zoom.
//
// The Flow popout (?ccc_popout=flow) on the demo fixtures. Expand the repo
// clusters so session nodes show, Organize to lay them out, drag one node to
// a new spot (pointer-based — genuinely works, position persists to
// localStorage), then zoom in a step.
'use strict';
const { CLEAN } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1&ccc_popout=flow',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...CLEAN, 'ccc-flow-zoom': '0.7' },
  lead: 1500,
  tail: 1800,
  async run(ctx) {
    // Expand clusters so sessions are visible, then lay the board out.
    await ctx.click('[data-flow-action="expand-all"]', { duration: 700 });
    await ctx.pause(900);
    const organize = await ctx.findByText('button', 'Organize');
    await ctx.click(organize, { duration: 600 });
    await ctx.pause(1400);
    // Drag a session node to a new spot.
    const node = await ctx.centerOf('.flow-node[data-flow-kind="session"]')
      .catch(() => ctx.centerOf('.flow-node'));
    await ctx.pressDrag(node, { x: node.x + 240, y: node.y + 170 }, { duration: 1100 });
    await ctx.pause(700);
    // Zoom in one step so the cards read.
    await ctx.click('[data-flow-action="zoom-in"]', { duration: 650 });
    await ctx.pause(500);
    await ctx.click('[data-flow-action="zoom-in"]', { duration: 200, settle: 400 });
    // Rest the cursor over the board, not the toolbar.
    await ctx.move({ x: 720, y: 470 }, { duration: 600 });
    await ctx.pause(700);
    await ctx.suppressBanner();
  },
};
