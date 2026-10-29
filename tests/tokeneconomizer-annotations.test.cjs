const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const scan = app.slice(app.indexOf('  const _CC_ORCH_RES = ['), app.indexOf('  function _ccOrchLabels('));
const render = app.slice(app.indexOf('  function _tokenEconomizerAnnotationHtml('),
  app.indexOf('  function _paintTokenEconomizerAnnotation('));
const context = vm.createContext({ escapeHtml: s => String(s), escapeAttr: s => String(s) });
vm.runInContext(scan + render, context);
const labels = text => Array.from(context._ccOrchScan(text));

test('TokenEconomizer CLI commands receive distinct purple action labels', () => {
  assert.deepEqual(labels('"~/.tokeneconomizer/bin/te" --host codex status --json'), ['TokenEconomizer status']);
  assert.deepEqual(labels('/opt/TokenEconomizer/plugin/scripts/te --host claude toolprune'), ['TokenEconomizer toolprune']);
  assert.deepEqual(labels('te --cwd "/a repo" --host codex config show'), ['TokenEconomizer config']);
  assert.deepEqual(labels('wt claim; ccc sessions'), ['wt claim', 'ccc sessions']);
  assert.deepEqual(labels('Discuss TokenEconomizer status and savings.'), []);
});

test('automatic activity distinguishes observation from actions and savings', () => {
  assert.equal(context._tokenEconomizerAnnotationHtml({ observed: false, actions: [] }), '');
  const observed = context._tokenEconomizerAnnotationHtml({ observed: true, actions: [] });
  assert.match(observed, /TokenEconomizer · observed/);
  assert.match(observed, /no optimization action is recorded/);
  const acted = context._tokenEconomizerAnnotationHtml({ observed: true, actions: ['loop redirected'] });
  assert.match(acted, /TokenEconomizer · loop redirected/);
  assert.match(acted, /not a measurement of token savings/);
});

test('opening annotation bypasses background queues and rejects a response for a switched pane', async () => {
  const pane = { conversationId: 'first' };
  let finish;
  const requests = [], painted = [];
  const runtime = vm.createContext({
    paneByPaneId: () => pane,
    fetch: (url, options) => {
      requests.push({ url, options });
      return new Promise(resolve => { finish = resolve; });
    },
  });
  vm.runInContext(app.slice(app.indexOf('  async function _refreshTokenEconomizerAnnotation('),
    app.indexOf('  function renderConversationEvents(')), runtime);
  runtime._paintTokenEconomizerAnnotation = (id, report) => painted.push({ id, report });
  const first = runtime._refreshTokenEconomizerAnnotation('pane');
  assert.equal(requests[0].options.cccPriority, 'user');
  pane.conversationId = 'second';
  finish({ ok: true, json: async () => ({ observed: true, actions: [] }) });
  await first;
  assert.equal(painted.length, 0);
  const second = runtime._refreshTokenEconomizerAnnotation('pane');
  finish({ ok: true, json: async () => ({ observed: true, actions: [] }) });
  await second;
  assert.equal(painted.length, 1);
  await runtime._refreshTokenEconomizerAnnotation('pane');
  assert.equal(requests.length, 2); // cached paint restores a reopened transcript without a fetch
  pane._teFetchedAt = 0;
  const update = runtime._refreshTokenEconomizerAnnotation('pane');
  assert.equal(requests[2].options, undefined);
  finish({ ok: true, json: async () => ({ observed: true, actions: ['loop redirected'] }) });
  await update;
  assert.equal(painted.at(-1).report.actions[0], 'loop redirected');
});
