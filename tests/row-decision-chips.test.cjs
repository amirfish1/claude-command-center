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


test('pending agent work replaces a completed milestone without asking the user', () => {
  const pending = { did: 'Reviewed the earlier verification.', next_step_user: 'None.',
    completion: { valid: true, ready_to_close: false,
      remaining_actions: [{ text: 'Agent: review the pending independent screenshots.' }] } };
  const info = rowOutcomeView(row(pending));
  assert.equal(info.label, 'Waiting for agent');
  assert.equal(info.kind, 'plan');
  assert.equal(info.text, 'review the pending independent screenshots.');
  const waiting = rowOutcomeView(row({ ...pending, completion: { ...pending.completion,
    remaining_actions: [{ owner: 'external', text: 'Wait for the independent browser report.' }] } }));
  assert.equal(waiting.label, 'Waiting');
  assert.equal(waiting.kind, 'plan');
  assert.equal(rowOutcomeView(row({ ...pending, completion: { valid: false, remaining_actions: pending.completion.remaining_actions } })).text, pending.did);
  assert.equal(rowOutcomeView(row({ ...pending, completion: { valid: true, remaining_actions: [] } })).text, pending.did);
  assert.equal(rowOutcomeView({ ...row(pending), state: 'working' }).label, 'Working');
  assert.equal(rowOutcomeView(row({ ...pending, next_step_user: 'Choose a region.' })).label, 'Decide');
  const reopened = { ...row(pending), archived: true, idea_ticket: { closed: true } };
  assert.equal(rowOutcomeView(reopened).label, 'Waiting for agent');
  assert.equal(rowOutcomeView(reopened).offerClose, false);
  assert.equal(rowOutcomeView({ ...reopened, explicitly_archived: true }).label, 'Archived');
  assert.equal(rowOutcomeView({ ...reopened, trashed: true }).label, 'Archived');
});


test('explicit category questions get literal reply chips without inventing actions', () => {
  const app = require('node:fs').readFileSync(require.resolve('../static/app.js'), 'utf8');
  const start = app.indexOf('  function decisionQuickActions(');
  const end = app.indexOf('\n  }\n', start);
  const derive = require('node:vm').runInNewContext(app.slice(start, end + 4) + '\ndecisionQuickActions;');
  const next = "Say which notification you're seeing (SMS, iOS push, in-app bell, or email), or whether to add an opt-out.";
  const chips = derive(next);
  assert.deepEqual(Array.from(chips, c => c.label), ['SMS', 'iOS push', 'in app bell', 'email']);
  assert.deepEqual(Array.from(chips, c => c.reply), ['SMS.', 'iOS push.', 'in-app bell.', 'email.']);
  assert.equal(rowOutcomeView(row({ next_step_user: next })).label, 'Decide');
  assert.equal(derive('Tell me [Q42:channel] which channel (SMS or email).')[0].reply, '[Q42:channel] SMS.');
  for (const next of [
    'Sign the form (place or date).',
    'Review and send the message (email or SMS).',
    'Tell me which account you mean.',
    'Tell me which option (A, B, C, D, or E).',
    'Tell me which channel (SMS, sms, or email).',
    'Tell me which channel (SMS/email or push).',
    'Tell me which channel (SMS, or email,).',
    'Tell me which notification (a very long multi word option or email).',
  ]) assert.equal(derive(next).length, 0, next);
  const authored = [{ label: 'Other', reply: 'Another channel.' }];
  assert.equal(rowOutcomeView(row({ next_step_user: next, next_actions: authored })).label, 'Next');
});


test('an event-triggered agent step reads Waiting, not Waiting for agent', () => {
  const state = { did: 'Shipped the worker changes.', next_step_user: 'None',
    completion: { valid: true, ready_to_close: false, remaining_actions: [
      { text: 'Agent: on the first went_bad or stuck_loop grade after release, query production for a second-review row.' }] } };
  const info = rowOutcomeView(row(state));
  assert.equal(info.label, 'Waiting');
  assert.equal(info.kind, 'plan');
  assert.match(info.text, /^on the first went_bad/);
});


test('raw footer preserves all comma-qualified decision chips and their exact replies', () => {
  const app = require('node:fs').readFileSync(require.resolve('../static/app.js'), 'utf8');
  const names = ['sessionQuickAction', '_ultraFreshParseState'];
  const source = names.map(name => {
    const start = app.indexOf('  function ' + name + '(');
    return app.slice(start, app.indexOf('\n  }\n', start) + 4);
  }).join('\n');
  const parse = require('node:vm').runInNewContext(source + '\n_ultraFreshParseState;');
  const chips = ['Brand everywhere', 'Demo Meta, private video', 'Personal, keep private', 'Personal, public video']
    .map(label => ({ label, reply: '[Q12:destination] ' + label + '; preserve these qualifications.' }));
  const footer = actions => '<session-state>\nNEXT_STEP_USER: Choose brand or personal destinations.\n<!--\nNEXT_ACTION: '
    + JSON.stringify(actions) + '\n-->\n</session-state>';
  const state = parse(footer(chips));
  assert.deepEqual(JSON.parse(JSON.stringify(state.next_actions)), chips);
  assert.equal(rowOutcomeView(row(state)).label, 'Decide');
  for (const label of ['Brand,', 'Brand,, private', 'Brand,private', 'Brand / private', 'One two three four five']) {
    assert.equal(parse(footer([...chips.slice(0, 3), { label, reply: 'Yes.' }])).next_actions, undefined);
  }
});
