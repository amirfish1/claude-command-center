const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function helpers() {
  const start = source.indexOf('  const _sidebarFamilyParents = new Map();');
  const end = source.indexOf('  // CCC-763:', start);
  assert(start >= 0 && end > start, 'family lineage helpers exist');
  const context = vm.createContext({ manualSubsessionParentId: () => '', _f2ContinuationEdges: () => ({}), conversationsData: [] });
  vm.runInContext(source.slice(start, end), context);
  return context;
}
test('family metadata groups rows missing parent links, including grandchildren', () => {
  const h = helpers();
  const tree = { session_id: 'parent-session', children: [{session_id: 'child-session', children: [{session_id: 'grandchild-session'}]}] };
  assert.equal(h.rememberSidebarFamilyParents(tree), true);
  assert.equal(h.f2EffectiveParentSessionId('child-session', ''), 'parent-session');
  assert.equal(h.f2EffectiveParentSessionId('grandchild-session', ''), 'child-session');
  assert.equal(h.rememberSidebarFamilyParents(tree), false, 'unchanged polls do not rerender');
  assert.equal(h.f2EffectiveParentSessionId('child-session', 'recorded-parent'), 'recorded-parent');
  h.manualSubsessionParentId = () => 'manual-parent';
  assert.equal(h.f2EffectiveParentSessionId('child-session', ''), 'manual-parent');
});
test('explicit collapse wins over attention and all parents have disclosure', () => {
  const start = source.indexOf('    const _renderSubagentCluster =');
  const end = source.indexOf('    // Active list keeps', start);
  const context = vm.createContext({
    _subagentClusterPresentation: c => ({rootItem:c.rows[0],total:c.rows.length-1,attention:1,active:1}),
    _subagentRowId: c => c.id,
    _subagentExpandedParents: new Set(), _subagentCollapsedParents: new Set(['parent']),
    _renderRow: (c, opts) => opts.subagentClusterMeta ? JSON.stringify(opts.subagentClusterMeta) : c.id,
    escapeAttr: s => s,
  });
  vm.runInContext(source.slice(start, end) + '\nthis.render = _renderSubagentCluster;', context);
  for (const total of [1, 2, 4]) {
    const html = context.render({rows:[{card:{id:'parent'}},...Array.from({length:total},(_,i)=>({card:{id:'child'+i},depth:1}))]});
    assert(!html.includes('is-expanded'), 'attention must not reopen a collapsed parent');
    assert(html.includes('"collapsible":true'), 'even one child gets a chevron');
  }
});
