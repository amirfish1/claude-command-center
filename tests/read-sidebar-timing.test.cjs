const assert = require('node:assert/strict');
const { test } = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = app.indexOf('  function _readSidebarTiming(');
const source = app.slice(start, app.indexOf('\n  }\n', start) + 4);

function harness(reading = true) {
  let now = 0;
  const messages = [];
  const context = vm.createContext({
    performance: { now: () => now },
    _loafReadTag: () => reading,
    _cccBootId: 'test-page',
    _clientLog: message => messages.push(message),
    document: { querySelector: () => ({ getAttribute: () => '/static/app.js?v=test' }) },
  });
  const begin = vm.runInContext(source + '\n_readSidebarTiming;', context);
  return { begin, messages, context, advance: ms => { now += ms; } };
}

test('inactive playback and fast renders do not add diagnostic traffic', () => {
  assert.equal(harness(false).begin(), null);
  const h = harness();
  const timing = h.begin();
  h.advance(199);
  timing.finish();
  assert.deepEqual(h.messages, []);
});

test('slow render phases retain the build and account for the whole callback', () => {
  const h = harness();
  const timing = h.begin();
  timing.result = 'rebuild';
  for (const phase of ['html', 'before_dom', 'dom', 'actions', 'mount']) {
    h.advance(41);
    timing.mark(phase);
  }
  h.advance(7);
  timing.finish();
  assert.equal(h.messages.length, 1);
  const row = JSON.parse(h.messages[0].slice('[READ-RENDER] '.length));
  assert.equal(row.ms, 212);
  assert.equal(Object.values(row.phases).reduce((sum, ms) => sum + ms, 0), row.ms);
  assert.equal(row.result, 'rebuild');
  assert.equal(row.boot_id, 'test-page');
  assert.equal(row.build, '/static/app.js?v=test');
  assert.ok(h.messages[0].length < 500, 'record must fit the client-log sink');
});

test('slow patch and early-return paths report remaining time without stale phases', () => {
  for (const result of ['patch', 'early']) {
    const h = harness();
    const timing = h.begin();
    timing.result = result;
    h.advance(250);
    timing.finish();
    const row = JSON.parse(h.messages[0].slice('[READ-RENDER] '.length));
    assert.equal(row.result, result);
    assert.deepEqual(row.phases, { rest: 250 });
  }
});

test('a diagnostic failure never hides the rendering outcome', () => {
  const h = harness();
  const timing = h.begin();
  h.advance(250);
  h.context._clientLog = () => { throw new Error('sink unavailable'); };
  assert.doesNotThrow(() => timing.finish());
});
