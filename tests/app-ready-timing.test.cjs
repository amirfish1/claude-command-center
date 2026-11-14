const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/app-kit.js', 'utf8');
const start = source.indexOf("  var AGENT_KEY =");
const end = source.indexOf('  // Freezes:', start);
assert.ok(start > 0 && end > start);

function harness({ hidden = false, brokenAgent = false } = {}) {
  const beacons = [], events = [];
  const nav = { startTime: 0, requestStart: 5.4, responseStart: 30.6,
    responseEnd: 50, domInteractive: 110, duration: 0, name: 'private-navigation' };
  const resource = (name, startTime, responseEnd) => ({ name, startTime, responseEnd,
    requestStart: startTime + 2, responseStart: responseEnd - 10, duration: responseEnd - startTime });
  const performance = (resources) => ({ now: () => 1600, timeOrigin: 100000,
    getEntriesByType: type => type === 'navigation' ? [nav] : resources });
  const origin = 'http://localhost:8090';
  const agent = { performance: performance([
    resource(origin + '/static/app.js?secret=never-report', 50, 300),
    resource(origin + '/api/conversations/session-1?tail=200&private=value', 500, 900),
    resource(origin + '/api/conversations/other-session?tail=200', 500, 1000),
    resource('https://other.example/static/app.js', 10, 400),
  ]) };
  agent.performance.timeOrigin += 100;
  if (brokenAgent) Object.defineProperty(agent, 'performance', { get() { throw Error('denied'); } });
  const ctx = {
    APP: 'example', thumb: false, agentId: 'session-1', frameFor: { 'session-1': { contentWindow: agent } },
    performance: performance([resource(origin + '/static/app-kit.js', 30, 80)]),
    document: { hidden, addEventListener() {} },
    location: { origin, href: origin + '/view/example' },
    URL, Number, Math, Date,
    CustomEvent: function (type, init) { this.type = type; this.detail = init.detail; },
    fetch: (url, init) => { beacons.push(JSON.parse(init.body)); return Promise.resolve({}); },
  };
  ctx.window = ctx;
  ctx.parent = ctx;
  ctx.dispatchEvent = event => events.push(event);
  vm.createContext(ctx);
  vm.runInContext(source.slice(start, end), ctx);
  return { ctx, beacons, events };
}

test('readiness waits for both content and agent and retains their original clocks', () => {
  const { ctx, beacons } = harness();
  ctx.noteContent({ ms: 200, cached: true });
  assert.equal(beacons.length, 0);
  vm.runInContext("ready.agent = 'yes'; ready.agentStart = 100; ready.agentAt = 1600; sendReady(); sendReady();", ctx);
  assert.equal(beacons.length, 1);
  const event = beacons[0];
  assert.equal(event.ms, 1600);
  assert.equal(event.detail.content_ms, 200);
  assert.equal(event.detail.agent_ms, 1600);
  assert.equal(event.detail.agent_start_ms, 100);
  assert.equal(event.detail.cached, true);
  assert.equal(event.detail.agent_timing.resources[1].responseEnd, 900);
  assert.equal(event.detail.page_timing.navigation.responseStart, 31);
  assert.equal(event.detail.agent_timing.origin_offset_ms, 100);
  assert.ok(event.detail.load_id);
});

test('attribution allowlists resource categories and omits URLs and session identifiers', () => {
  const { ctx, beacons } = harness();
  vm.runInContext("ready.agent = 'yes'; ready.agentAt = 1600; noteContent({ms: 200});", ctx);
  const detail = beacons[0].detail;
  assert.deepEqual(detail.agent_timing.resources.map(r => r.kind), ['dashboard_script', 'conversation']);
  const serialized = JSON.stringify(detail);
  for (const privateValue of ['secret', 'session-1', 'other-session', 'private', 'localhost', 'other.example']) {
    assert.equal(serialized.includes(privateValue), false, privateValue);
  }
});

test('missing or inaccessible timing APIs never prevent the existing ready beacon', () => {
  const { ctx, beacons } = harness({ brokenAgent: true });
  vm.runInContext("ready.agent = 'yes'; ready.agentAt = 1600; noteContent({ms: 200});", ctx);
  assert.equal(beacons.length, 1);
  assert.equal(beacons[0].detail.agent_timing, null);
});

test('apps without agents still finish with no invented agent timing', () => {
  const { ctx, beacons } = harness();
  vm.runInContext("ready.agent = 'none'; noteContent({ms: 200});", ctx);
  assert.equal(beacons[0].ms, 200);
  assert.equal(beacons[0].detail.agent_timing, null);
  assert.equal(beacons[0].detail.agent_start_ms, null);
});

test('background loads remain excluded and timing resources are bounded', () => {
  const { ctx, beacons, events } = harness({ hidden: true });
  vm.runInContext("ready.agent = 'none'; noteContent({ms: 200});", ctx);
  assert.equal(beacons.length, 0);
  assert.equal(events.length, 1);
  const entry = { name: 'http://localhost:8090/static/app.js', duration: Infinity,
    serverTiming: [{ name: 'app', duration: 120 }, { name: 'private-server-path', duration: 12 }] };
  const timing = ctx.timingSnapshot({ performance: { getEntriesByType: type => type === 'navigation' ? [] : Array(20).fill(entry) } }, '');
  assert.equal(timing.resources.length, 8);
  assert.equal('duration' in timing.resources[0], false);
  assert.equal(timing.resources[0].server.app, 120);
  assert.equal(Object.keys(timing.resources[0].server).length, 1);
});
