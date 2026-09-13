// Intro spawn: start a session from a GitHub issue in LIST view.
//
// The in-app kanban board is no longer a product surface. Expand the
// sidebar GH Issues section and click the row Start control. Demo mode
// stubs the spawn and shows the read-only banner.
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: {
    ...LIST,
    'ccc-sidebar-width': '520',
    'ccc-ghissues-collapsed': '0',
  },
  lead: 1500,
  tail: 2400,
  async run(ctx) {
    await ctx.eval(() => {
      const header = document.querySelector('[data-role="ghissues-toggle"]');
      const section = document.querySelector('[data-role="ghissues-section"]');
      if (header && section && section.classList.contains('collapsed')) {
        header.click();
      }
    });
    await ctx.pause(700);
    await ctx.eval(() => {
      const list = document.getElementById('convList');
      const row = list && list.querySelector('.conv-item.is-github-issue, .conv-ghissues-list .conv-item');
      if (row) row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
    await ctx.pause(600);
    const row = '.conv-ghissues-list .conv-item, .conv-item.is-github-issue';
    await ctx.move(row, { duration: 700 }).catch(() => {});
    await ctx.pause(800);
    await ctx.allowBanner();
    const start = '.conv-ghissues-list .conv-start-btn, .conv-item.is-github-issue .conv-start-btn';
    await ctx.click(start, { duration: 400 }).catch(async () => {
      await ctx.click('#sidebarNewBtn', { duration: 400 });
    });
    await ctx.pause(2000);
  },
};
