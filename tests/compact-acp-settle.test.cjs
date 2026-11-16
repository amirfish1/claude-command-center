// CCC-1188: a Devin /compact card must end — on a fresh context summary
// (done) or when the ACP turn goes idle without one (unconfirmed) — instead
// of spinning "Compacting context" forever.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./legacy-dashboard-source.cjs');
const start = source.indexOf('  // COMPACT_ACP_SETTLE_START');
const end = source.indexOf('  // COMPACT_ACP_SETTLE_END', start);
assert.ok(start >= 0 && end > start, 'settle block markers present');

function load(run, live) {
  const ctx = vm.createContext({
    _compactRun: run,
    liveStatus: live,
    liveStatusMatchesOpenConv: () => true,
    _compactRunFor: (sid) => (run && run.sid === sid ? run : null),
    _compactRunIsForeground: () => true,
    _stopCompactRunTimer() {},
    _compactRunMount() {},
    _compactRunPaint() {},
    completeCompactRun(sid) { if (run && run.sid === sid) run.stage = 'done'; },
    Date, Number, String,
  });
  vm.runInContext(source.slice(start, end), ctx);
  return ctx;
}
const newRun = (over) => Object.assign({
  sid: 's1', source: 'devin-cli', engineLabel: 'Devin', stage: 'working',
  startedAt: Date.now() - 60000,
}, over);

test('running ACP turn keeps the card working', () => {
  const run = newRun();
  const ctx = load(run, {kind: 'acp', status: 'running'});
  assert.equal(ctx._compactRunSettleAcpTurn(run, 60000), false);
  assert.equal(run.stage, 'working');
  assert.equal(run.acpSawRunning, true);
});

test('turn that ran and then stayed idle settles as unconfirmed', () => {
  const run = newRun({acpSawRunning: true, acpIdleSince: Date.now() - 11000});
  const ctx = load(run, {kind: 'acp', status: 'idle'});
  assert.equal(ctx._compactRunSettleAcpTurn(run, 60000), true);
  assert.equal(run.stage, 'unconfirmed');
  assert.match(run.error, /without writing a new context summary/);
});

test('a brief idle blip does not settle the card', () => {
  const run = newRun({acpSawRunning: true});
  const ctx = load(run, {kind: 'acp', status: 'idle'});
  assert.equal(ctx._compactRunSettleAcpTurn(run, 60000), false);
  assert.equal(run.stage, 'working');
});

test('non-Devin runs are left to their own completion signals', () => {
  const run = newRun({source: 'claude', acpIdleSince: Date.now() - 60000});
  const ctx = load(run, {kind: 'acp', status: 'idle'});
  assert.equal(ctx._compactRunSettleAcpTurn(run, 60000), false);
  assert.equal(run.stage, 'working');
});

test('a Devin summary newer than the run completes it; an old one does not', () => {
  const run = newRun();
  const ctx = load(run, {kind: 'acp', status: 'running'});
  ctx._compactRunAdoptDevinSummary({ts: new Date(run.startedAt - 3600e3).toISOString()});
  assert.equal(run.stage, 'working');
  ctx._compactRunAdoptDevinSummary({ts: new Date(run.startedAt + 30e3).toISOString()});
  assert.equal(run.stage, 'done');
});
