const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./legacy-dashboard-source.cjs');
function load(rows) {
  const start = source.indexOf('  function isGenericHandoffTitle(');
  const end = source.indexOf('  // One row per continuation chain.', start);
  const displayStart = source.indexOf('  const LEADING_PATH_OR_URL_RE =');
  const displayEnd = source.indexOf('  // Extract the first sentence / line', displayStart);
  return vm.runInNewContext(source.slice(displayStart, displayEnd) + source.slice(start, end) + '\nrowRawTitle;', {
    cleanIssuePrompt: s => s,
    firstSentenceOf: (s, limit) => s.slice(0, limit),
    stripGhIssueProjectTag: s => s,
    _uxFixesWorkerDisplayTitle: (_c, s) => s,
    _withTicketPrefix: (_c, s) => s,
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
test('continuation topics compare using the rendered title normalization', () => {
  const root = { session_id: 'root', display_name: 'Build pre-send QA gate' };
  const leg = { session_id: 'leg', continued_from_session_id: 'root', name_overridden: true,
    display_name: 'Build pre send QA gate: add review routes' };
  const title = load([root, leg]);
  assert.equal(title(leg).rawTitle, leg.display_name);
  leg.continuation_topic_title = root.display_name;
  assert.equal(title(leg).rawTitle, leg.display_name, 'persisted topics use the same comparison');
  leg.display_name = 'Build pre send QA gate';
  assert.equal(title(leg).rawTitle, root.display_name, 'identical visible names need no task suffix');
  leg.display_name = 'Build pre send QA gateway';
  assert.equal(title(leg).rawTitle, 'Build pre-send QA gate: Build pre send QA gateway',
    'a distinct task that only starts with the topic still keeps both names');
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
test('a regenerated automatic title replaces its stale cached display name', () => {
  const c = { session_id: 'root', display_name: 'Pick up and continue pending work',
    auto_titled: true, task_title: 'Review inbox and booking fixes' };
  assert.equal(load([c])(c).rawTitle, 'Review inbox and booking fixes');
  c.name_overridden = true;
  c.display_name = 'My chosen topic';
  assert.equal(load([c])(c).rawTitle, 'My chosen topic: Review inbox and booking fixes');
  c.name_overridden = false; // /rename in the terminal has no CCC sidecar
  assert.equal(load([c])(c).rawTitle, 'My chosen topic: Review inbox and booking fixes');
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


test('server-persisted topic survives missing or archived origin rows', () => {
  const title = load([]);
  const c = { session_id: 'latest', continued_from_session_id: 'unloaded-origin',
    ai_title: 'Repair reply choices', continuation_topic_title: 'Workspace design',
    continuation_topic_session_id: 'root' };
  assert.equal(title(c).rawTitle, 'Workspace design: Repair reply choices');
  c.ai_title = 'Workspace design: Repair reply choices';
  assert.equal(title(c).rawTitle, 'Workspace design: Repair reply choices');
  c.ai_title = 'You are continuing a task from earlier';
  c.task_title = 'Repair status';
  assert.equal(title(c).rawTitle, 'Workspace design: Repair status');
});

test('the topic before the colon remains stable across later legs', () => {
  const root = { session_id: 'root', display_name: 'Workspace design: First task', name_overridden: true };
  const c = { session_id: 'latest', continued_from_session_id: 'root', ai_title: 'Second task' };
  assert.equal(load([root])(c).rawTitle, 'Workspace design: Second task');
});

 test('a durable topic remains canonical when the latest task is missing or identical', () => {
  const title = load([]);
  const c = {session_id: 'latest', continued_from_session_id: 'unloaded',
    continuation_topic_title: 'Workspace design'};
  assert.equal(title(c).rawTitle, 'Workspace design');
  c.ai_title = 'You are continuing a task from earlier';
  assert.equal(title(c).rawTitle, 'Workspace design');
  c.ai_title = 'Workspace design';
  assert.equal(title(c).rawTitle, 'Workspace design');
});
