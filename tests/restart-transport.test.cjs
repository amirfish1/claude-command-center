const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/restart-transport.js'), 'utf8');

function harness(fetch, options = {}) {
  let now = 0;
  let node;
  const document = {
    body: {appendChild(n) { node = n; }},
    createElement() { return {setAttribute() {}, addEventListener(type, fn) { this[type] = fn; }, appendChild(child) { this.child = child; }}; },
  };
  const window = {fetch, location: {href: 'http://localhost/', origin: 'http://localhost'}};
  vm.runInNewContext(source, {
    window, document, URL, Request, DOMException,
    Date: {now: () => now},
    setTimeout(fn, ms) {
      now += options.expire ? 61000 : ms;
      return setTimeout(fn, options.slow ? 30 : 0);
    }, clearTimeout,
  });
  return {fetch: window.fetch, node: () => node};
}

test('proxy 502/503/504 and network failures recover without leaking an error', async () => {
  for (const failure of [502, 503, 504, 'network']) {
    let calls = 0;
    let h;
    h = harness(async () => {
      if (!calls++) {
        if (failure === 'network') throw new TypeError('Failed to fetch');
        return new Response('proxy unavailable', {status: failure});
      }
      assert.match(h.node().textContent, /restarting.*retrying/);
      return new Response('{"ok":true}');
    });
    const response = await h.fetch('/api/health');
    assert.equal(await response.text(), '{"ok":true}');
    assert.equal(calls, 2);
    assert.equal(h.node().hidden, true);
  }
});

test('late POST explicitly rejected before execution can retry its Request body', async () => {
  const bodies = [];
  const h = harness(async request => {
    bodies.push(await request.text());
    return bodies.length === 1
      ? new Response('{}', {status: 503, headers: {'X-CCC-Restarting': '1'}})
      : new Response('{"saved":true}');
  });
  const response = await h.fetch(new Request('http://localhost/api/save', {method: 'POST', body: 'save-once'}));
  assert.equal(response.status, 200);
  assert.deepEqual(bodies, ['save-once', 'save-once']);
});

test('ambiguous writes reach their caller without replay or a global unsaved-change banner', async () => {
  for (const failure of ['network', 502, 503, 504]) {
    let calls = 0;
    const h = harness(async () => {
      calls++;
      if (failure === 'network') throw new TypeError('Failed to fetch');
      return new Response('{}', {status: failure});
    });
    const request = h.fetch('/api/save', {method: 'POST', body: '{}'});
    if (failure === 'network') await assert.rejects(request, /Check whether your change was saved/);
    else assert.equal((await request).status, failure);
    assert.equal(calls, 1);
    assert.equal(h.node(), undefined);
  }
});

test('an uncertain write after a confirmed restart rejection clears only its retry notice', async () => {
  for (const failure of ['network', 502]) {
    let calls = 0;
    const h = harness(async () => {
      calls++;
      if (calls === 1) return new Response('{}', {status: 503, headers: {'X-CCC-Restarting': '1'}});
      if (failure === 'network') throw new TypeError('Failed to fetch');
      return new Response('{}', {status: failure});
    });
    const request = h.fetch('/api/save', {method: 'POST', body: '{}'});
    if (failure === 'network') await assert.rejects(request, /Check whether your change was saved/);
    else assert.equal((await request).status, failure);
    assert.equal(calls, 2, 'only the explicitly unstarted attempt can be replayed');
    assert.equal(h.node().hidden, true);
  }
});

test('abort stops the retry and clears the recovery status', async () => {
  let calls = 0;
  const h = harness(async () => { calls++; return new Response('', {status: 502}); }, {slow: true});
  const controller = new AbortController();
  const pending = h.fetch('/api/health', {signal: controller.signal});
  await new Promise(resolve => setTimeout(resolve, 5));
  controller.abort();
  await assert.rejects(pending, {name: 'AbortError'});
  assert.equal(calls, 1);
  assert.equal(h.node().hidden, true);
});

test('recovery is bounded and provides a next step when the service stays down', async () => {
  let calls = 0;
  const h = harness(async () => { calls++; return new Response('', {status: 502}); }, {expire: true});
  assert.equal((await h.fetch('/api/health')).status, 502);
  assert.equal(calls, 2);
  assert.match(h.node().textContent, /still unavailable/);
});

test('validation, authentication, unrelated URLs and ordinary errors pass through', async () => {
  for (const status of [400, 401, 403, 404, 409, 429, 500]) {
    let calls = 0;
    const h = harness(async () => { calls++; return new Response('{}', {status}); });
    assert.equal((await h.fetch('/api/health')).status, status);
    assert.equal(calls, 1);
    assert.equal(!!h.node() && !h.node().hidden, false);
  }
  const h = harness(async () => new Response('', {status: 502}));
  assert.equal((await h.fetch('https://example.org/api/health')).status, 502);
  assert.equal((await h.fetch('/static/example.json')).status, 502);
  assert.equal(h.node(), undefined);
});


test('receipt-aware endpoint helpers retain their own retries and status', async () => {
  let calls = 0;
  const h = harness(async (input, init) => {
    calls++;
    assert.equal(input, '/api/ux-fixes/enqueue');
    assert.equal(init.cccRetryHandled, true);
    return new Response('', {status: 502});
  });
  const response = await h.fetch('/api/ux-fixes/enqueue', {
    method: 'POST', body: '{}', cccRetryHandled: true,
  });
  assert.equal(response.status, 502);
  assert.equal(calls, 1);
  assert.equal(h.node(), undefined);
});


test('unrelated success cannot erase exhausted recovery; user can dismiss it', async () => {
  let calls = 0;
  const h = harness(async () => new Response('{}', {status: calls++ < 2 ? 502 : 200}), {expire: true});
  await h.fetch('/api/health');
  assert.match(h.node().textContent, /still unavailable/);
  await h.fetch('/api/health');
  assert.equal(h.node().hidden, false);
  assert.match(h.node().textContent, /still unavailable/);
  h.node().child.click();
  assert.equal(h.node().hidden, true);
});


test('best-effort diagnostics cannot create a misleading unsaved-change warning', async () => {
  let calls = 0;
  const h = harness(async () => { calls++; return new Response('', {status: 502}); });
  for (const url of ['/api/client-log', '/api/free-runtime/tts-log']) {
    assert.equal((await h.fetch(url, {method: 'POST', body: '{}'})).status, 502);
  }
  assert.equal(calls, 2);
  assert.equal(h.node(), undefined);
});
