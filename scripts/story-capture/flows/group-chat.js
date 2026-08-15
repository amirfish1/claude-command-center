// V-16: group chat — several agent sessions in one shared thread.
//
// LIST view. Opens the seeded demo group chat ("Ship the v5.7 release notes")
// where three agent sessions — Planner, Builder, Reviewer — take turns in one
// shared thread, scrolls the transcript, then starts typing a follow-up so
// the "post once, everyone is pinged" idea reads on screen. Requires the
// seeded group-chat fixtures (docs/demo/api/group-chats/active.json +
// group-chat/read.json).
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...LIST, 'ccc-sidebar-width': '470' },
  lead: 1500,
  tail: 2000,
  async run(ctx) {
    await ctx.pause(1200); // let pollGcActive load the seeded chat
    // Prefer the real click path; fall back to opening the reader directly
    // (the row may render in a collapsed group-chats section off-screen).
    await ctx.eval(() => {
      const row = document.querySelector('[data-role="ingroupchat-row"]');
      if (row) { row.click(); return true; }
      if (typeof openGroupChatReader === 'function') {
        openGroupChatReader('~/.claude/group-chats/gc-demo-0001.md',
          'Ship the v5.7 release notes', 'topic', true, 'gc-demo-0001');
        return true;
      }
      return false;
    });
    await ctx.waitFor('#gcReaderBody article.gc-message').catch(() => {});
    await ctx.pause(1600);
    await ctx.suppressBanner();
    // Read down the thread, then back up a touch.
    await ctx.scrollEl('#gcReaderBody', 320, { duration: 1000 });
    await ctx.pause(900);
    await ctx.scrollEl('#gcReaderBody', 320, { duration: 900 });
    await ctx.pause(800);
    // Start a reply so the composer reads as live.
    await ctx.waitFor('#gcHumanInput').catch(() => {});
    const hasComposer = await ctx.eval(() => !!document.querySelector('#gcHumanInput'));
    if (hasComposer) {
      await ctx.type('#gcHumanInput', 'Reviewer — hold the merge until the changelog lands', { perChar: 45 });
      await ctx.pause(1200);
    }
  },
};
