// CCC-276: a decision never reads "Next: Answer Q12 ..."; it reads
// "Decide: <the choice>" and gets one chip per option, even for footers
// written before NEXT_ACTION accepted arrays.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');

const rowOutcomeView = loadRowOutcomeView();
const row = state => ({ session_id: 's1', last_event_type: 'assistant', last_assistant_text: 'Done.', session_state: state });

test('legacy decision prose becomes Decide with derived option chips', () => {
  const next = 'Answer [Q589:lcpp-intake-scope]: preserve all LCPP questions or use the shorter Body Evolutions intake.';
  const info = rowOutcomeView(row({ next_step_user: next }));
  assert.equal(info.label, 'Decide');
  assert.equal(info.kind, 'next');
  assert.equal(info.next, next);
  assert.ok(!/^answer/i.test(info.text), info.text);
  const helpers = require('node:vm');
  const fs = require('node:fs');
  const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
  const start = app.indexOf('  function decisionQuickActions(');
  const end = app.indexOf('\n  }\n', start);
  const derive = helpers.runInNewContext(app.slice(start, end + 4) + '\ndecisionQuickActions;');
  const chips = derive(next);
  assert.equal(chips.length, 2);
  assert.equal(chips[0].reply, '[Q589:lcpp-intake-scope] Preserve all LCPP questions.');
  assert.equal(chips[1].reply, '[Q589:lcpp-intake-scope] Use the shorter Body Evolutions intake.');
  assert.ok(chips.every(c => c.label.length <= 33));
  assert.equal(derive('Say yes to a dashboard and worker restart.').length, 0);
  assert.equal(derive('Answer Q12 to establish the scope.').length, 0);
});

test('model-authored decision chips win and a single go-ahead stays Next', () => {
  const chips = [{ label: 'Keep all', reply: 'Keep all.' }, { label: 'Shorter form', reply: 'Use the shorter form.' }];
  const info = rowOutcomeView(row({ next_step_user: 'Keep all intake questions or the shorter form?', next_actions: chips, next_action: chips[0] }));
  assert.equal(info.label, 'Decide');
  const single = rowOutcomeView(row({ next_step_user: 'Say yes to a restart.', next_action: { label: 'Restart', reply: 'Yes.' } }));
  assert.equal(single.label, 'Next');
  assert.equal(single.text, 'Say yes to a restart.');
});
