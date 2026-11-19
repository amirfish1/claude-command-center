const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const bridge = source.slice(source.indexOf('  window.cccChooseLimitContinuation ='), source.indexOf('  // Question-tags panel:'));

function load({ enabled = true, routes, input = 'Existing draft', sid = 'session-a' } = {}) {
  const st = { launch: { engine: 'claude', model: 'old', effort: 'high' } };
  const calls = [];
  const context = { window: { cccFrontSessionId: () => sid },
    closeSettingsModal: () => calls.push('close-settings'),
    withSessionComposer: async () => ({ value: input }), activePaneId: () => 'p1', f2PaneKey: x => x,
    f2DismissedPanes: new Map(), f2ManualOpen: () => calls.push('open-picker'), f2PaneState: new Map([['p1', st]]),
    spawnDefaultsState: { worker_fallback: { enabled, models: routes || [{ engine: 'codex', model: 'test-model', effort: 'medium' }] } },
    f2LaunchEngines: () => [{ id: 'claude' }, { id: 'codex' }],
    _modelAllowedForEngine: (_e, m) => m !== 'blocked',
    f2ModelsForEngine: () => [{ id: 'test-model' }], f2EffortsForEngine: () => [{ id: 'low' }, { id: 'medium' }],
    f2RenderComposer: () => calls.push('render'), f2PanelEl: () => null,
  };
  vm.runInNewContext(bridge, context);
  return { api: context.window.cccChooseLimitContinuation, st, calls };
}

test('opens saved fallback engine/model/effort while preserving the draft, without starting it', async () => {
  const { api, st, calls } = load();
  assert.equal(await api('session-a', 'claude'), true);
  assert.deepEqual(JSON.parse(JSON.stringify(st.launch)), { engine: 'codex', model: 'test-model', effort: 'medium' });
  assert.equal(st.promptDraft, 'Existing draft');
  assert.equal(st.configOpen, true);
  assert.deepEqual(calls, ['close-settings', 'open-picker', 'render']);
});

test('skips the limited engine, disabled engines and unavailable models in saved fallback order', async () => {
  const { api, st } = load({ routes: [
    { engine: 'claude', model: 'test-model' }, { engine: 'off', model: 'test-model' },
    { engine: 'codex', model: 'blocked' }, { engine: 'codex', model: 'test-model', effort: 'invalid' },
  ], input: '' });
  assert.equal(await api('session-a', 'claude'), true);
  assert.equal(st.launch.engine, 'codex');
  assert.equal(st.launch.effort, 'low');
  assert.match(st.promptDraft, /unfinished task/);
});

test('disabled fallback retains ordinary picker defaults; navigation race cannot open another session picker', async () => {
  const disabled = load({ enabled: false });
  await disabled.api('session-a', 'claude');
  assert.equal(disabled.st.launch.model, 'old');
  const moved = load({ sid: 'session-b' });
  assert.equal(await moved.api('session-a', 'claude'), false);
  assert.deepEqual(moved.calls, ['close-settings']);
});
