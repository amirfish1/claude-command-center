// V-16 (new): the project tree — organize work that outgrew a flat list.
//
// LIST view (2026-07-16 direction). The sidebar's project tree groups sessions
// under their repos (widgets-api, blog-engine, marketing-site). Scroll down to
// the tree, collapse a repo cluster and expand it again so the grouping reads,
// then pan the tree to show the other clusters. Pain rows 13/11 (F3).
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...LIST, 'ccc-sidebar-width': '470' },
  lead: 1400,
  tail: 1700,
  async run(ctx) {
    // Quick sweep of the flat current-sessions list.
    await ctx.move('.conv-item', { duration: 600 });
    await ctx.pause(450);
    // Bring the project tree into view.
    await ctx.eval(() => {
      const h = document.querySelector('[data-role="project-tree-header"], .conv-project-tree-header');
      if (h) h.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
    await ctx.pause(800);
    // Collapse the first repo cluster, then expand it — shows the grouping.
    // Use the collapse ARROW (not the header, which opens the object).
    const arrow = '[data-role="folder-group-collapse"]';
    await ctx.move(arrow, { duration: 550 }).catch(() => {});
    await ctx.pause(400);
    await ctx.click(arrow, { duration: 300 }).catch(() => {});
    await ctx.pause(900);
    await ctx.click(arrow, { duration: 300 }).catch(() => {});
    await ctx.pause(800);
    // Pan the tree to reveal the other repo clusters.
    await ctx.scrollEl('[data-role="project-tree-scroll"], .conv-project-tree-scroll', 260, { duration: 900 }).catch(() => {});
    await ctx.pause(800);
    await ctx.suppressBanner();
  },
};
