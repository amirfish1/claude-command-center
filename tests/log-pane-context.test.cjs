const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function section(start, end) {
  const a = app.indexOf(start), b = app.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return app.slice(a, b);
}

function harness() {
  const body = {
    entries: [], _html: '',
    set innerHTML(value) { this._html = value; this.entries = []; },
    get innerHTML() { return this._html; },
    querySelectorAll() { return this.entries; },
  };
  const toggle = { setAttribute() {} };
  const pane = { classList: { contains: () => true } };
  const requests = [], rendered = [];
  const h = vm.createContext({
    AbortController, currentConversation: 'session-a',
    conversationsData: [{ session_id: 'session-a', title: 'Release notes' }],
    rowRawTitle: row => ({ rawTitle: row.title }),
    escapeHtml: value => String(value), escapeAttr: value => String(value),
    _activityLogTimestampLocal: () => '10/15/2026, 2:34:56 PM',
    document: {
      addEventListener() {},
      getElementById: id => ({ railLogBody: body, railLogToggleAll: toggle, statusRailLogPane: pane })[id],
    },
    fetch: (url, options) => new Promise((resolve, reject) => {
      requests.push({ url, signal: options.signal, reject,
        reply: (events, ok = true) => resolve({ ok, json: async () => ({ ok, events }) }),
      });
    }),
  });
  vm.runInContext(section('  // Readable activity log:', '  function initRailLogPane()'), h);
  vm.runInContext(section('  async function _loadActivityLogInto(', '  function openActivityLogModal('), h);
  h._renderReadableActivityLog = (_body, events) => {
    rendered.push(Array.from(events));
    body.innerHTML = events.map(ev => ev.detail).join('\n');
    body.entries = events.map(() => ({ open: false }));
  };
  return { h, body, toggle, requests, rendered, run: code => vm.runInContext(code, h) };
}

const event = (verb, detail, category = 'inject') => ({
  ts: '2026-10-15 12:34:56 UTC', category, verb, detail,
});

test('search includes raw and local timestamps, summaries and displayed session names', () => {
  const { h, run, rendered } = harness();
  h.events = [event('UDS', 'session=session-a receipt=delivered')];
  run('_railLogEvents = events');
  for (const needle of ['2026-10-15', '2:34:56 PM', 'RELEASE NOTES', 'peer connection', 'receipt=delivered', 'inject', 'UDS']) {
    h.needle = needle;
    run('_railLogFilter = needle; _renderRailLogPane()');
    assert.equal(rendered.length, 1, needle);
    assert.deepEqual(rendered.pop(), h.events);
  }
});

test('search and attention cannot reveal hidden healthy heartbeats or routine injects', () => {
  const { h, run, body, rendered, toggle } = harness();
  h.events = [event('BEAT', 'ok (0.5s)', 'health'), event('UDS', 'receipt=delivered'),
    event('UDS', 'receipt=unknown'), event('UDS-FAIL', 'socket closed')];
  run('_railLogEvents = events; _railLogShowInjects = false; _railLogOnlyAttention = true; _renderRailLogPane()');
  assert.deepEqual(rendered.pop().map(ev => ev.verb), ['UDS', 'UDS-FAIL']);
  run('_railLogFilter = "delivered"; _renderRailLogPane()');
  assert.match(body.innerHTML, /No events match/);
  assert.equal(toggle.disabled, true);
});

test('hidden-only records show an empty message and disable expansion', () => {
  const { h, run, body, rendered, toggle } = harness();
  h.events = [event('stale?', 'old_stale=False new_stale=False', 'worker')];
  run('_railLogEvents = events; _renderRailLogPane()');
  assert.equal(rendered.length, 0);
  assert.match(body.innerHTML, /No events match/);
  assert.equal(toggle.disabled, true);
});

for (const lateFailure of [false, true]) test(`rail ignores superseded ${lateFailure ? 'errors' : 'events'} after a scope change`, async () => {
  const { h, run, body, requests, rendered } = harness();
  const all = h.refreshRailLogPane();
  await h.refreshRailLogPane();
  assert.equal(requests.length, 1, 'polls for the same scope coalesce');
  run('_railLogScope = "session"');
  const session = h.refreshRailLogPane();
  assert.equal(requests.length, 2, 'new scope loads immediately');
  assert.equal(requests[0].signal.aborted, true);
  assert.match(requests[1].url, /session_id=session-a/);
  assert.match(body.innerHTML, /Loading/);
  requests[1].reply([event('FAILED', 'session-a only')]);
  await session;
  if (lateFailure) requests[0].reject(new Error('late network failure'));
  else requests[0].reply([event('FAILED', 'different session')]);
  await all;
  assert.equal(rendered.length, 1);
  assert.equal(body.innerHTML, 'session-a only');
});

test('changing conversations while fetching rejects the old response and loads the new session', async () => {
  const { h, run, requests, rendered, body } = harness();
  run('_railLogScope = "session"');
  const first = h.refreshRailLogPane();
  h.currentConversation = 'session-b';
  requests[0].reply([event('FAILED', 'session-a')]);
  await first;
  assert.equal(rendered.length, 0);
  assert.match(requests[1].url, /session_id=session-b/);
  requests[1].reply([event('FAILED', 'session-b')]);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(body.innerHTML, 'session-b');
});

test('missing selection, empty results and errors retain their message through filter changes', async () => {
  const { h, run, requests, body, toggle } = harness();
  run('_railLogScope = "session"');
  h.currentConversation = '__new__';
  await h.refreshRailLogPane();
  assert.equal(requests.length, 0);
  run('_renderRailLogPane()');
  assert.match(body.innerHTML, /Select a session/);
  assert.equal(toggle.disabled, true);

  h.currentConversation = 'session-a';
  const empty = h.refreshRailLogPane();
  requests[0].reply([]);
  await empty;
  run('_renderRailLogPane()');
  assert.match(body.innerHTML, /No activity-log entries for this session yet/);
  const failed = h.refreshRailLogPane();
  requests[1].reply([], false);
  await failed;
  run('_renderRailLogPane()');
  assert.match(body.innerHTML, /activity-log-error.*Could not load/);
  assert.equal(toggle.disabled, true);
});

test('modal scope changes cannot be overwritten by a slower prior request', async () => {
  const { h, requests, body, rendered } = harness();
  const overlay = {};
  h._activityLogModalEl = overlay;
  const session = h._loadActivityLogInto(overlay, body, 'session-a', false);
  const all = h._loadActivityLogInto(overlay, body, 'session-a', true);
  assert.equal(requests[0].signal.aborted, true);
  requests[1].reply([event('FAILED', 'all events')]);
  await all;
  requests[0].reply([event('FAILED', 'old session result')]);
  await session;
  assert.equal(rendered.length, 1);
  assert.equal(body.innerHTML, 'all events');
});
