const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./legacy-dashboard-source.cjs');
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

// Expiry-first scoring with the "good enough" cutoff.
const now2 = Date.parse('2026-11-23T00:15:00Z');
const engines2 = {claude:{available:true, efforts:['medium']}, devin:{available:true, efforts:[]}, codex:{available:true, efforts:['medium']}};
const catalog2 = {claude:[{id:'opus'}], devin:[{id:'swe'}], codex:[{id:'sol'}]};
const fresh = (extra) => ({observed_at:new Date(now2).toISOString(), ...extra});
const real = () => ({
  rankings:{deep:[choice('codex','sol','medium'), choice('claude','opus','medium'), choice('devin','swe')]},
  resources:{
    codex:fresh({remaining_pct:25, resets_at:'2026-11-26T05:05:00Z'}),
    claude:fresh({remaining_pct:82, resets_at:'2026-11-27T07:00:00Z'}),
    devin:fresh({remaining_pct:100, resets_at:'2026-11-23T07:58:00Z', window_hours:24}),
  }});

test('a daily allowance expiring soon outranks weekly spare; behind-pace accounts sink', () => {
  const rows = rank(real(), 'deep', catalog2, engines2, {}, now2);
  assert.deepEqual([...rows.map(r => r.engine)], ['devin', 'claude', 'codex']);
  assert.ok(rows[0].pace > 60 && rows[1].pace > 15 && rows[2].pace < -15);
});

test('rows below the good-enough cutoff are fallbacks even when most urgent', () => {
  const data = real(); data.cutoffs = {deep: 2};
  const rows = rank(data, 'deep', catalog2, engines2, {}, now2);
  assert.deepEqual([...rows.map(r => r.engine)], ['claude', 'codex', 'devin']);
  assert.equal(rows[2].tier, 1);
});

test('banked resets and a plan end date raise urgency', () => {
  const data = real(); data.cutoffs = {deep: 2};
  data.resources.claude.remaining_pct = 10; // behind pace on its own
  assert.equal(rank(data, 'deep', catalog2, engines2, {}, now2)[0].engine, 'codex');
  data.resources.claude.banked_resets = [{label:'weekly', count:2, expires_at:'2026-12-10T00:00:00Z'}];
  assert.equal(rank(data, 'deep', catalog2, engines2, {}, now2)[0].engine, 'claude');
  delete data.resources.claude.banked_resets;
  data.resources.claude.plan_ends_at = '2026-11-30T00:00:00Z';
  const top = rank(data, 'deep', catalog2, engines2, {}, now2)[0];
  assert.equal(top.engine, 'claude');
  assert.ok(top.notes.some(n => /Plan ends in/.test(n)));
  data.resources.claude.plan_ends_at = '2026-11-01T00:00:00Z';
  assert.equal(rank(data, 'deep', catalog2, engines2, {}, now2).find(r => r.engine === 'claude').unavailable, 'Plan has ended');
});
