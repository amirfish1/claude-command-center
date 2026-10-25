const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const code = app.slice(app.indexOf('  function sessionReadyToClose('), app.indexOf('  function _trackedRowStatus('));
const ready = vm.runInNewContext(code + '\nsessionReadyToClose;');
const completed = {
  state: 'waiting', last_event_type: 'result',
  session_state: { did: 'Fixed the layout and pushed the change.', next_step_user: 'Reload CCC to load the fix.' },
};

test('completion words and passive next steps alone cannot mark a topic complete', () => {
  assert.equal(ready(completed), false);
  for (const next of ['Nothing required.', 'No action is needed.', 'None.', 'Refresh CCC and check the layout.']) {
    assert.equal(ready({ ...completed, session_state: { ...completed.session_state, next_step_user: next } }), false);
  }
});

test('a real decision or unfinished delivery never becomes ready from idle alone', () => {
  for (const next of ['Reply yes or no on the replay spend.', 'Choose the new price.', 'Reload and answer [Q12:price].', 'Approve the repair.', 'Wake the Mac to install it.', 'Reload, then run the repair.']) {
    assert.equal(ready({ ...completed, session_state: { ...completed.session_state, next_step_user: next } }), false);
  }
  for (const did of ['Fixed locally but not pushed.', 'Pushed the branch; not deployed.', 'Fixed the layout but not visually verified.', 'Build failed.', 'Investigated the report.']) {
    assert.equal(ready({ ...completed, session_state: { ...completed.session_state, did } }), false);
  }
  assert.equal(ready({ state: 'idle' }), false);
});

test('new work and real approval signals override an earlier completion summary', () => {
  for (const patch of [{ state: 'working' }, { codex_state: 'working' }, { needs_approval: true },
    { question_waiting: true }, { pending_spawn: true }, { stale_tool_call: true }, { last_event_type: 'user' }, { archived: true }]) {
    assert.equal(ready({ ...completed, ...patch }), false);
  }
  assert.equal(ready(completed, { codex_state: 'working' }), false);
  assert.equal(ready(completed, { sidecar_in_flight: true, sidecar_status: 'active' }), false);
});

test('ready explanation ends in a Close button instead of a CLOSE label (CCC-136)', () => {
  const start = app.indexOf('      // Outcome line (GOAL-1)');
  const outcome = app.slice(start, app.indexOf('      const _outcomeInline =', start));
  const html = vm.runInNewContext(code + '\n(function(){\n' + outcome + '\nreturn outcomeHtml;})()', {
    c: { ...completed, session_state: {} }, isBacklogRow: false, isGithubPrRow: false, _isWorkingRow: false,
    _codexStateWorking: false, _readyToCloseRow: true,
    sessionIsOptimisticallySending: () => false, preSendPreviewFor: () => null,
    _applyLiveOverlayToRow: row => row, escapeHtml: String, escapeAttr: String,
  });
  assert.match(html, /Reviewed as complete; archive when ready/);
  assert.doesNotMatch(html, /data-label=/);
  // The button follows the text, inside the explanation box.
  assert.match(html, /archive when ready\.<\/span><button [^>]*conv-ready-close[^>]*>Close<\/button><\/div>$/);
  assert.match(vm.runInNewContext(code + "\nreadyToCloseArchiveHtml();"), /data-role="archive" data-archived="true"/);
  assert.doesNotMatch(vm.runInNewContext(code + "\nreadyToCloseArchiveHtml();"), />Archive</);
  assert.doesNotMatch(html, /data-label="Next"/);
});


test('an explicit review expires on newer transcript activity and never overrides active work', () => {
  const reviewed = { ...completed, mtime: 100, modified: 100,
    session_state: {}, idea_ticket: { ready_to_close_reviewed_at: 110 } };
  assert.equal(ready(reviewed), true);
  assert.equal(ready({ ...reviewed, mtime: 120 }), false);
  assert.equal(ready({ ...reviewed, last_event_type: 'user' }), false);
  assert.equal(ready({ ...reviewed, needs_approval: true }), false);
  assert.equal(ready(reviewed, { state: 'working' }), false);
  assert.equal(ready(reviewed, { modified: 120 }), false);
});

// Normalized assessments come from the shared backend parser, not a model verdict.
test('a validated assessment can recommend Close without an old manual review', () => {
  const row = { ...completed, session_state: { completion: { version: 1, valid: true, ready_to_close: true } } };
  assert.equal(ready(row), true);
  for (const completion of [{ ready_to_close: true }, { version: 1, valid: false, ready_to_close: true },
      { version: 1, valid: true, ready_to_close: false }]) {
    assert.equal(ready({ ...row, session_state: { completion } }), false);
  }
  for (const live of [{ state: 'working' }, { last_event_type: 'user' }, { pending_tool: 'Bash' }, { question_waiting: true }]) {
    assert.equal(ready(row, live), false);
  }
});

test('a new incomplete assessment takes precedence over an older review', () => {
  assert.equal(ready({ ...completed, modified: 100, idea_ticket: { ready_to_close_reviewed_at: 110 },
    session_state: { completion: { version: 1, valid: true, ready_to_close: false } } }), false);
});

test('a pending user review overrides completion scores after compaction', () => {
  const row = {state:'idle', last_event_type:'assistant', session_state:{
    next_step_user:'Watch the delivered video and say what to change.',
    completion:{version:1,valid:true,ready_to_close:true}}};
  assert.equal(ready(row), false);
  for (const next_step_user of ['None.', 'No action needed.', '']) {
    assert.equal(ready({...row,session_state:{...row.session_state,next_step_user}}), true);
  }
});
