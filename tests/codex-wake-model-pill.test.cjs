const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('  function syncCodexWakeUsage(');
const end = source.indexOf('  function _sessionUsagePaneIsVisible(', start);

function harness() {
  const paints = [];
  const ctx = vm.createContext({
    _usageSessionIdByPane: { p1: 'thread-a', p2: 'thread-a', p3: 'thread-b' },
    _usageDataByPane: Object.fromEntries(['p1', 'p2', 'p3'].map(pid => [pid, {
      engine: 'codex', model: 'old-model', reasoning_effort: 'medium',
      latest_input_tokens: 42000, override: { model: 'new-model' },
    }])),
    _usageRequestGenerationByPane: { p1: 7 },
    _usageRequestByPane: { p1: { generation: 7 } },
    renderSessionUsageIntoStrip: pid => paints.push(pid),
  });
  vm.runInContext(source.slice(start, end), ctx);
  return { ctx, paints, sync: data => ctx.syncCodexWakeUsage('thread-a', data) };
}

test('confirmed rollout updates both visible copies without a usage poll', () => {
  const { ctx, paints, sync } = harness();
  sync({ sid: 'thread-a', rollout_model: 'new-model', rollout_effort: 'high' });
  assert.deepEqual(paints, ['p1', 'p2']);
  assert.equal(ctx._usageDataByPane.p1.model, 'new-model');
  assert.equal(ctx._usageDataByPane.p2.reasoning_effort, 'high');
  assert.equal(ctx._usageDataByPane.p3.model, 'old-model');
  assert.equal(ctx._usageDataByPane.p1.latest_input_tokens, 42000);
  assert.equal(ctx._usageDataByPane.p1.override.model, 'new-model');
  assert.equal(ctx._usageRequestGenerationByPane.p1, 8);
  assert.equal(ctx._usageRequestByPane.p1, undefined, 'older usage response cannot undo the observation');
  sync({ sid: 'thread-a', rollout_model: 'new-model', rollout_effort: 'high' });
  assert.equal(paints.length, 2, 'unchanged ticks do not repaint');
});

test('attempted model, mismatched session, and missing rollout never confirm a switch', () => {
  const { ctx, paints, sync } = harness();
  sync({ sid: 'thread-a', model: 'new-model', effort: 'high' });
  sync({ sid: 'thread-b', rollout_model: 'new-model' });
  sync({ sid: 'thread-a', rollout_model: null });
  assert.equal(paints.length, 0);
  assert.equal(ctx._usageDataByPane.p1.model, 'old-model');
  assert.equal(ctx._usageRequestGenerationByPane.p1, 7);
});

test('pending selection survives a confirmed different model; unknown effort stays unchanged', () => {
  const { ctx, sync } = harness();
  sync({ sid: 'thread-a', rollout_model: 'actual-model', effort: 'high' });
  assert.equal(ctx._usageDataByPane.p1.model, 'actual-model');
  assert.equal(ctx._usageDataByPane.p1.override.model, 'new-model');
  assert.equal(ctx._usageDataByPane.p1.reasoning_effort, 'medium');
});

test('panes still loading and other engines are not overwritten', () => {
  const { ctx, paints, sync } = harness();
  ctx._usageDataByPane.p1 = null;
  ctx._usageDataByPane.p2.engine = 'claude';
  sync({ sid: 'thread-a', rollout_model: 'new-model' });
  assert.equal(paints.length, 0);
});
