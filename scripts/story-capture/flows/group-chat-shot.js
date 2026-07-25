// S-F4b: group chat with several agent sessions in one thread.
//
// LIST view. Opens the seeded demo group chat ("Ship the v5.7 release notes")
// where three agent sessions — Planner, Builder, Reviewer — take turns in one
// shared thread. Requires the seeded group-chat fixtures
// (docs/demo/api/group-chats/active.json + group-chat/read.json).
'use strict';
const { LIST } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: { ...LIST, 'ccc-sidebar-width': '470' },
  async run(ctx) {
    await ctx.pause(1200); // let pollGcActive load the seeded chat
    // Prefer the real click path; fall back to opening the reader directly
    // (the row may render in a collapsed group-chats section off-screen).
    const clicked = await ctx.eval(() => {
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
    await ctx.pause(1700);
    await ctx.suppressBanner();
  },
};
