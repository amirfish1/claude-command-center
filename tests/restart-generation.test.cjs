const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./legacy-dashboard-source.cjs');
const helper = source.slice(source.indexOf('  async function restartWaitForGeneration('), source.indexOf('  async function restartServerRun('));

function harness(versions) {
  let now = 0, calls = 0;
  const context = {Date: {now: () => now}, AbortSignal,
    setTimeout(fn, ms) { now += ms; fn(); },
    async fetch() { return {ok: true, async json() { return versions[Math.min(calls++, versions.length - 1)]; }}; },
  };
  vm.runInNewContext(helper + '\nthis.wait = restartWaitForGeneration;', context);
  return {wait: context.wait, calls: () => calls};
}

test('old healthy generation is not restart success, including same-pid exec', async () => {
  const old = {pid: 7, started_at: 10};
  const h = harness([old, old, {pid: 7, started_at: 11}]);
  assert.equal(await h.wait(old, 120), true);
  assert.equal(h.calls(), 3);
});

test('cancelled and failed restart report reason instead of success', async () => {
  for (const state of ['cancelled', 'failed']) {
    const h = harness([{pid: 7, restart_outcome: {state, reason: 'requests are still running'}}]);
    await assert.rejects(h.wait({pid: 7}, 120), new RegExp('Restart ' + state + ': requests are still running'));
  }
});

test('unchanged generation times out without claiming success', async () => {
  const old = {pid: 7, started_at: 10};
  assert.equal(await harness([old]).wait(old, 3), false);
});
