const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  const CACHE_READ_TOKEN_WEIGHT');
const end = source.indexOf('  // A running, cross-turn log', start);
const ctx = vm.createContext({});
vm.runInContext(source.slice(start, end), ctx);
test('Fable 5.1 cache reads use 2.5% in the value and explanation', () => {
  assert.equal(ctx._cacheAdjustedTurnTokens(208770, 1656, 208051, 'claude-fable-5-1'), 7576);
  assert.match(ctx._cacheAdjustedTurnTitle(208770, 1656, 208051, 'claude-fable-5-1'), /2.5%/);
});
test('Opus and original Fable retain their 10% cache discount', () => {
  for (const model of ['claude-opus-5', 'claude-fable-5', '']) {
    assert.equal(ctx._cacheAdjustedTurnTokens(208770, 1656, 208051, model), 23180);
  }
});
test('graph keeps the 30-turn window and passes the session model to its tooltip', () => {
  const graphStart = source.indexOf('  const RAIL_TURN_GRAPH_MAX');
  const graphEnd = source.indexOf('  // Big accumulated-token headline', graphStart);
  ctx.escapeAttr = s => s;
  ctx._formatTokens = n => String(n);
  vm.runInContext(source.slice(graphStart, graphEnd), ctx);
  const rows = Array.from({length: 40}, () => ({tokens_in:208770, tokens_cached:208051, tokens_out:1656}));
  const html = ctx._railTurnGraphHtml(rows, 'claude-fable-5-1');
  assert.equal((html.match(/class="rail-turn-bar/g) || []).length, 30);
  assert.match(html, /7,576 cache-adjusted tokens/);
  assert.match(html, /2.5%/);
});
