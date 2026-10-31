const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');

for (const state of ['waiting', 'running', 'blocked']) {
  test(`current owner choice supersedes retained ${state} supervision`, () => {
    const handoff = { state, owner: 'watchtower' };
    const outcome = loadRowOutcomeView({ rowHandoff: () => handoff,
      sessionAttentionStep: () => ({ label: 'Waiting', kind: 'handoff', text: 'Old automation dependency.' }) });
    const next = 'Optional: restore the earlier session, or leave archived.';
    const row = { session_id: 'test-decision', state: 'idle', last_event_type: 'assistant',
      idea_ticket: { handoff }, session_state: { next_step_user: next, next_actions: [
        { label: 'Leave archived', reply: 'Leave the earlier session archived.' },
        { label: 'Restore', reply: 'Restore the earlier session.' },
      ] } };
    const result = outcome(row);
    assert.equal(result.kind, 'next');
    assert.equal(result.label, 'Decide');
    assert.equal(result.next, next);
    assert.equal(result.text, next);
    assert.equal(outcome({ ...row, state: 'working' }).kind, 'working');
    assert.equal(outcome({ ...row, session_state: { next_step_user: 'None.' } }).kind, 'handoff');
  });
}
