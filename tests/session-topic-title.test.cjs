const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
function load(rows) {
  const start = source.indexOf('  function rowLocalTitle(');
  const end = source.indexOf('  // One row per continuation chain.', start);
  return vm.runInNewContext(source.slice(start, end) + '\nrowRawTitle;', {
    cleanIssuePrompt: s => s,
    firstSentenceOf: (s, limit) => s.slice(0, limit),
    stripGhIssueProjectTag: s => s,
    _uxFixesWorkerDisplayTitle: (_c, s) => s,
    continuationParentId: c => c.continued_from_session_id || '',
    _continuationRowForSid: sid => rows.find(r => r.session_id === sid),
    isContinuationBoilerplateTitle: s => /^You are continuing a task/.test(s),
  });
}
test('manual topic survives continuation while the current task remains visible', () => {
  const root = { session_id: 'root', display_name: 'Dashboard design', name_overridden: true };
  const leg = { session_id: 'leg', continued_from_session_id: 'root', display_name: 'Fix attention sections', name_overridden: true };
  const title = load([root, leg]);
  assert.equal(title(leg).rawTitle, 'Dashboard design: Fix attention sections');
  assert.equal(title(leg).titleSource, 'user');
});
test('multiple continuations preserve the original topic without stacking subtasks', () => {
  const rows = [
    { session_id: 'root', ai_title: 'Improve dashboard' },
    { session_id: 'middle', continued_from_session_id: 'root', ai_title: 'Fix sorting' },
    { session_id: 'last', continued_from_session_id: 'middle', ai_title: 'Fix grouping' },
  ];
  assert.equal(load(rows)(rows[2]).rawTitle, 'Improve dashboard: Fix grouping');
});
test('a manual name leads the AI title in the same session', () => {
  const row = { session_id: 'root', display_name: 'My project', name_overridden: true, ai_title: 'Fix sorting' };
  assert.equal(load([row])(row).rawTitle, 'My project: Fix sorting');
});
test('persisted task title survives refreshes that only supply continuation boilerplate', () => {
  const root = { session_id: 'root', display_name: 'Dashboard design', name_overridden: true };
  const leg = { session_id: 'leg', continued_from_session_id: 'root',
    display_name: 'You are continuing a task from earlier', ai_title: null,
    task_title: 'Fix attention sections' };
  assert.equal(load([root, leg])(leg).rawTitle, 'Dashboard design: Fix attention sections');
});
test('identical, already augmented, missing and boilerplate titles stay readable', () => {
  const root = { session_id: 'root', ai_title: 'Improve dashboard' };
  const title = load([root]);
  assert.equal(title({ continued_from_session_id: 'root', ai_title: 'Improve dashboard' }).rawTitle, 'Improve dashboard');
  assert.equal(title({ continued_from_session_id: 'root', ai_title: 'Improve dashboard: Fix sorting' }).rawTitle, 'Improve dashboard: Fix sorting');
  assert.equal(title({ continued_from_session_id: 'root', display_name: 'You are continuing a task from earlier' }).rawTitle, 'Improve dashboard');
  assert.equal(title({ continued_from_session_id: 'missing', ai_title: 'Fix sorting' }).rawTitle, 'Fix sorting');
});
test('cyclic lineage terminates and unrelated worker parents do not rename a task', () => {
  const a = { session_id: 'a', continued_from_session_id: 'b', ai_title: 'Task A' };
  const b = { session_id: 'b', continued_from_session_id: 'a', ai_title: 'Task B' };
  assert.equal(typeof load([a, b])(a).rawTitle, 'string');
  assert.equal(load([a])({ parent_session_id: 'a', ai_title: 'Verify results' }).rawTitle, 'Verify results');
});
