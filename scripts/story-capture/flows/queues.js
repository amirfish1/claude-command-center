// V-17: durable queues, one inbox.
//
// Lands on the sidebar Queues tab (scope: All queues) backed by the seeded
// demo fixtures — BUGS (GitHub-backed, draining), CONTENT (two workers),
// WEBSITE (one ticket needs input), RELEASE (waiting). Sweeps the ticket
// rows, opens the queue picker (NEEDS YOU / RECENT / ALL QUEUES), filters it,
// then scopes into WEBSITE and opens the ticket that needs a human answer.
// Requires docs/demo/api/queue/{list,status}.json + wt/workers.json +
// watchtower/alerts.json fixtures.
'use strict';
const { QUEUES } = require('./_seeds.js');

module.exports = {
  path: '/static/index.html?demo=1',
  fixtureBase: '/docs/demo/api',
  localStorage: QUEUES,
  lead: 1800,
  tail: 1800,
  async run(ctx) {
    // Land: all queues, one inbox. Let the panel's first poll settle.
    await ctx.waitFor('#sidebarQueueList .fq-row', 9000);
    await ctx.pause(1200);

    // Sweep a couple of ticket rows (chips, priorities, run buttons).
    const firstRow = '#sidebarQueueList .fq-row';
    await ctx.move(firstRow, { duration: 700 });
    await ctx.pause(700);
    const secondRow = await ctx.eval(() => {
      const rows = [...document.querySelectorAll('#sidebarQueueList .fq-row')];
      return rows.length > 1;
    });
    if (secondRow) {
      await ctx.eval(() => {
        const rows = [...document.querySelectorAll('#sidebarQueueList .fq-row')];
        const r = rows[1].getBoundingClientRect();
        window.__capCursor.moveTo(r.left + r.width / 2, r.top + r.height / 2, 550);
      });
      await ctx.pause(700);
    }

    // Open the queue picker: NEEDS YOU / RECENT / ALL QUEUES.
    await ctx.click('#queueScopeTrigger', { duration: 600, settle: 400 });
    await ctx.waitFor('#queuePickerCard .fq-qp-row', 5000);
    await ctx.pause(1500);
    // Glide down the picker rows so each group reads.
    await ctx.eval(() => {
      const rows = [...document.querySelectorAll('#queuePickerCard .fq-qp-row')];
      const r = rows[Math.min(2, rows.length - 1)].getBoundingClientRect();
      window.__capCursor.moveTo(r.left + r.width / 2, r.top + r.height / 2, 700);
    });
    await ctx.pause(900);

    // Type a filter that narrows to the WEBSITE queue row.
    await ctx.type('#queuePickerCard .fq-qp-filter-input', 'web', { perChar: 120 });
    await ctx.pause(1400);

    // Pick the WEBSITE queue (it carries the needs-input ticket).
    const siteRow = await ctx.eval(() => {
      const rows = [...document.querySelectorAll('#queuePickerCard .fq-qp-row')];
      const row = rows.find(r => (r.textContent || '').includes('WEBSITE'));
      if (!row) return false;
      const r = row.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    });
    if (siteRow) {
      await ctx.click(siteRow, { duration: 650, settle: 500 });
    } else {
      await ctx.click('#queuePickerCard .fq-qp-esc', { duration: 500, settle: 300 }).catch(() => {});
    }
    await ctx.pause(1400);

    // Open the ticket that's blocked on a human answer.
    const blocked = await ctx.eval(() => {
      const row = [...document.querySelectorAll('#sidebarQueueList .fq-row')]
        .find(r => r.classList.contains('is-blocked'));
      if (!row) return null;
      const r = row.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    });
    if (blocked) {
      await ctx.click(blocked, { duration: 650, settle: 600 });
      await ctx.waitFor('#uxqItemModal', 5000).catch(() => {});
      await ctx.pause(2200);
    }
    await ctx.suppressBanner();
  },
};
