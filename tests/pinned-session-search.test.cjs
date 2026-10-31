const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const rows = [{ id: 'pinned', pinned: true, display_name: 'Release notes', modified: 1 },
  { id: 'plain', display_name: 'Search indexing', modified: 100 }];
const identity = v => v;
function localSearch(q) {
  const start = source.indexOf('  function filterConversations(q) {');
  const end = source.indexOf('\n  }\n', start) + 4;
  return vm.runInNewContext(source.slice(start, end) + '\nfilterConversations(query);', {
    query: q, conversationsData: rows, _bridgeSearchNeedle: () => '', recencyCutoffSec: () => 50,
    _archivedBacklogIds: new Set(), currentConversation: '', showRecentOnly: true, window: {},
    rowMatchesEffortQuery: () => false, _prioritizeNameMatches: identity,
    _prioritizeSessionIdMatches: identity, applyConvSort: identity, _decorateWithHistoryMatches: identity,
  });
}
function archiveSearch(q) {
  const start = source.indexOf('    let rows = q ? byFolder.filter(c =>');
  const end = source.indexOf(') : byFolder;', start) + ') : byFolder;'.length;
  return vm.runInNewContext(source.slice(start, end) + '\nrows;', { q, byFolder: rows, rowMatchesEffortQuery: () => false });
}
for (const [name, search] of [['local', localSearch], ['archive', archiveSearch]]) {
  test(name + ': pinned sessions must match a nonempty query', () => {
    assert.equal(search('nonexistent-query').length, 0);
    assert.equal(search('release')[0].id, 'pinned');
    assert.equal(search('search').length, 1);
    assert.equal(search('search')[0].id, 'plain');
    assert.equal(search('').length, 2);
  });
}
