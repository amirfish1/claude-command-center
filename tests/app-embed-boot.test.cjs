const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const boot = fs.readFileSync(path.join(root, 'static/app-embed-boot.js'), 'utf8');
const entry = fs.readFileSync(path.join(root, 'static/conversation-entry.js'), 'utf8');
const {functionSource} = require('./conversation-source.cjs');
const timedRead = functionSource('_fetchConvJsonTimed');

function harness(search = '?ccc_popout=conversation&embed=app&conv=session-1') {
  let now = 10;
  const requests = [], messages = [], frames = [], timers = [];
  const window = { parent: {postMessage: data => messages.push(data)}, location: {origin: 'http://localhost'} };
  const context = vm.createContext({
    window, location: {search}, URLSearchParams, console, Promise, Response,
    document: { getElementById: () => null },
    performance: {now: () => ++now},
    fetch: async url => {
      requests.push(url);
      return {ok: true, headers: {get: () => 'app;dur=5'}, text: async () => '{"events":[{"type":"assistant"}]}'};
    },
    _convDirectUrl: () => null, _convResourceTiming: () => ({}),
    requestAnimationFrame: fn => frames.push(fn), setTimeout: fn => timers.push(fn),
  });
  context.sharedState = context.hostServices = context;
  return {window, context, requests, messages, frames, timers};
}

test('early transcript read is consumed once by the shared timed reader', async () => {
  const h = harness();
  vm.runInContext(boot + timedRead, h.context);
  const data = await vm.runInContext('_fetchConvJsonTimed("/api/conversations/session-1?tail=120")', h.context);
  assert.equal(data.events[0].type, 'assistant');
  assert.equal(data.__perf.server_ms, 5);
  assert.equal(h.requests.length, 1);
  assert.equal(h.window.__cccAppEmbedTail, undefined);
  await vm.runInContext('_fetchConvJsonTimed("/api/conversations/session-1?tail=120")', h.context);
  assert.equal(h.requests.length, 2, 'later freshness reads still go to the server');
});

test('failed early read retries through the existing fetch path', async () => {
  const h = harness();
  h.context.fetch = async () => { throw new Error('connection reset'); };
  vm.runInContext(boot, h.context);
  await h.window.__cccAppEmbedTail.promise;
  h.context.fetch = async url => { h.requests.push(url); return {ok: true, headers: {get: () => ''}, text: async () => '{"events":[]}'}; };
  vm.runInContext(timedRead, h.context);
  const data = await vm.runInContext('_fetchConvJsonTimed("/api/conversations/session-1?tail=120")', h.context);
  assert.equal(data.events.length, 0);
  assert.equal(h.requests.length, 1);
});

test('canonical page tail is consumed once without another read', async () => {
  const h = harness();
  h.context.document.getElementById = () => ({remove() {}, textContent: JSON.stringify({
    url: '/api/conversations/session-1?tail=120', cache: 'miss',
    text: JSON.stringify({events: [{type: 'assistant', text: 'Current server read'}], last_line: 73}),
  })});
  vm.runInContext(boot + timedRead, h.context);
  const data = await vm.runInContext('_fetchConvJsonTimed("/api/conversations/session-1?tail=120")', h.context);
  assert.equal(h.requests.length, 0);
  assert.equal(data.events[0].text, 'Current server read');
  assert.equal(data.last_line, 73);
  assert.equal(data.__perf.server_cache, 'page-miss');
  assert.equal(data.__perf.server_ms, null, 'not misreported as a zero-duration network read');
  await vm.runInContext('_fetchConvJsonTimed("/api/conversations/session-1?tail=120")', h.context);
  assert.equal(h.requests.length, 1);
});

test('invalid, mismatched and failed page tails keep the real request path', async () => {
  for (const initial of [null, '{broken', JSON.stringify({url: '/api/conversations/other?tail=120', text: '{"events":[]}'}),
    JSON.stringify({url: '/api/conversations/session-1?tail=120', text: '{"error":"Read failed"}'})]) {
    const h = harness();
    h.context.document.getElementById = () => initial && ({textContent: initial});
    vm.runInContext(boot + timedRead, h.context);
    const data = await vm.runInContext('_fetchConvJsonTimed("/api/conversations/session-1?tail=120")', h.context);
    assert.equal(data.events[0].type, 'assistant');
    assert.equal(h.requests.length, 1);
  }
});

test('boot never reads a missing/new/invalid target or a top-level page', () => {
  for (const search of ['?embed=app&conv=__new__', '?embed=app&conv=bad/path', '?conv=session-1', '?embed=app']) {
    const h = harness(search); vm.runInContext(boot, h.context); assert.equal(h.requests.length, 0);
  }
  const h = harness(); h.window.parent = h.window;
  vm.runInContext(boot, h.context); assert.equal(h.requests.length, 0);
});

test('readiness requires the actual selected composer and sends only one painted preview', () => {
  const h = harness();
  const drawn = [];
  h.window.dispatchEvent = event => drawn.push(event.type);
  h.context.Event = class { constructor(type) { this.type = type; } };
  Object.assign(h.context, {
    APP_EMBED_MODE: true, CONV_POPOUT_TARGET: 'session-1',
    activePaneId: () => 'p1', paneByPaneId: () => ({conversationId: 'session-1'}),
    composerInputForPane: () => null, currentSession: {id: 'session-1'},
  });
  const source = functionSource('noteAppEmbedDrawn');
  h.context._appEmbedReadySent = false;
  vm.runInContext(source, h.context);
  vm.runInContext('noteAppEmbedDrawn("session-1", true)', h.context);
  assert.equal(h.frames.length, 0, 'no readiness before composer initialization');
  h.context.composerInputForPane = () => ({});
  vm.runInContext('noteAppEmbedDrawn("another-session", true)', h.context);
  assert.equal(h.frames.length, 0);
  vm.runInContext('noteAppEmbedDrawn("session-1", true); noteAppEmbedDrawn("session-1", false)', h.context);
  assert.equal(h.messages.length, 0, 'wait for paint');
  h.frames[0](); h.timers.forEach(fn => fn());
  assert.equal(h.messages.length, 1);
  assert.equal(h.messages[0].cached, true);
  assert.equal(h.messages[0].conv, 'session-1');
  assert.deepEqual(drawn, ['ccc:conversation-drawn']);
});

test('conversation entry initializes once the pane is parsed, while extensions are still loading', async () => {
  const calls = [];
  const document = new EventTarget();
  document.readyState = 'loading';
  const window = {
    CCCConversationHost: {
      createHost({documentReady}) {
        documentReady(() => calls.push('initialize'));
        return {
          bindConversationInteractions: () => calls.push('interactions'),
          bindConversationControls: () => calls.push('controls'),
          bindConversationViews: () => calls.push('views'),
          wireEmbedHostMessages: () => calls.push('messages'),
          bootConversationPopoutDirect: () => calls.push('conversation'),
        };
      },
    },
  };
  const initialized = vm.runInNewContext(entry, {window, document, console});
  assert.deepEqual(calls, [], 'wait until the parser has built the pane');
  document.readyState = 'interactive';
  document.dispatchEvent(new Event('readystatechange'));
  await new Promise(setImmediate);
  const expected = ['interactions', 'controls', 'views', 'initialize', 'messages', 'conversation'];
  assert.deepEqual(calls, expected);
  document.dispatchEvent(new Event('DOMContentLoaded'));
  document.readyState = 'complete';
  document.dispatchEvent(new Event('readystatechange'));
  await initialized;
  assert.deepEqual(calls, expected, 'later lifecycle events must not start the pane again');
});
