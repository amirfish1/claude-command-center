const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { functions } = require('./conversation-source.cjs');
const source = require('./legacy-dashboard-source.cjs');
const rows = [{ id: 'pinned', pinned: true, display_name: 'Release notes', modified: 1 },
  { id: 'plain', display_name: 'Search indexing', modified: 100 }];
const identity = v => v;
function localSearch(q, data = rows, decorate = identity) {
  const fn = functions(['splitConversationSearch', 'searchExcludesConversation', 'filterConversations']);
  const context = {
    query: q, conversationsData: data, _bridgeSearchNeedle: () => '', _uuidSearchNeedle: () => null, recencyCutoffSec: () => 50,
    _archivedBacklogIds: new Set(), currentConversation: '', showRecentOnly: true, window: {},
    rowMatchesEffortQuery: () => false, _prioritizeNameMatches: identity,
    _prioritizeSessionIdMatches: identity, applyConvSort: identity, _decorateWithHistoryMatches: decorate,
    rowRawTitle: c => ({ rawTitle: c.display_name || c.first_message || '' }),
    sidebarRowDisplayTitle: identity, continuationBackstopTitle: (_, title) => title,
  };
  context.sharedState = context.hostServices = context;
  return vm.runInNewContext(fn + '\nfilterConversations(query);', context);
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

test('title exclusions apply to pinned rows and history or repo results', () => {
  const data = [
    { id: 'hide', pinned: true, display_name: 'Notification review' },
    { id: 'keep', display_name: 'Review pipeline', first_message: 'notification' },
    { id: 'incident', display_name: 'Review incident' },
    { id: 'message', display_name: 'Deployment' },
  ];
  const ids = result => Array.from(result, row => row.id);
  const decorate = (result, positive, key) => {
    assert.equal(positive, 'review');
    assert.equal(key, 'review -notification -incident');
    return result.concat(
      { id: 'hide', display_name: 'Synthetic review snippet' },
      { id: 'repo', display_name: 'Notification rollout' },
      { id: 'message', display_name: 'Deployment' },
    );
  };
  assert.deepEqual(ids(localSearch('review -Notification -INCIDENT', data, decorate)), ['keep', 'message']);
  assert.deepEqual(ids(localSearch('-notification', data)), ['keep', 'incident', 'message']);
});

test('standalone exclusions preserve positive phrases, IDs and quoted text', () => {
  const context = {};
  vm.runInNewContext(functions(['splitConversationSearch']), context);
  const parse = query => JSON.parse(JSON.stringify(context.splitConversationSearch(query)));
  assert.deepEqual(parse('ops-* -notification review -incident'), {
    positive: 'ops-* review',
    excluded: ['notification', 'incident'],
  });
  assert.deepEqual(parse('a1b2-c3d4 "literal -notification" -'), {
    positive: 'a1b2-c3d4 "literal -notification" -',
    excluded: [],
  });
});
