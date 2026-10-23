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

test('completed work with a reload or no-action next step is ready', () => {
  assert.equal(ready(completed), true);
  for (const next of ['Nothing required.', 'No action is needed.', 'None.', 'Refresh CCC and check the layout.']) {
    assert.equal(ready({ ...completed, session_state: { ...completed.session_state, next_step_user: next } }), true);
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

test('ready outcome offers the existing archive action instead of asking for a reply', () => {
  const start = app.indexOf('      // Outcome line (GOAL-1)');
  const outcome = app.slice(start, app.indexOf('      const _outcomeInline =', start));
  const html = vm.runInNewContext(code + '\n(function(){\n' + outcome + '\nreturn outcomeHtml;})()', {
    c: completed, isBacklogRow: false, isGithubPrRow: false, _isWorkingRow: false,
    _codexStateWorking: false, _readyToCloseRow: true,
    sessionIsOptimisticallySending: () => false, preSendPreviewFor: () => null,
    _applyLiveOverlayToRow: row => row, escapeHtml: String, escapeAttr: String,
  });
  assert.match(html, /data-label="Ready to close"/);
  assert.match(html, /conv-ready-archive.*data-role="archive" data-archived="true"/);
  assert.doesNotMatch(html, /data-label="Next step"/);
});
