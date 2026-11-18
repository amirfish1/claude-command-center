// CCC-143: a hover prefetch parked in the background pool must be sent at
// once when the user opens that row, not after every queued poll.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = require('./legacy-dashboard-source.cjs');

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

test('visible sidebar reads lead queued polls in FIFO order while keeping the user slot free', async () => {
  const h = gateHarness(), gate = h.window.__cccConnectionGate;
  for (let i = 0; i < 12; i++) h.window.fetch('/api/poll-' + i);
  const first = '/api/conversations/list?window=7d&initial=1';
  const second = '/api/conversations/list?window=7d';
  h.window.fetch(first);
  h.window.fetch(second);
  assert.equal(h.sent.length, 5, 'list reads must still wait for a pool slot');
  assert.equal(gate.directActive, 0);
  h.pending[0].resolve({status:200, body:null});
  await new Promise(setImmediate);
  assert.equal(h.sent[5], first, 'initial rows go before optional queued polls');
  h.pending[1].resolve({status:200, body:null});
  await new Promise(setImmediate);
  assert.equal(h.sent[6], second, 'full archive follows earlier sidebar reads');
  assert.equal(gate.active, 5);
  assert.equal(gate.directActive, 0);
  h.window.fetch('/api/user-action', {cccPriority:'user'});
  assert.equal(h.sent[7], '/api/user-action', 'user actions still have their reserved slot');
  h.pending.find(p => p.input === '/api/user-action').resolve({status:200, body:null});
  await new Promise(setImmediate);
  h.pending[2].resolve({status:200, body:null});
  await new Promise(setImmediate);
  assert.equal(h.sent[8], '/api/poll-5', 'ordinary polls retain FIFO order too');
});

test('aborting a queued sidebar read removes it without consuming a connection', async () => {
  const h = gateHarness(), gate = h.window.__cccConnectionGate;
  for (let i = 0; i < 8; i++) h.window.fetch('/api/poll-' + i);
  const controller = new AbortController();
  const request = h.window.fetch('/api/conversations/list?window=7d', {signal:controller.signal});
  const queued = gate.queued;
  controller.abort();
  await assert.rejects(request);
  assert.equal(gate.queued, queued - 1);
  h.pending[0].resolve({status:200, body:null});
  await new Promise(setImmediate);
  assert.equal(h.sent[5], '/api/poll-5');
  assert.equal(gate.active, 5);
});
