const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
function fn(name) {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }', start) + 4);
}
function setup() {
  const context = { continuationParentId: c => c?.continued_from_session_id || '' };
  vm.createContext(context);
  vm.runInContext(fn('_continuationFoldMaps') + fn('sidebarRowsWithParentAnchors'), context);
  return context.sidebarRowsWithParentAnchors;
}
const ids = rows => Array.from(rows, c => c.session_id);

test('a filtered child brings back its loaded topic and intermediate parent', () => {
  const topic = {session_id: 'topic', modified: 1};
  const parent = {session_id: 'parent', parent_session_id: 'topic', modified: 2};
  const child = {session_id: 'agent-test', id: 'parent:agent-test', parent_session_id: 'parent', modified: 100};
  const rows = [child];
  assert.deepEqual(ids(setup()(rows, [topic, parent, child])), ['agent-test', 'parent', 'topic']);
  assert.equal(rows.length, 1);
});

test('children of folded sessions anchor under the current topic continuation', () => {
  const origin = {session_id: 'origin', modified: 1};
  const head = {session_id: 'head', continued_from_session_id: 'origin', modified: 2};
  const child = {session_id: 'child', parent_session_id: 'origin', modified: 100};
  assert.deepEqual(ids(setup()([child], [origin, head, child])), ['child', 'origin', 'head']);
});

test('cycles, missing parents and explicit removal do not restore unrelated topics', () => {
  const child = {session_id: 'child', parent_session_id: 'parent'};
  const parent = {session_id: 'parent', parent_session_id: 'child'};
  const anchors = setup();
  assert.deepEqual(ids(anchors([child], [parent, child])), ['child', 'parent']);
  assert.deepEqual(ids(anchors([child], [])), ['child']);
  for (const removal of [{trashed: true}, {explicitly_archived: true}, {idea_ticket: {explicitly_archived: true}}]) {
    assert.deepEqual(ids(anchors([child], [{...parent, ...removal}])), ['child']);
  }
});

test('topic sections keep a child with its parent and preserve separate tickets', () => {
  const start = app.indexOf('    const _allTabTreeRowsFor =');
  const end = app.indexOf('    const _allTabById =', start);
  const topic = {session_id: 'topic', idea_ticket: {ref: 'TEST-1'}};
  const child = {session_id: 'child', parent_session_id: 'topic'};
  const separate = {session_id: 'separate', parent_session_id: 'topic', idea_ticket: {ref: 'TEST-2'}};
  const context = {
    _ipSearchActive: false,
    _allTabUnfilteredConvs: [topic, child, separate],
    _allTabSessionId: c => c.session_id,
    _allTabParentId: c => c.parent_session_id,
    continuationParentId: () => '',
  };
  vm.createContext(context);
  vm.runInContext(fn('_continuationFoldMaps') + fn('sidebarRowsWithParentAnchors')
    + app.slice(start, end) + '\nthis.tree = _allTabTreeRowsFor;', context);
  const result = context.tree([child, separate]);
  assert.deepEqual(Array.from(result, r => [r.card.session_id, r.depth]), [['separate', 0], ['topic', 0], ['child', 1]]);
});

test('a separate ticket does not pull another ticket into its view', () => {
  const parent = {session_id: 'parent', idea_ticket: {ref: 'TEST-1'}};
  const child = {session_id: 'child', parent_session_id: 'parent', idea_ticket: {ref: 'TEST-2'}};
  assert.deepEqual(ids(setup()([child], [parent, child])), ['child']);
});

test('the tree folds an old spawning leg into its restored continuation', () => {
  const origin = {session_id: 'origin', modified: 1};
  const head = {session_id: 'head', continued_from_session_id: 'origin', modified: 2};
  const child = {session_id: 'child', parent_session_id: 'origin'};
  const context = {
    _ipSearchActive: false, _allTabUnfilteredConvs: [origin, head, child],
    _allTabSessionId: c => c.session_id, _allTabParentId: c => c.parent_session_id,
    continuationParentId: c => c.continued_from_session_id || '',
  };
  const start = app.indexOf('    const _allTabTreeRowsFor =');
  const end = app.indexOf('    const _allTabById =', start);
  vm.createContext(context);
  vm.runInContext(fn('_continuationFoldMaps') + fn('sidebarRowsWithParentAnchors')
    + app.slice(start, end) + '\nthis.tree = _allTabTreeRowsFor;', context);
  assert.deepEqual(Array.from(context.tree([child]), r => [r.card.session_id, r.depth]), [['head', 0], ['child', 1]]);
});
