const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/q2.js', 'utf8');
test('standalone board retains the confirmed record until the list catches up', () => {
  const start = source.indexOf('  var filedTickets = {}');
  const ctx = vm.createContext({});
  vm.runInContext(source.slice(start, source.indexOf('  // A poll-driven', start)), ctx);
  const saved = {ref:'TEST-1', note:'A confirmed ticket', status:'open'};
  ctx.rememberFiledTicket(saved);
  assert.equal(ctx.mergeFiledTickets([])[0], saved);
  assert.equal(ctx.mergeFiledTickets([])[0], saved, 'stale polls cannot erase the receipt');
  const fresh = {...saved, status:'in_progress'};
  const merged = ctx.mergeFiledTickets([fresh]);
  assert.equal(merged.length, 1);
  assert.equal(merged[0], fresh, 'fresh server data supersedes the receipt');
  assert.equal(ctx.mergeFiledTickets([]).length, 0, 'receipt is released after server catches up');
});
