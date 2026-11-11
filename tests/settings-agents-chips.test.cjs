// Launch-default chips, "Apply to all queues" and per-queue model/effort in
// Settings. Runs the real functions from static/app.js with stubbed neighbours.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
function between(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'missing source block: ' + start);
  return source.slice(from, to);
}
const chips = between('  function aoChipsHtml(which) {', '  // Changes only this queue\'s engine pin');

function sandbox(overrides = {}) {
  const calls = { saved: [], posts: [], toasts: [], refreshed: 0 };
  const context = {
    calls,
    spawnDefaultsState: { engine: 'claude', models: { claude: 'claude-sonnet-5-5' }, reasoning_effort: 'medium', worker_inherit_session: false, worker_engine: 'claude', worker_model: 'claude-sonnet-5-5', worker_reasoning_effort: 'medium' },
    getTopSpawnPicks: () => [
      { engine: 'claude', model: 'claude-sonnet-5-5', effort: 'medium' },
      { engine: 'codex', model: 'gpt-test', effort: 'high' },
      { engine: 'cursor', model: 'composer-test', effort: '' },
    ],
    ENGINE_HUB_WORKER_ENGINES: ['claude', 'codex'],
    MODEL_PICKER_ENGINE_GLYPHS: { claude: 'C', codex: 'X', cursor: 'U' },
    _normalizeModelId: value => String(value || '').replace(/^claude-/, ''),
    escapeAttr: value => String(value).replaceAll('"', '&quot;'),
    escapeHtml: value => String(value),
    getModelPillLabel: (engine, model, effort) => model + (effort ? ' (' + effort + ')' : ''),
    aoSaveDefaults: (body, message) => calls.saved.push({ body, message }),
    showOpToast: (message, kind) => calls.toasts.push({ message, kind }),
    refreshAgentsOverview: () => { calls.refreshed += 1; },
    _agentsOverviewQueues: { queues: [] },
    _agentsOverviewSig: 'x',
    aoPost: async (url, body) => {
      calls.posts.push(body);
      if (overrides.fail === body.queue) throw new Error('refused');
      if (body.policy_action === 'read') return { policy_revision: 'rev-' + body.queue };
      if (body.policy_action === 'preview') return { preview_token: 'tok-' + body.queue, changes: overrides.noChange ? [] : [{ field: 'engine' }] };
      return { ok: true };
    },
    ...overrides.context,
  };
  vm.createContext(context);
  vm.runInContext(chips + '\nthis.api = { aoChipsHtml, aoApplyChip, aoApplyWorkerToAllQueues, aoApplyQueuePolicy };', context);
  return context;
}

test('new-session chips light the saved setup and omit the inherit chip', () => {
  const html = sandbox().api.aoChipsHtml('session');
  assert.equal((html.match(/data-ao-chip="session"/g) || []).length, 3);
  assert.equal((html.match(/is-selected/g) || []).length, 1);
  assert.match(html, /is-selected" role="radio" aria-checked="true" data-ao-chip="session" data-engine="claude"/);
  assert.doesNotMatch(html, /data-ao-inherit/);
});

test('queue-worker chips lead with "Same as new sessions" and list worker engines only', () => {
  const context = sandbox();
  let html = context.api.aoChipsHtml('worker');
  assert.match(html, /data-ao-inherit="on">Same as new sessions/);
  assert.doesNotMatch(html, /data-engine="cursor"/);
  context.spawnDefaultsState.worker_inherit_session = true;
  html = context.api.aoChipsHtml('worker');
  assert.match(html, /orch-tier-chip is-selected" role="radio" aria-checked="true" data-ao-inherit/);
  assert.equal((html.match(/is-selected/g) || []).length, 1);
});

test('a saved setup outside the favorites still appears as the lit chip', () => {
  const context = sandbox();
  context.spawnDefaultsState.engine = 'codex';
  context.spawnDefaultsState.models = { codex: 'gpt-other' };
  const html = context.api.aoChipsHtml('session');
  assert.match(html, /is-selected"[^>]*data-engine="codex" data-model="gpt-other"/);
});

test('clicking a chip saves engine, model and effort for the right role', () => {
  const context = sandbox();
  context.api.aoApplyChip('session', 'codex', 'gpt-test', 'high');
  context.api.aoApplyChip('worker', 'codex', 'gpt-test', '');
  assert.deepEqual(JSON.parse(JSON.stringify(context.calls.saved.map(row => row.body))), [
    { engine: 'codex', models: { codex: 'gpt-test' }, reasoning_effort: 'high' },
    { worker_engine: 'codex', worker_model: 'gpt-test', worker_inherit_session: false },
  ]);
});

test('apply to all queues clears pins only on pinned queues, via read, preview, apply', async () => {
  const context = sandbox();
  context._agentsOverviewQueues = { queues: [
    { queue: 'ALPHA', pins: { engine: 'codex' } }, { queue: 'BETA', pins: {} }, { queue: 'GAMMA', pins: { effort: 'low' } },
  ] };
  const button = { disabled: false };
  await context.api.aoApplyWorkerToAllQueues(button);
  const posts = JSON.parse(JSON.stringify(context.calls.posts));
  assert.deepEqual(posts.map(post => post.queue + ':' + post.policy_action), ['ALPHA:read', 'ALPHA:preview', 'ALPHA:apply', 'GAMMA:read', 'GAMMA:preview', 'GAMMA:apply']);
  assert.deepEqual(posts[2], { queue: 'ALPHA', policy_action: 'apply', expected_revision: 'rev-ALPHA', changes: { engine: null, model: null, effort: null }, preview_token: 'tok-ALPHA' });
  assert.equal(button.disabled, false);
  assert.deepEqual(JSON.parse(JSON.stringify(context.calls.toasts)), [{ message: '2 queues now use this setup.', kind: 'ok' }]);
});

test('apply to all queues writes nothing when no queue is pinned and reports failures', async () => {
  const idle = sandbox();
  idle._agentsOverviewQueues = { queues: [{ queue: 'ALPHA', pins: {} }] };
  await idle.api.aoApplyWorkerToAllQueues({ disabled: false });
  assert.equal(idle.calls.posts.length, 0);
  assert.equal(idle.calls.toasts[0].message, 'All queues already use this setup.');
  const failing = sandbox({ fail: 'ALPHA' });
  failing._agentsOverviewQueues = { queues: [{ queue: 'ALPHA', pins: { model: 'm' } }] };
  await failing.api.aoApplyWorkerToAllQueues({ disabled: false });
  assert.equal(failing.calls.toasts[0].message, 'Could not change: ALPHA');
  assert.equal(failing.calls.toasts[0].kind, 'err');
});

test('a queue policy preview with no changes is not applied', async () => {
  const context = sandbox({ noChange: true });
  await context.api.aoApplyQueuePolicy('ALPHA', { effort: 'low' });
  assert.deepEqual(context.calls.posts.map(post => post.policy_action), ['read', 'preview']);
});
