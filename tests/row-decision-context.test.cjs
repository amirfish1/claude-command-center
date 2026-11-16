const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = require('./legacy-dashboard-source.cjs');
const helpers = app.slice(app.indexOf('  const _rowDecisionReplies ='), app.indexOf('  // "Answer [Q12:slug]'));

test('decision context loads the full root reply once and escapes content as text', async () => {
  const host = { textContent: '' };
  const row = { dataset: { sessionId: 'test-session' }, querySelector: () => host };
  let preview = 'Short preview';
  let requests = 0;
  let refreshes = 0;
  const full = 'A'.repeat(350) + ' <script>literal evidence</script>\n<session-state>hidden';
  const context = vm.createContext({
    Map, String, JSON, Array, AbortSignal, encodeURIComponent,
    _ultraFreshRow: () => ({ last_assistant_text: preview }),
    syncRowQuickActions: () => { refreshes++; },
    fetch: async (url, options) => {
      requests++;
      assert.match(url, /test-session\?tail=20$/);
      assert.equal(options.cache, 'no-store');
      assert.equal(options.cccPriority, 'user', 'explicit context reads bypass deferred startup status work');
      return { ok: true, json: async () => ({ events: [
        { type: 'assistant', blocks: [{ kind: 'text', text: full }] },
        { type: 'assistant', parent_tool_use_id: 'helper', blocks: [{ kind: 'text', text: 'Child reply' }] },
        { type: 'assistant', blocks: [{ kind: 'tool_use', text: 'Tool output' }] },
      ] }) };
    },
  });
  vm.runInContext(helpers + '\nglobalThis.load = loadRowDecisionReply; globalThis.format = rowDecisionReplyText;', context);
  context.load(row); context.load(row);
  await new Promise(resolve => setImmediate(resolve));
  context.load(row);
  assert.equal(requests, 1);
  assert.equal(refreshes, 1);
  assert.equal(host.textContent, full.split('\n<session-state>')[0]);
  preview = 'A newer reply';
  context.load(row);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests, 2, 'a changed source must not reuse old context');
  assert.match(context.format('```json\n{"decisions":[{"question":"Proceed?","impact":"A new job starts","tradeoff":"Uses capacity"}]}\n```'),
    /Proceed\?\nImpact: A new job starts\nTradeoff: Uses capacity/);
});

test('unavailable decision context gives a recovery path', async () => {
  const host = { textContent: '' };
  const context = vm.createContext({
    Map, String, JSON, Array, AbortSignal, encodeURIComponent,
    _ultraFreshRow: () => ({ last_assistant_text: 'Preview' }),
    syncRowQuickActions: () => {},
    fetch: async () => ({ ok: false }),
  });
  vm.runInContext(helpers + '\nglobalThis.load = loadRowDecisionReply;', context);
  const row = { dataset: { sessionId: 'test-session' }, querySelector: () => host };
  context.load(row);
  await new Promise(resolve => setImmediate(resolve));
  context.load(row);
  assert.equal(host.textContent, 'Full reply unavailable. Open this session to read it.');
});
