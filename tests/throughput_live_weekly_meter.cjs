// Run with: node tests/throughput_live_weekly_meter.cjs
// Exercises the page's pure live-quota overlay without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../static/throughput.html'), 'utf8');

function sourceFor(name, nextName) {
  const start = html.indexOf(`function ${name}`);
  assert.notEqual(start, -1, `${name} is present`);
  const end = html.indexOf(`function ${nextName}`, start);
  assert.notEqual(end, -1, `${nextName} follows ${name}`);
  return html.slice(start, end);
}

const context = { LIVE_WEEKLY_USAGE_MAX_AGE_MS: 120000 };
vm.runInNewContext(
  sourceFor('liveCodexWeeklyQuota', 'overlayLiveCodexWeeklyQuota') +
    sourceFor('overlayLiveCodexWeeklyQuota', 'readActiveThroughputBootstrap'),
  context,
);

const nextWeek = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
const current = (pct, fetchedAt = new Date().toISOString()) => ({
  ok: true,
  fetched_at: fetchedAt,
  codex: {
    stale: false,
    plan_type: 'prolite',
    weekly: { pct, resets_at: nextWeek },
    pace: {
      weekly_pct: pct,
      weekly_resets_at: nextWeek,
      week_start: '2026-09-07T18:41:55-07:00',
    },
  },
});
const bootstrap = (pct) => ({
  available: true,
  display_pct: 52,
  codex: { ok: true, weekly_pct: pct, weekly_resets_at: '2026-09-13T04:18:05Z' },
});

const live22 = context.liveCodexWeeklyQuota(current(22));
assert.equal(context.overlayLiveCodexWeeklyQuota(bootstrap(5), live22).codex.weekly_pct, 22,
  'a live 22% quota replaces a stale 5% bootstrap');
assert.equal(context.overlayLiveCodexWeeklyQuota(bootstrap(22), context.liveCodexWeeklyQuota(current(1))).codex.weekly_pct, 1,
  'a real reset may decrease the quota');
assert.equal(context.overlayLiveCodexWeeklyQuota(bootstrap(5), live22).codex.weekly_pct, 22,
  'reapplying an old bootstrap preserves the already-fetched live quota');
const newerBootstrap = bootstrap(5);
newerBootstrap.codex.fetched_at = new Date(Date.now() + 60_000).toISOString();
assert.equal(context.overlayLiveCodexWeeklyQuota(newerBootstrap, live22).codex.weekly_pct, 5,
  'a newer bootstrap reading wins over an older live poll');
assert.equal(context.liveCodexWeeklyQuota({ ok: true, codex: { weekly: { pct: null, resets_at: nextWeek } } }), null,
  'an unavailable provider stays unknown instead of becoming 0%');

console.log('throughput live weekly meter: ok');
