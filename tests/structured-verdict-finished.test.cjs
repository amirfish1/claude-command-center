const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const fn = name => {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }\n', start) + 4);
};
const outcome = loadRowOutcomeView();
const context = { _ultraFreshCurrent: c => c, _applyLiveOverlayToRow: c => c,
  sessionIsOptimisticallySending: () => false, sidebarSessionWorking: () => false,
  rowOutcomeView: outcome };
const section = vm.runInNewContext(fn('ticketSessionSection') + '\nticketSessionSection;', context);
const row = { session_id: 'verdict-test', last_event_type: 'assistant', state: 'waiting',
  last_assistant_text: '{"verdict": "pass"}',
  session_state: { did: 'Returned the requested JSON verdict.', response_complete: true } };

test('a delivered verdict moves to Finished and shows its outcome without reply chips', () => {
  assert.equal(section(row), 'finished');
  assert.equal(outcome(row).kind, 'ready');
  assert.equal(outcome(row).text, row.session_state.did);
  assert.equal(outcome(row).next, undefined);
  assert.equal(section({ ...row, session_state: null }), 'active');
  assert.equal(section({ ...row, explicitly_archived: true }), 'archived');
});

test('new work, pending questions and explicit assessments override verdict delivery', () => {
  for (const patch of [{ last_event_type: 'user' }, { state: 'working' },
      { pending_tool: 'Bash' }, { needs_approval: true }, { question_waiting: true },
      { session_state: { ...row.session_state, next_step_user: 'Choose the scope.' } },
      { session_state: { ...row.session_state, completion: { version: 1, valid: true, ready_to_close: false } } }]) {
    assert.notEqual(section({ ...row, ...patch }), 'finished');
    assert.equal(outcome({ ...row, ...patch }).ready, false);
  }
});

test('raw duplicate frames retain verdict metadata; new text and user turns clear it', () => {
  const entry = { text: row.last_assistant_text, at: 2000, userAt: 1000 };
  const current = vm.runInNewContext(['_ultraFreshTime', '_ultraFreshWaitingAt', '_ultraFreshSendBarrier', '_ultraFreshCurrent']
    .map(fn).join('\n') + '\n_ultraFreshCurrent;', {
      _ultraFresh: { entries: new Map([[row.session_id, entry]]) },
      sessionIsOptimisticallySending: () => false, preSendPreviewFor: () => null,
    });
  assert.equal(current(row).session_state.response_complete, true);
  entry.text = 'A new assessment is pending.';
  assert.equal(current(row).session_state, null);
  entry.userAt = 3000;
  assert.equal(current(row).session_state, null);
  assert.equal(current(row).last_event_type, 'user');
});
