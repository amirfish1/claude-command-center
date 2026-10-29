const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/fleet-failover.js'), 'utf8');

function load({ allowed = false, missingGate = false, response } = {}) {
  const requests = [];
  const gate = { allowed };
  const window = { addEventListener() {} };
  if (!missingGate) window.cccPopups = { allowed: (id) => gate.allowed && id === 'fleet-limit' };
  const context = {
    window, document: { readyState: 'loading', hidden: false, addEventListener() {}, dispatchEvent() {}, getElementById: () => null },
    localStorage: { getItem: () => null }, Event: class Event {}, setInterval() {},
    fetch: async (url, init = {}) => {
      requests.push({ url, init });
      return response ? response(url, init) : { ok: true, status: 200, json: async () => ({ ok: true, groups: [] }) };
    }, console, Date, Promise,
  };
  vm.createContext(context);
  vm.runInContext(source.replace('  function boot() {',
    '  window.testFleet = { popupAllowed, receive, selected, change, groupHtml, act };\n  function boot() {'), context);
  return { window, requests, api: window.testFleet, gate };
}

function fixture(n = 7, detected = 1) {
  return {
    ok: true, auto_resume_max_per_minute: 5, free_ready: true,
    groups: [{ key: 'claude', engine: 'claude', engine_label: 'Claude', can_continue_free: true, can_auto_resume: true,
      sessions: Array.from({ length: n }, (_, i) => ({
        session_id: 'session-' + i, display_name: 'Task ' + i, state: 'limited', detected_at: detected,
        resume_at: Date.now() / 1000 + 3600,
      })) }],
  };
}

const settle = () => new Promise(setImmediate);

test('fleet popup is off by default and fails closed without the gate', () => {
  assert.equal(load().api.popupAllowed(), false);
  assert.equal(load({ missingGate: true }).api.popupAllowed(), false);
  assert.equal(load({ allowed: true }).api.popupAllowed(), true);
});

test('all seven rows start checked, with plain batch labels', () => {
  const { api } = load();
  const data = fixture();
  api.receive(data);
  assert.equal(api.selected(data.groups[0]).length, 7);
  const html = api.groupHtml(data.groups[0], false);
  assert.match(html, /7 sessions stopped/);
  assert.match(html, /Continue all 7 free/);
  assert.match(html, /Resume all at reset/);
  assert.match(html, /up to 5 per minute/);
});

test('selection survives polling and resets for a new stop', () => {
  const { api } = load();
  const data = fixture(2);
  api.receive(data);
  api.change({ target: { matches: () => true, getAttribute: () => 'session-0', checked: false } });
  api.receive(data);
  assert.deepEqual(Array.from(api.selected(data.groups[0])), ['session-1']);
  assert.match(api.groupHtml(data.groups[0], false), /Continue 1 free/);
  api.receive(fixture(2, 2));
  assert.equal(api.selected(data.groups[0]).length, 2);
});

test('selected IDs and always opt-in are sent once in a batch', async () => {
  const loaded = load({ response: (_url, init) => ({
    ok: true, status: 200, json: async () => init.method === 'POST'
      ? { ok: true, results: { 'session-1': { ok: true } } } : fixture(2),
  }) });
  const data = fixture(2);
  loaded.api.receive(data);
  loaded.api.act(data.groups[0], 'continue', ['session-1'], { always: true });
  loaded.api.act(data.groups[0], 'continue', ['session-1'], { always: true });
  await settle();
  const posts = loaded.requests.filter((r) => r.init.method === 'POST');
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].init.body), { action: 'continue', session_ids: ['session-1'], always: true });
});

test('partial failure identifies the row instead of claiming full success', async () => {
  const loaded = load({ response: (_url, init) => ({
    ok: true, status: 200, json: async () => init.method === 'POST'
      ? { ok: false, results: { 'session-0': { ok: true }, 'session-1': { ok: false, error: 'Still busy' } } }
      : fixture(2),
  }) });
  const data = fixture(2);
  loaded.api.receive(data);
  loaded.api.act(data.groups[0], 'arm', ['session-0', 'session-1']);
  await settle();
  const html = loaded.api.groupHtml(data.groups[0], false);
  assert.match(html, /1 of 2 changed/);
  assert.match(html, /Still busy/);
});

test('display names cannot become markup', () => {
  const { api } = load();
  const data = fixture(1);
  data.groups[0].sessions[0].display_name = '<img src=x onerror=alert(1)>';
  api.receive(data);
  const html = api.groupHtml(data.groups[0], false);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
});

test('covers stays off until a fleet surface is actually visible', () => {
  const closed = load();
  closed.api.receive(fixture(1));
  assert.equal(closed.window.cccFleetFailover.covers('session-0'), false);
  closed.gate.allowed = true;
  assert.equal(closed.window.cccFleetFailover.covers('session-0'), true);
  closed.gate.allowed = false;
  assert.equal(closed.window.cccFleetFailover.covers('session-0'), false);
  const allowed = load({ allowed: true });
  allowed.api.receive(fixture(1));
  assert.equal(allowed.window.cccFleetFailover.covers('session-0'), true);
});

test('404 clears fleet ownership so the legacy view can degrade gracefully', async () => {
  const loaded = load({ allowed: true, response: () => ({ status: 404 }) });
  loaded.api.receive(fixture(1));
  assert.equal(loaded.window.cccFleetFailover.covers('session-0'), true);
  loaded.window.cccFleetFailover.refresh();
  await settle();
  assert.equal(loaded.window.cccFleetFailover.covers('session-0'), false);
});
