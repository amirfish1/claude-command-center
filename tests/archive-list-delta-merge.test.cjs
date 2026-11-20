const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const app = require('./legacy-dashboard-source.cjs');
const start = app.indexOf('  function _mergeArchiveListDelta(');
const src = app.slice(start, app.indexOf('  // Generous: cold-cache scans', start));
assert.ok(start > 0 && src.length > 100);
const ctx = vm.createContext({});
vm.runInContext(`${src}\nthis.merge = _mergeArchiveListDelta;`, ctx);

test('delta merge replaces changed rows, drops gone ids and follows server order', () => {
  const a = { session_id: 'a', title: 'A' };
  const b = { session_id: 'b', title: 'B' };
  const gone = { id: 'gone', title: 'Old' };
  const out = ctx.merge([a, b, gone], {
    ids: ['c', 'a', 'b'],
    conversations: [{ session_id: 'b', title: 'B2' }, { session_id: 'c', title: 'C' }],
  });
  assert.deepEqual([...out].map(r => r.title), ['C', 'A', 'B2']);
  assert.equal(out[1], a);
});

test('delta naming an id the client never had is unmergeable', () => {
  assert.equal(ctx.merge([{ session_id: 'a' }], { ids: ['a', 'x'], conversations: [] }), null);
  assert.equal(ctx.merge(null, { ids: [], conversations: [] }), null);
});

test('saved order requires a matching proof, count, and known changed rows', () => {
  const rows = [{id:'a'}, {id:'b'}];
  const delta = {ids:[], conversations:[], same_order:true, order_token:'saved', count:2};
  assert.equal(ctx.merge(rows, delta, 'different'), null);
  assert.equal(ctx.merge(rows, {...delta, count:1}, 'saved'), null);
  assert.equal(ctx.merge(rows, {...delta, conversations:[{id:'c'}]}, 'saved'), null);
});
