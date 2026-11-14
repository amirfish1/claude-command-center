const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/q2.js', 'utf8');
const source = app.slice(app.indexOf('  function fileTicket(btn)'), app.indexOf('  // ── queue configuration'));

function fixture(result) {
  const notices = [], opened = [], remembered = [];
  const box = {value: 'Fix the button'};
  const ctx = vm.createContext({
    state: {queue: 'TEST', items: []}, document: {getElementById: () => box},
    note: (...args) => notices.push(args), closeModal() {},
    openNewTicket: () => opened.push(true),
    cccSubmitQueueTicket: result,
    rememberFiledTicket: item => remembered.push(item),
    mergeFiledTickets: items => items, markNewTicket() {},
    refresh: async () => {}, selectTicket() {},
  });
  ctx.window = ctx;
  vm.runInContext(source, ctx);
  return {ctx, notices, opened, remembered, box};
}

test('queue page stays quiet and does not reopen the composer for unconfirmed saves', async () => {
  const h = fixture(async () => {throw Object.assign(new Error('Ticket confirmation pending'), {pending: true});});
  await vm.runInContext('fileTicket({})', h.ctx);
  await new Promise(setImmediate);
  assert.deepEqual(h.notices, [['Filing ticket in TEST…', 'pending']]);
  assert.equal(h.opened.length, 0);
  assert.equal(h.remembered.length, 0, 'never invent a confirmed save');
});

test('queue page still alerts on rejected saves and restores the draft', async () => {
  const h = fixture(async () => {throw Object.assign(new Error('note required'), {rejected: true});});
  await vm.runInContext('fileTicket({})', h.ctx);
  await new Promise(setImmediate);
  assert.deepEqual(h.notices.at(-1), ['Could not file ticket: note required', 'error']);
  assert.equal(h.opened.length, 1);
  assert.equal(h.box.value, 'Fix the button');
});

test('queue page still reports the confirmed ticket reference', async () => {
  const h = fixture(async () => ({ok: true, item: {ref: 'TEST-1'}}));
  await vm.runInContext('fileTicket({})', h.ctx);
  await new Promise(setImmediate);
  assert.deepEqual(h.notices.at(-1), ['Filed TEST-1', 'success']);
  assert.equal(h.remembered[0].ref, 'TEST-1');
});
