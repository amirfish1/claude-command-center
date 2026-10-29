const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('  function rankNsAdvisorChoices(');
const end = source.indexOf('  function markNsAdvisorManual()', start);
assert.ok(start > 0 && end > start);
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);
const rank = context.rankNsAdvisorChoices;
const now = Date.parse('2026-01-01T12:00:00Z');
const engines = {codex: {available: true, efforts: ['low', 'medium', 'high']}, grok: {available: true, efforts: []}};
const catalog = {codex: [{id: 'workhorse', reasoning_efforts: ['medium', 'high']}], grok: [{id: 'current-model'}]};
const choice = (engine, model, effort='') => ({engine, model, effort});
const config = () => ({rankings: {deep: [choice('codex', 'workhorse', 'medium')], routine: [choice('grok', 'current-model'), choice('codex', 'workhorse', 'medium')]}, resources: {}});

test('depth shortlist is a capability gate; it never adds a routine-only model to deep', () => {
  const rows = rank(config(), 'deep', catalog, engines, {}, now);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].engine, 'codex');
  assert.equal(rows[0].effort, 'medium');
});

test('policy, model availability, engine availability and effort support exclude choices', () => {
  for (const model of [{id:'workhorse', policy_blocked:true}, {id:'workhorse', available:false}, {id:'workhorse', reasoning_efforts:['high']}]) {
    assert.ok(rank(config(), 'deep', {...catalog, codex:[model]}, engines, {}, now)[0].unavailable);
  }
  assert.ok(rank(config(), 'deep', catalog, {...engines, codex:{available:false, efforts:['medium']}}, {}, now)[0].unavailable);
});

test('live readings override reported quota and exhausted short windows exclude', () => {
  const data = config();
  data.resources.codex = {remaining_pct: 90, observed_at: new Date(now).toISOString()};
  const live = {engine:'codex', available:true, stale:false, percent_left:0, resets_at:'2026-01-02T12:00:00Z'};
  let row = rank(data, 'deep', catalog, engines, {accounts:[live]}, now)[0];
  assert.equal(row.unavailable, 'Allowance is exhausted');
  row = rank(data, 'deep', catalog, engines, {accounts:[{...live, percent_left:90, windows:[{available:true, stale:false, percent_left:0}]}]}, now)[0];
  assert.match(row.unavailable, /usage window/);
});

test('expired or old reported quota becomes unknown without inventing a refill', () => {
  const data = config();
  for (const resource of [
    {remaining_pct:0, observed_at:'2025-12-30T12:00:00Z'},
    {remaining_pct:0, observed_at:'2026-01-01T11:00:00Z', resets_at:'2026-01-01T11:30:00Z'},
  ]) {
    data.resources.codex = resource;
    const row = rank(data, 'deep', catalog, engines, {}, now)[0];
    assert.equal(row.source, 'unknown');
    assert.equal(row.unavailable, '');
    assert.ok(row.notes.includes('Quota unknown'));
  }
});

test('fresh allowance at risk of expiry can change the preferred eligible model', () => {
  const data = config();
  data.resources.codex = {remaining_pct:90, observed_at:new Date(now).toISOString(), resets_at:'2026-01-01T14:00:00Z'};
  assert.equal(rank(data, 'routine', catalog, engines, {}, now)[0].engine, 'codex');
});

test('unknown quota is neither a full nor an exhausted allowance', () => {
  const row = rank(config(), 'deep', catalog, engines, {}, now)[0];
  assert.equal(row.source, 'unknown');
  assert.equal(row.unavailable, '');
});

test('credit expiry and zero balance exclude the route; subscription readings do not replace credits', () => {
  const data = config();
  const live = {accounts:[{engine:'codex', available:true, stale:false, percent_left:0, resets_at:'2026-01-02T12:00:00Z'}]};
  data.resources.codex = {kind:'credits', balance_usd:100, observed_at:new Date(now).toISOString()};
  assert.equal(rank(data,'deep',catalog,engines,live,now)[0].unavailable, '');
  data.resources.codex.balance_usd = 0;
  assert.match(rank(data,'deep',catalog,engines,{},now)[0].unavailable, /balance/);
  data.resources.codex = {kind:'credits', expires_at:'2026-01-01T11:00:00Z'};
  assert.equal(rank(data,'deep',catalog,engines,{},now)[0].unavailable, 'Credits have expired');
});
