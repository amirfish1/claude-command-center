const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/fleet-failover.js'), 'utf8');

function load({ allowed = false, missingGate = false, response, storage = new Map(), clock = Date } = {}) {
  const requests = [];
  const gate = { allowed };
  const window = { addEventListener() {} };
  if (!missingGate) window.cccPopups = { allowed: (id) => gate.allowed && id === 'fleet-limit' };
  const context = {
    window, document: { readyState: 'loading', hidden: false, addEventListener() {}, dispatchEvent() {}, getElementById: () => null },
    localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) }, Event: class Event {}, setInterval() {},
    fetch: async (url, init = {}) => {
      requests.push({ url, init });
      return response ? response(url, init) : { ok: true, status: 200, json: async () => ({ ok: true, groups: [] }) };
    }, console, Date: clock, Promise,
  };
  vm.createContext(context);
  vm.runInContext(source.replace('  function boot() {',
    '  window.testFleet = { popupAllowed, receive, selected, change, groupHtml, act, click };\n  function boot() {'), context);
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

test('hiding an armed notice preserves the schedule through polls and reloads', () => {
  const storage = new Map();
  const loaded = load({ allowed: true, storage });
  const data = fixture(1);
  const group = data.groups[0];
  group.sessions[0].state = 'armed';
  loaded.api.receive(data);
  assert.match(loaded.api.groupHtml(group, true), /Hide notice/);
  const card = { getAttribute: () => group.key };
  const control = { disabled: false, closest: () => card,
    getAttribute: (name) => name === 'data-act' ? 'hide-notice' : null };
  loaded.api.click({ target: { closest: () => control } });
  loaded.api.receive(data);
  assert.equal(loaded.api.groupHtml(group, true), '');
  assert.equal(loaded.requests.filter(r => r.init.method === 'POST').length, 0);
  assert.equal(group.sessions[0].state, 'armed');
  assert.match(loaded.api.groupHtml(group, false), /Will resume/);
  assert.match(loaded.api.groupHtml(group, false), /Cancel/);

  const reloaded = load({ allowed: true, storage });
  reloaded.api.receive(data);
  assert.equal(reloaded.api.groupHtml(group, true), '');
  group.sessions[0].resume_at += 60;
  reloaded.api.receive(data);
  assert.match(reloaded.api.groupHtml(group, true), /Hide notice/);
});

test('notice dismissal works when browser storage is unavailable', () => {
  const loaded = load({ allowed: true, storage: {
    get() { throw new Error('storage blocked'); },
    set() { throw new Error('storage blocked'); },
  } });
  const data = fixture(1);
  const group = data.groups[0];
  group.sessions[0].state = 'armed';
  loaded.api.receive(data);
  const control = { disabled: false, closest: () => ({ getAttribute: () => group.key }),
    getAttribute: (name) => name === 'data-act' ? 'hide-notice' : null };
  loaded.api.click({ target: { closest: () => control } });
  assert.equal(loaded.api.groupHtml(group, true), '');
  assert.equal(loaded.requests.filter(r => r.init.method === 'POST').length, 0);
  group.sessions.push({ session_id: 'new-session', state: 'armed', resume_at: Date.now() / 1000 + 60 });
  loaded.api.receive(data);
  assert.match(loaded.api.groupHtml(group, true), /Hide notice/);
});

test('hidden limited notices remain recoverable without a dismiss request', () => {
  const storage = new Map();
  const loaded = load({ allowed: true, storage });
  const data = fixture(1);
  const group = data.groups[0];
  loaded.api.receive(data);
  const control = { disabled: false, closest: () => ({ getAttribute: () => group.key }),
    getAttribute: (name) => name === 'data-act' ? 'hide-notice' : null };
  loaded.api.click({ target: { closest: () => control } });
  assert.equal(loaded.api.groupHtml(group, true), '');
  assert.match(loaded.api.groupHtml(group, false), /Resume all at reset/);
  assert.equal(loaded.requests.filter(r => r.init.method === 'POST').length, 0);
  const reloaded = load({ allowed: true, storage });
  reloaded.api.receive(data);
  assert.equal(reloaded.api.groupHtml(group, true), '');
  assert.match(reloaded.api.groupHtml(group, false), /Continue all 1 free/);
});

test('seven-day snooze survives reload and new stops for the same group', () => {
  const storage = new Map();
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  const loaded = load({ allowed: true, storage, clock: Clock });
  const data = fixture(1);
  const group = data.groups[0];
  loaded.api.receive(data);
  assert.match(loaded.api.groupHtml(group, true), /Don’t show me this again for 7 days/);

  const control = { disabled: false, closest: () => ({ getAttribute: () => group.key }),
    getAttribute: (name) => name === 'data-act' ? 'snooze-notice' : null };
  loaded.api.click({ target: { closest: () => control } });
  assert.equal(loaded.api.groupHtml(group, true), '');
  assert.match(loaded.api.groupHtml(group, false), /Resume all at reset/);
  assert.equal(loaded.window.cccFleetFailover.covers('session-0'), true);
  assert.equal(loaded.requests.filter(r => r.init.method === 'POST').length, 0);

  const reloaded = load({ allowed: true, storage, clock: Clock });
  const next = fixture(2, 2);
  reloaded.api.receive(next);
  const nextGroup = next.groups[0];
  assert.equal(reloaded.api.groupHtml(nextGroup, true), '');
  const codex = { ...nextGroup, key: 'codex', engine: 'codex', engine_label: 'Codex' };
  assert.match(reloaded.api.groupHtml(codex, true), /Codex limit reached/);
  const switchBack = { ...nextGroup, sessions: [{ ...nextGroup.sessions[0], state: 'free', switch_back_offered: true }] };
  assert.match(reloaded.api.groupHtml(switchBack, true), /Switch back/);
  assert.doesNotMatch(reloaded.api.groupHtml(switchBack, true), /7 days/);

  now += 7 * 24 * 60 * 60 * 1000 - 1;
  assert.equal(reloaded.api.groupHtml(nextGroup, true), '');
  now++;
  assert.match(reloaded.api.groupHtml(nextGroup, true), /Claude limit reached/);
});

test('seven-day snooze also hides the notice for the current tab when storage is blocked', () => {
  const loaded = load({ allowed: true, storage: {
    get() { throw new Error('storage blocked'); },
    set() { throw new Error('storage blocked'); },
  } });
  const data = fixture(1);
  const group = data.groups[0];
  group.sessions[0].state = 'armed';
  loaded.api.receive(data);
  const control = { disabled: false, closest: () => ({ getAttribute: () => group.key }),
    getAttribute: (name) => name === 'data-act' ? 'snooze-notice' : null };
  loaded.api.click({ target: { closest: () => control } });
  assert.equal(loaded.api.groupHtml(group, true), '');
  assert.match(loaded.api.groupHtml(group, false), /Will resume/);
  assert.equal(loaded.requests.filter(r => r.init.method === 'POST').length, 0);
});

test('notice uses escaped sidebar title, latest status and activity', () => {
  const loaded = load();
  loaded.window.cccFleetSessionInfo = () => ({ title: 'Fix <layout>', status: 'Next', detail: '<review>',
    repo: '/example/project', modified: '2026-11-19T12:00:00Z', model: 'test-model', effort: 'low' });
  const data = fixture(1);
  loaded.api.receive(data);
  const html = loaded.api.groupHtml(data.groups[0], false);
  for (const text of ['Fix &lt;layout&gt;', 'Next: &lt;review&gt;', 'Last activity:', '/example/project', 'test-model', 'low', 'session-0']) assert.ok(html.includes(text), text);
  assert.doesNotMatch(html, /<review>/);
});

test('choosing a route opens the picker without a fleet mutation and preserves failures', async () => {
  const loaded = load({ allowed: true });
  let opened = false;
  const calls = [];
  loaded.window.cccChooseLimitContinuation = async (...args) => { calls.push(args); return opened; };
  const data = fixture(1);
  const group = data.groups[0];
  loaded.api.receive(data);
  const control = { disabled: false, closest: () => ({ getAttribute: () => group.key }),
    getAttribute: (name) => name === 'data-act' ? 'choose-route' : 'session-0' };
  loaded.api.click({ target: { closest: () => control } });
  await settle();
  assert.match(loaded.api.groupHtml(group, true), /Could not open the model choices/);
  opened = true;
  loaded.api.click({ target: { closest: () => control } });
  await settle();
  assert.deepEqual(calls, [['session-0', 'claude'], ['session-0', 'claude']]);
  assert.equal(loaded.requests.filter(r => r.init.method === 'POST').length, 0);
  assert.equal(loaded.api.groupHtml(group, true), '');
});


test('automatically fetches native details for sessions absent from the sidebar', async () => {
  const loaded = load({ allowed: true, response: (url) => ({ ok: true, json: async () => ({
    ok: true, sessions: { 'session-0': { display_name: 'Native review session',
      last_assistant_text: 'Stopped at weekly limit', modified: 1795089600 } }, errors: {},
  }) }) });
  loaded.window.cccFleetSessionInfo = (_sid, row) => row ? {
    title: row.display_name, status: 'Update', detail: row.last_assistant_text, modified: row.modified,
  } : null;
  const data = fixture(1);
  delete data.groups[0].sessions[0].display_name;
  loaded.api.receive(data);
  await settle();
  const html = loaded.api.groupHtml(data.groups[0], true);
  assert.match(html, /Native review session/);
  assert.match(html, /Stopped at weekly limit/);
  assert.match(html, /Last activity:/);
  assert.doesNotMatch(html, /not loaded|Open session to load/);
  assert.equal(loaded.requests.filter(r => r.url.startsWith('/api/free-failover/fleet-details?')).length, 1);
  loaded.api.receive(data);
  await settle();
  assert.equal(loaded.requests.length, 1, 'unchanged details are reused across fleet polls');
});
