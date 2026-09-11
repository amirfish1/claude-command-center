const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  // RAIL_USAGE_BREAKDOWN_START');
const end = source.indexOf('  // RAIL_USAGE_BREAKDOWN_END', start);
const ctx = vm.createContext({});
if (start >= 0) vm.runInContext(source.slice(start, end), ctx);
const usage = {
 total_input_tokens: 1000000, total_cache_creation_tokens: 1000000,
 total_cache_read_tokens: 1000000, total_output_tokens: 1000000,
 cost_breakdown_usd: {input:10, cache_creation:12.5, cache_read:0.25, output:50},
};
test('three disjoint buckets count and charge cache writes only once', () => {
 const p = ctx.railUsageBreakdown(usage);
 assert.equal(p.rows.length,3);
 assert.equal(p.rows[0].tokens,2000000);
 assert.equal(p.rows[0].cost,22.5);
 assert.equal(p.rows[0].rate,11.25);
 assert.equal(p.rows[0].baseRate,10);
 assert.equal(p.rows[0].writeRate,12.5);
 assert.equal(p.rows[1].rate,0.25);
 assert.equal(p.rows[2].rate,50);
 assert.equal(p.totalTokens,4000000);
 assert.equal(p.totalCost,72.75);
});
test('missing pricing stays unavailable rather than appearing free', () => {
 const p = ctx.railUsageBreakdown({total_input_tokens:10});
 assert.equal(p.rows[0].cost,null);
 assert.equal(p.rows[0].rate,null);
 assert.equal(p.totalCost,null);
});
test('zero token buckets do not divide by zero', () => {
 const p = ctx.railUsageBreakdown({cost_breakdown_usd:{input:0,cache_creation:0,cache_read:0,output:0}});
 assert.equal(p.totalCost,0);
 assert.equal(p.totalTokens,0);
 assert.equal(p.rows[0].rate,null);
});
test('rendered breakdown shows all three rates, included writes, and total', () => {
 const html = ctx.railUsageBreakdownHtml(ctx.railUsageBreakdown(usage));
 assert.equal((html.match(/class="rail-usage-row"/g) || []).length,3);
 assert.match(html,/2,000,000 tokens · \$11.25 \/ MTok blended/);
 assert.match(html,/1,000,000 cache-write tokens included/);
 assert.match(html,/\$10.00 base \/ \$12.50 cache write per MTok/);
 assert.match(html,/\$0.25 \/ MTok/);
 assert.match(html,/\$50.00 \/ MTok/);
 assert.match(html,/Total · 4,000,000 tokens · \$72.7500/);
});
