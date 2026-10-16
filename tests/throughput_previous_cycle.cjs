// Run with: node tests/throughput_previous_cycle.cjs
// THROUGHPUT-1: the 7-day view and the zoomed 48h view must agree on the %
// the previous billing cycle ended on.
process.env.TZ = 'UTC';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../static/throughput.html'), 'utf8');

function sourceFor(name, nextMarker) {
  const start = html.indexOf(`function ${name}`);
  assert.notEqual(start, -1, `${name} is present`);
  const end = html.indexOf(nextMarker.startsWith('const ') ? nextMarker : `function ${nextMarker}`, start);
  assert.notEqual(end, -1, `${nextMarker} follows ${name}`);
  return html.slice(start, end);
}

const context = {};
vm.runInNewContext(
  sourceFor('chartRowTimeMs', 'throughputSummaryRowTimeMs') +
    sourceFor('throughputSummaryRowTimeMs', 'updateChartZoomButton') +
    sourceFor('buildHourlyZoomRows', 'const chartZoomBtn') +
    sourceFor('buildPreviousQuotaSeries', 'drawQuotaSeries'),
  context,
);

const HOUR = 3600000;
const periodStart = Date.parse('2026-10-14T19:30:00Z'); // reset, not slot-aligned
const now = Date.parse('2026-10-15T18:00:00Z');
const hourKey = ms => new Date(ms).toISOString().slice(0, 13).replace('T', ' ') + ':00';
// 1000 tokens every hour from well before the previous cycle started until now.
const hourly = [];
for (let ms = periodStart - 9 * 24 * HOUR - 30 * 60000; ms < now; ms += HOUR) {
  hourly.push({ hour: hourKey(ms), raw_context_tokens: 1000, output_tokens: 0 });
}
const summary = { hourly };
const ppt = 1 / 1000; // 0.001% per token → 1% per hour

// 7-day view: 3h slots starting 7 days before now.
const weekRows = [];
for (let ms = now - 7 * 24 * HOUR; ms < now; ms += 3 * HOUR) {
  const d = new Date(ms);
  weekRows.push({ hour: d.toISOString().slice(0, 19), _prePeriod: ms < periodStart, _projected: false });
}
// Zoomed view: 1h slots for the last 48h.
const zoomRows = context.buildHourlyZoomRows(hourly, now - 48 * HOUR, now + HOUR, periodStart);

const week = context.buildPreviousQuotaSeries(summary, weekRows, ppt, 3 * HOUR, periodStart);
const zoom = context.buildPreviousQuotaSeries(summary, zoomRows, ppt, HOUR, periodStart);
assert.ok(week && zoom, 'both views draw a previous-cycle trace');

// The previous cycle is [reset - 7d, reset): 168 hourly rows → 168%.
const expected = 168 * 1000 * ppt;
assert.ok(Math.abs(week.currentPct - expected) < 1e-9, `7-day view ends on ${week.currentPct}%, expected ${expected}%`);
assert.ok(Math.abs(zoom.currentPct - expected) < 1e-9, `zoomed view ends on ${zoom.currentPct}%, expected ${expected}%`);
assert.equal(week.currentPct, zoom.currentPct, 'both views agree on where the previous cycle ended');

// Running values never include tokens spent after the reset.
[week, zoom].forEach(series => series.values.forEach(value => {
  if (value != null) assert.ok(value <= expected + 1e-9, `running value ${value} exceeds the cycle total`);
}));

console.log('throughput previous cycle: ok');
