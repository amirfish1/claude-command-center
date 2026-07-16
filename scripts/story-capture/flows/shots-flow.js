// S-F3d: Flow canvas with arranged nodes and edges.
//
// Flow popout mode. Expands the repo clusters so child sessions + edges show,
// runs Organize to line them up, and sets a readable zoom. Cursor-led so the
// canvas reads as a real arranged board.
'use strict';

module.exports = {
  path: '/static/index.html?demo=1&ccc_popout=flow',
  fixtureBase: '/docs/demo/api',
  localStorage: {
    'ccc-last-seen-version': 'demo',
    'ccc-whats-new-dismissed-version': 'demo',
    'ccc-pwa-install-dismissed': '9999999999999',
    'ccc-telemetry-bar-dismissed': '1',
    'ccc-flow-zoom': '0.55',
  },
  async run(ctx) {
    await ctx.pause(1500);
    await ctx.eval(() => {
      const b = (a) => document.querySelector('[data-flow-action="' + a + '"]');
      if (b('expand-all')) b('expand-all').click();
    });
    await ctx.pause(900);
    await ctx.eval(() => {
      const b = (a) => document.querySelector('[data-flow-action="' + a + '"]');
      if (b('organize')) b('organize').click();
    });
    await ctx.pause(1400);
    // Reset pan so the arranged widgets-api cluster + its edges sit high and
    // centred, and clear the transient "Organized" op-toast.
    await ctx.eval(() => {
      const board = document.getElementById('flowBoard');
      if (board) { board.scrollLeft = 120; board.scrollTop = 190; }
    });
    await ctx.pause(500);
    // The Organize op-toast is a class-less fixed div — match it by text.
    // (Copy is its action button; text reads e.g. "Organize: moved 249px total".)
    await ctx.eval(() => {
      document.querySelectorAll('body > div').forEach(el => {
        const t = el.textContent || '';
        if (/Organize[d]?[:\s].*(moved|tight)/i.test(t) || /moved\s+\d+px/i.test(t)) el.remove();
      });
    });
    await ctx.pause(300);
    await ctx.suppressBanner();
  },
};
