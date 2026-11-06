const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const helpers = ['_archiveWindowTsSec', '_archiveWindowRowTs'].map(name => {
  const start = source.indexOf('  function ' + name + '(');
  return source.slice(start, source.indexOf('\n  }\n', start) + 4);
}).join('\n');
const keyStart = source.indexOf('    const _allTabClusterMtime =');
const keyEnd = source.indexOf('    // Sort key matches', keyStart);
const start = source.indexOf('    const _ticketLifecycleView =');
const end = source.indexOf("    } else if (_allTabView !== 'workers') {", start);

function render(rows, { lane = 'alltickets', mode = 'state', grouping = 'time' } = {}) {
  const context = {
    _allTabView: lane, _allTabMainConvs: rows, currentConversation: null,
    _hideGroupChatsForSearch: false, _ipSearchActive: false, _isSpecificFolderFilter: false,
    _arcShouldGroupByFolder: false, _arcGrouping: grouping,
    localStorage: { getItem: key => key === 'ccc-session-state-grouping' ? mode : null },
    _ideaTicketRows: () => [],
    todayStarActive: () => false,
    stableTicketSessionGroups: items => ({
      'needs-you': items.filter(r => r.question_waiting),
      active: items.filter(r => !r.question_waiting), finished: [], archived: [],
    }),
    _allTabTreeRowsFor: items => items.map(card => ({ card, depth: 0 })),
    _allTabRowsToClusters: items => items.map(item => ({ rows: [item] })),
    _allTabClusterPinRank: cluster => cluster.rows[0].card.pinned ? 0 : Infinity,
    _allTabClusterCost: cluster => cluster.rows[0].card.cost_usd || 0,
    _renderAllTabClusters: clusters => clusters.map(c => '[' + c.rows[0].card.id + ']').join(''),
  };
  return vm.runInNewContext(helpers + '\n' + source.slice(keyStart, keyEnd)
    + "\nlet _arcRows = '', _ideaArchivedHtml = '';\n"
    + source.slice(start, end) + '\n}\n_arcRows;', context);
}
const ids = html => Array.from(html.matchAll(/\[([^\]]+)\]/g), match => match[1]);
const rows = [
  { id: 'old-question', modified: 1700000000000, question_waiting: true, pinned: true },
  { id: 'new-working', modified: 1700000020 },
  { id: 'recent-question', modified: 1700000030, question_waiting: true },
  { id: 'touched', modified: 1700000000, last_interacted: 1700000040 },
];

test('All interleaves questions and active sessions newest first, regardless of pins', () => {
  assert.deepEqual(ids(render(rows)), ['touched', 'recent-question', 'new-working', 'old-question']);
});
test('All one-list mode uses normalized activity timestamps too', () => {
  assert.deepEqual(ids(render(rows, { mode: 'list' })),
    ['touched', 'recent-question', 'new-working', 'old-question']);
});
test('Features lists pins first, then one newest-first Active list', () => {
  assert.deepEqual(ids(render(rows, { lane: 'ideas' })),
    ['old-question', 'touched', 'recent-question', 'new-working']);
});
test('explicit cost sorting still orders All by cost', () => {
  assert.deepEqual(ids(render(rows.map((r, i) => ({ ...r, cost_usd: i })), { mode: 'list', grouping: 'cost' })),
    ['touched', 'recent-question', 'new-working', 'old-question']);
});
test('All uses the visible parent time instead of a newer nested lane', () => {
  const mtime = vm.runInNewContext(helpers + '\n' + source.slice(keyStart, keyEnd)
    + '\n_allTabClusterMtime;', { _allTabView: 'alltickets' });
  assert.equal(mtime({ rows: [{ card: { modified: 10 } }, { card: { modified: 30 } }] }), 10);
});
