const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = app.indexOf('  const _ATTN_NONE_RE =');
const end = app.indexOf('  // The server derives readiness', start);
const sessionAttentionStep = vm.runInNewContext(app.slice(start, end)
  + '\nsessionAttentionStep;', {});
const outcome = loadRowOutcomeView({ sessionAttentionStep });
const plan = {
  did: 'Started production.', next_step_user: 'None.',
  completion: { valid: true, ready_to_close: false, decision_dependency: { score: 0 },
    remaining_actions: [
      { owner: 'agent', text: 'Finish production and verify delivery.' },
      { owner: 'user', text: 'After delivery, check the attachment preview.' },
    ] },
};
const row = {
  session_id: 'continuation', state: 'idle', last_event_type: 'assistant',
  last_assistant_text: 'Production is underway; no further approval is needed.',
  session_state: plan,
  idea_ticket: { handoff: { state: 'blocked', session: 'earlier-conversation', question: 'Old preview check.' } },
};

test('a continuation shows its current agent plan instead of an earlier conversation block', () => {
  const result = outcome(row);
  assert.equal(result.label, 'Waiting for agent');
  assert.equal(result.kind, 'plan');
  assert.equal(result.text, plan.completion.remaining_actions[0].text);
  assert.equal(result.attn, null);
  assert.equal(outcome({ ...row, state: 'working' }).label, 'Working');
});

test('a later user check does not block pending agent work without a handoff', () => {
  assert.equal(outcome({ ...row, idea_ticket: null }).kind, 'plan');
});

test('blocks on the current conversation and unassessed continuations remain visible', () => {
  for (const handoff of [
    { ...row.idea_ticket.handoff, session: 'continuation' },
    { state: 'blocked', question: 'A block without a recorded session.' },
  ]) {
    const result = outcome({ ...row, idea_ticket: { handoff } });
    assert.equal(result.label, 'Needs you');
    assert.equal(result.text, handoff.question);
  }
  const current = { ...row, idea_ticket: { handoff: { ...row.idea_ticket.handoff, session: 'continuation' } } };
  assert.equal(outcome({ ...current, session_state: null }).label, 'Needs you');
  assert.equal(outcome({ ...current, session_state: { ...plan, completion: { valid: false } } }).label, 'Needs you');
});

test('a list refresh without a footer cannot restore an earlier conversation question', () => {
  const result = outcome({ ...row, session_state: null });
  assert.equal(result.label, 'Update');
  assert.doesNotMatch(result.text, /Old preview check/);
  assert.equal(result.attn, null);
});

test('current user decisions and first user actions still ask the owner', () => {
  const decision = outcome({ ...row, session_state: { ...plan, next_step_user: 'Choose a delivery format.' } });
  assert.equal(decision.label, 'Decide');
  const needsDecision = { ...plan.completion, decision_dependency: { score: 0.5 } };
  assert.equal(outcome({ ...row, idea_ticket: null, session_state: { ...plan, completion: needsDecision } }).kind, 'next');
  const userFirst = { ...plan.completion, remaining_actions: [...plan.completion.remaining_actions].reverse() };
  const result = outcome({ ...row, idea_ticket: null, session_state: { ...plan, completion: userFirst } });
  assert.equal(result.kind, 'next');
  assert.equal(result.text, userFirst.remaining_actions[0].text);
});

test('a paused takeover with unconfirmed delivery reads Stalled, not passive Waiting', () => {
  const stalled = outcome({ ...row, session_state: null, idea_ticket: { handoff: {
    state: 'waiting', session: 'continuation', supervision_state: 'paused_unreachable',
    delivery_state: 'uncertain',
    waiting_on: 'Delivery outcome unknown; inspect receipt before explicit recovery' } } });
  assert.equal(stalled.label, 'Stalled');
  assert.match(stalled.text, /Nothing is progressing/);
  assert.equal(stalled.attn.handoff, 'stalled');
  assert.notEqual(stalled.attn.handoff, 'blocked'); // blocked renders an Answer button
  assert.equal(stalled.attn.needsYou, true);
  const real = outcome({ ...row, session_state: null, idea_ticket: { handoff: {
    state: 'waiting', session: 'continuation', waiting_on: 'IDEAS-5' } } });
  assert.equal(real.label, 'Waiting');
});
