const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
const start = app.indexOf('  async function fetchInjectWithRetry(');
const source = app.slice(start, app.indexOf('\n  }', start) + 4);
function harness(statuses) {
  const calls = [], waits = [], cancelled = [];
  const context = {
    fetch: async (url, options) => {
      calls.push({ url, options });
      const status = statuses.shift();
      if (status instanceof Error) throw status;
      return { status, body: { cancel: async () => cancelled.push(status) } };
    },
    setTimeout: (fn, ms) => { waits.push(ms); fn(); },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { calls, waits, cancelled, send: context.fetchInjectWithRetry };
}
test('502 retries preserve the exact request and idempotency key', async () => {
  const h = harness([502, 502, 200]);
  const options = { method: 'POST', body: JSON.stringify({ session_id: 'test-session', text: 'Hello', idempotency_key: 'test-inject' }) };
  assert.equal((await h.send('/api/inject-input', options)).status, 200);
  assert.equal(h.calls.length, 3);
  assert.ok(h.calls.every(c => c.url === '/api/inject-input' && c.options === options));
  assert.deepEqual(h.waits, [1000, 2000]);
  assert.deepEqual(h.cancelled, [502, 502]);
});
test('persistent 502 returns the final response after two retries', async () => {
  const h = harness([502, 502, 502, 200]);
  assert.equal((await h.send('/api/inject-input', {})).status, 502);
  assert.equal(h.calls.length, 3);
});
for (const status of [200, 400, 401, 409, 429, 500, 503]) {
  test(`HTTP ${status} is returned without retry`, async () => {
    const h = harness([status]);
    assert.equal((await h.send('/api/inject-input', {})).status, status);
    assert.equal(h.calls.length, 1);
  });
}
test('ambiguous network failures are not automatically replayed', async () => {
  const h = harness([new Error('network failure')]);
  await assert.rejects(h.send('/api/inject-input', {}), /network failure/);
  assert.equal(h.calls.length, 1);
});
