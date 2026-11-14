const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('static/queue-ticket-submit.js', 'utf8');
const reply = (status, data) => ({ok: status >= 200 && status < 300, status, json: async () => data});
function fixture(fetch, storage = new Map()) {
  const calls = [], retries = [];
  const ctx = vm.createContext({AbortController, crypto: require('node:crypto').webcrypto,
    sessionStorage: {getItem: k => storage.get(k), setItem: (k,v) => storage.set(k,v)},
    fetch: async (url, opts) => {calls.push({url, opts}); return fetch(calls.length, opts);},
    setTimeout: (fn, ms) => {if (ms < 10000) queueMicrotask(fn); return 1;}, clearTimeout: () => {},
  });
  ctx.window = ctx;
  vm.runInContext(source, ctx);
  return {submit: payload => ctx.cccSubmitQueueTicket(payload, s => retries.push(s)), calls, retries, storage};
}
for (const lost of ['502', 'timeout', 'empty', 'pending']) {
  test(`lost ${lost} confirmation retries the exact same key and returns the saved ref`, async () => {
    const h = fixture(async n => {
      if (n > 1) return reply(200, {ok:true, item:{ref:'TEST-1'}});
      if (lost === 'timeout') throw Error('The operation was aborted');
      return reply(lost === '502' ? 502 : lost === 'pending' ? 202 : 200,
        lost === 'pending' ? {ok:false, pending:true} : null);
    });
    assert.equal((await h.submit({note:'Fix it', project:'TEST'})).item.ref, 'TEST-1');
    assert.equal(h.calls.length, 2);
    assert.equal(h.calls[0].opts.body, h.calls[1].opts.body);
    assert.equal(h.calls[0].opts.cccRetryHandled, true, 'dedicated helper owns write recovery');
    assert.match(JSON.parse(h.calls[0].opts.body).idempotency_key, /^[a-f0-9-]{36}$/);
    assert.equal(h.retries.length, 1);
  });
}
test('persistent outage remains pending and a retry after reload reuses the saved key', async () => {
  const h = fixture(async () => reply(502, null));
  await assert.rejects(h.submit({note:'Fix it'}), e => e.pending === true);
  assert.equal(h.calls.length, 4);
  const reload = fixture(async () => reply(200, {ok:true, item:{ref:'TEST-1'}}), h.storage);
  await reload.submit({note:'Fix it'});
  assert.equal(h.calls[0].opts.body, reload.calls[0].opts.body);
  await reload.submit({note:'Fix it'});
  assert.notEqual(reload.calls[0].opts.body, reload.calls[1].opts.body, 'intentional second ticket gets a new key');
});
test('explicit validation failure is rejected immediately', async () => {
  const h = fixture(async () => reply(400, {ok:false, error:'note required'}));
  await assert.rejects(h.submit({note:''}), e => e.rejected && !e.pending);
  assert.equal(h.calls.length, 1);
});
test('slow acknowledged saves keep checking and explain which ticket is pending', async () => {
  const h = fixture(async n => n < 7
    ? reply(202, {ok:false, pending:true, error:'Ticket save is still running'})
    : reply(200, {ok:true, item:{ref:'TEST-2'}}));
  assert.equal((await h.submit({note:'Fix the button', project:'TEST'})).item.ref, 'TEST-2');
  assert.equal(h.calls.length, 7);
  assert.equal(new Set(h.calls.map(c => c.opts.body)).size, 1);
  assert.match(h.retries[0], /TEST.*Fix the button.*Ticket save is still running/);
});
test('HTTP pages without randomUUID can still submit with corrupt saved state', async () => {
  const ctx = vm.createContext({AbortController, crypto: {},
    sessionStorage: {getItem: () => 'null', setItem: () => {}},
    setTimeout: () => 1, clearTimeout: () => {},
    fetch: async (_, opts) => {
      assert.match(JSON.parse(opts.body).idempotency_key, /^ticket-[a-z0-9-]+$/);
      return reply(200, {ok:true, item:{ref:'TEST-1'}});
    },
  });
  ctx.window = ctx;
  vm.runInContext(source, ctx);
  assert.equal((await ctx.cccSubmitQueueTicket({note:'Fix it'})).item.ref, 'TEST-1');
});
