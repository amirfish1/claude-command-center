const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const app = require('./legacy-dashboard-source.cjs');
const fn = name => {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }', start) + 4);
};
const context = {continuationParentId: row => row.continued_from_session_id || ''};
vm.runInNewContext(['_isOtherUtilityRow', '_continuationFoldMaps', 'sidebarRowsWithParentAnchors',
  'sidebarRowsWithWindowFamilies'].map(fn).join('\n'), context);
const select = (rows, pool) => Array.from(context.sidebarRowsWithWindowFamilies(rows, pool), r => r.session_id);

test('unfiltered pools preserve row order and duplicates without scanning for IDs', () => {
  let lookups = 0;
  const child = {get session_id() { lookups++; return 'child'; }, parent_session_id: 'parent'};
  const parent = {get session_id() { lookups++; return 'parent'; }};
  const pool = [child, parent, child];
  for (const expand of [context.sidebarRowsWithParentAnchors, context.sidebarRowsWithWindowFamilies]) {
    const result = expand(pool, pool);
    assert.notEqual(result, pool);
    assert.equal(result.length, pool.length);
    pool.forEach((row, index) => assert.equal(result[index], row));
  }
  assert.equal(lookups, 0);
});

test('recent topic retains old earlier legs and all nested lanes across windows', () => {
  const origin = {session_id: 'origin', modified: 1};
  const head = {session_id: 'head', continued_from_session_id: 'origin', modified: 100};
  const child = {session_id: 'child', parent_session_id: 'origin', modified: 2};
  const nested = {session_id: 'nested', parent_session_id: 'child', modified: 3};
  const unrelated = {session_id: 'unrelated', modified: 1};
  const pool = [origin, head, child, nested, unrelated];
  for (const cutoff of [5, 50, 99]) {
    assert.deepEqual(new Set(select(pool.filter(r => r.modified >= cutoff), pool)),
      new Set(['head', 'origin', 'child', 'nested']));
  }
  assert.deepEqual(select([], pool), []);
});

test('family expansion respects explicit removals and utility isolation, and stops cycles', () => {
  const root = {session_id: 'root', parent_session_id: 'child'};
  const child = {session_id: 'child', parent_session_id: 'root'};
  const removed = {session_id: 'removed', parent_session_id: 'root', trashed: true};
  const archived = {session_id: 'archived', parent_session_id: 'root', explicitly_archived: true};
  const utility = {session_id: 'utility', parent_session_id: 'root', first_message: 'TE_SIDECALL: check'};
  const pool = [root, child, removed, archived, utility];
  assert.deepEqual(new Set(select([root], pool)), new Set(['root', 'child']));
  assert.deepEqual(select([utility], pool), ['utility']);
});
