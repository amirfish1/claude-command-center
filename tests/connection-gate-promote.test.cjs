// CCC-143: a hover prefetch parked in the background pool must be sent at
// once when the user opens that row, not after every queued poll.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function gateHarness() {
  const sent = [];
  const pending = [];
  const nativeFetch = (input) => {
    sent.push(String(input));
    return new Promise((resolve) => pending.push({ input: String(input), resolve }));
  };
  const window = {
    fetch: nativeFetch,
    location: { href: 'http://127.0.0.1:8091/', origin: 'http://127.0.0.1:8091' },
    addEventListener() {},
  };
  const ctx = vm.createContext({
    window, URL, Promise, Response: class { constructor(b, o) { Object.assign(this, o); } },
    DOMException: Error,
    performance: { now: () => 1e9, getEntriesByType: () => [] },
  });
  const start = source.indexOf('  (function installConnectionSlotGate() {');
  const end = source.indexOf('  // ── Demo mode', start);
  assert.ok(start > 0 && end > start, 'gate block found');
  vm.runInContext(source.slice(start, end), ctx);
  return { window, sent, pending };
}

test('promote sends a queued prefetch now; others stay queued', async () => {
  const h = gateHarness();
  const gate = h.window.__cccConnectionGate;
  for (let i = 0; i < 12; i++) h.window.fetch('/api/poll-' + i);
  const tail = h.window.fetch('/api/conversations/abc?tail=80');
  assert.ok(!h.sent.includes('/api/conversations/abc?tail=80'), 'prefetch starts parked in the pool');
  const queuedBefore = gate.queued;

  assert.equal(gate.promote(u => u.indexOf('/api/conversations/abc?tail=') >= 0), 1);
  assert.ok(h.sent.includes('/api/conversations/abc?tail=80'), 'promoted fetch went out immediately');
  assert.equal(gate.queued, queuedBefore - 1);
  assert.equal(gate.directActive, 1);

  const resp = { status: 200, body: null };
  h.pending.find(p => p.input === '/api/conversations/abc?tail=80').resolve(resp);
  assert.equal(await tail, resp, 'the original promise resolves with the response');
  await new Promise(r => setImmediate(r));
  assert.equal(gate.directActive, 0);
  assert.equal(gate.promote(u => u.indexOf('/api/conversations/abc?tail=') >= 0), 0, 'nothing left to promote');
});

test('cccPriority user bypasses the pool even with no recent gesture', () => {
  const h = gateHarness();
  for (let i = 0; i < 12; i++) h.window.fetch('/api/poll-' + i);
  h.window.fetch('/api/conversations/xyz?tail=80', { cccPriority: 'user' });
  assert.ok(h.sent.includes('/api/conversations/xyz?tail=80'));
});
