const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  // RAIL_QUOTA_COST_START');
const end = source.indexOf('  // RAIL_QUOTA_COST_END', start);
const ctx = vm.createContext({});
if (start >= 0) vm.runInContext(source.slice(start, end), ctx);

test('quota cost uses the matching Claude and Codex defaults', () => {
  assert.equal(ctx.railQuotaEngine({engine: 'Claude'}), 'claude');
  assert.equal(ctx.railQuotaEngine({engine: 'codex'}), 'codex');
  assert.equal(ctx.railMonthlyPlanUsd('claude'), 200);
  assert.equal(ctx.railMonthlyPlanUsd('codex'), 100);
  assert.equal(ctx.railMonthlyPlanUsd('claude', '275.50'), 275.5);
  assert.equal(ctx.railMonthlyPlanUsd('codex', 0), 100);
  assert.equal(ctx.railMonthlyPlanUsd('unknown'), null);
});

test('quota cost applies the historical rate without capping multiweek sessions', () => {
  const result = ctx.railQuotaCostPresentation(
    {engine: 'claude'}, 12.5,
    {available: true, pct_per_usd: 12}, 200,
  );
  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    state: 'ready', engine: 'claude', apiCost: 12.5, contributionPct: 150,
    monthlyPlanUsd: 200, allocatedCost: 70,
  });
});

test('quota cost allows a zero observed contribution without fabricating a charge', () => {
  const result = ctx.railQuotaCostPresentation(
    {engine: 'codex'}, 8,
    {available: true, pct_per_usd: 0}, 100,
  );
  assert.equal(result.state, 'ready');
  assert.equal(result.contributionPct, 0);
  assert.equal(result.allocatedCost, 0);
});

test('quota cost distinguishes pending and unavailable calibration or pricing', () => {
  assert.equal(ctx.railQuotaCostPresentation({engine: 'claude'}, 4, null, 200).state, 'calibrating');
  assert.equal(ctx.railQuotaCostPresentation(
    {engine: 'codex'}, 4, {available: false, reason: 'not enough samples'}, 100,
  ).state, 'unavailable');
  assert.equal(ctx.railQuotaCostPresentation({engine: 'kimi'}, 4, {}, 100).state, 'unavailable');
  assert.equal(ctx.railQuotaCostPresentation({engine: 'claude'}, null, {}, 200).state, 'unavailable');
});

test('missing cost or missing rate never becomes a zero estimate', () => {
  assert.equal(ctx.railQuotaCostPresentation(
    {engine:'claude'}, null, {available:true,pct_per_usd:0.05}, 200,
  ).state,'unavailable');
  assert.equal(ctx.railQuotaCostPresentation(
    {engine:'codex'}, 40, {available:true,pct_per_usd:null}, 100,
  ).state,'unavailable');
});
