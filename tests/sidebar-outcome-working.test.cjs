const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');

const source = require('./legacy-dashboard-source.cjs');
const start = source.indexOf('      // Outcome line (GOAL-1)');
const end = source.indexOf('      const _outcomeInline =', start);
assert.ok(start >= 0 && end > start);
const outcomeCode = source.slice(start, end);

function renderOutcome(row, overlay = row) {
  const escape = text => String(text).replace(/[&<>"]/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  }[ch]));
  const rowOutcomeView = loadRowOutcomeView({ _applyLiveOverlayToRow: () => overlay });
  return vm.runInNewContext('(function () {\n' + outcomeCode + '\nreturn outcomeHtml;\n})()', {
    _outView: rowOutcomeView(row, {}),
    _readyToCloseRow: false, readyToCloseArchiveHtml: () => '',
    _childLaneRow: false, rowHandoff: () => null, sessionAttentionStep: () => null, attentionChipsHtml: () => '',
    c: row, isBacklogRow: false, isGithubPrRow: false, _isWorkingRow: false,
    _codexStateWorking: row.codex_state === 'working',
    sessionIsOptimisticallySending: () => false, preSendPreviewFor: () => null,
    _applyLiveOverlayToRow: () => overlay, escapeHtml: escape, escapeAttr: escape,
  });
}

const row = {
  id: 'session-test', source: 'codex', codex_state: 'working',
  last_assistant_text: 'Thinking',
  session_state: { did: 'Completed the previous turn.', next_step_user: 'Reload to load the fix.' },
};

test('a running Codex turn keeps Working through generic-state polling gaps', () => {
  for (const state of ['idle', 'working', null, 'idle', 'working']) {
    const html = renderOutcome({ ...row, state });
    assert.match(html, /data-label="Working"/);
    assert.doesNotMatch(html, /data-label="Next"/);
  }
});

test('a newer live overlay keeps Working when the census row lags', () => {
  const stale = { ...row, codex_state: 'idle', state: 'idle' };
  assert.match(renderOutcome(stale, { ...stale, codex_state: 'working' }), /data-label="Working"/);
});

test('the next step returns when the Codex turn finishes', () => {
  const html = renderOutcome({ ...row, codex_state: 'idle', state: 'idle' });
  assert.match(html, /data-label="Next"/);
  assert.match(html, /Reload to load the fix/);
});

test('tool activity never replaces the agent message explanation', () => {
  const html = renderOutcome({ ...row, last_assistant_text: 'I am checking the new message path.' }, {
    ...row, is_live: true, sidecar_status: 'active', sidecar_in_flight: true,
    sidecar_ts: Date.now() / 1000, sidecar_tool: 'Bash', sidecar_file: 'noisy-command',
  });
  assert.match(html, /I am checking the new message path/);
  assert.doesNotMatch(html, /Bash|noisy-command/);
});

test('a running Antigravity Gemini row shows its thought preview', () => {
  const preview = { id: 'gemini-test', source: 'antigravity', engine: 'antigravity',
    is_live: true, state: 'working', last_event_type: 'assistant',
    last_assistant_text: '**Checking settings**\n\nI am comparing current & proposed values.' };
  const html = renderOutcome(preview);
  assert.match(html, /data-label="Working"/);
  assert.match(html, /Checking settings I am comparing current &amp; proposed values/);
  assert.doesNotMatch(html, /Working…|\*\*/);
  assert.match(renderOutcome({ ...preview, last_event_type: 'user' }), /data-label="Working">Working…<\/span>/);
});


test('a resumed Antigravity ACP row shows current activity before its transcript catches up', () => {
  const now = Date.now() / 1000;
  const old = {
    id: 'antigravity-resume', source: 'antigravity', modified: now - 600,
    last_event_type: 'result', state: 'ended',
    last_assistant_text: 'I will start once the earlier check finishes.',
  };
  const live = {
    ...old, state: 'working', is_live: true, acp_status: 'running',
    sidecar_in_flight: true, sidecar_ts: now, working_since: now - 70,
    sidecar_tool: 'edit_file',
  };
  assert.match(renderOutcome(old, live), /data-label="Working">Editing file<\/span>/);
  const betweenTools = { ...live, sidecar_tool: null };
  assert.match(renderOutcome(old, betweenTools), /Agent activity just now/);
  const noOutput = { ...betweenTools, sidecar_ts: live.working_since };
  assert.match(renderOutcome(old, noOutput), /Waiting for agent output/);
  const newReply = { ...old, modified: now - 25, last_event_type: 'assistant',
    last_assistant_text: 'I have started changing the settings.' };
  assert.match(renderOutcome(newReply, live), /I have started changing the settings/);
  const quiet = { ...live, working_since: now - 500, sidecar_ts: now - 380 };
  assert.match(renderOutcome(old, quiet), /No agent activity for 6 min/);
});

test('an idle Antigravity ACP connection does not erase native background activity', () => {
  const sid = 'antigravity-background';
  const now = Date.now() / 1000;
  const row = { id: sid, source: 'antigravity', is_live: true, modified: now - 60,
    state: 'idle', last_assistant_text: 'Previous finished turn.' };
  const packet = { is_live: true, acp_status: 'idle', sidecar_status: 'active',
    sidecar_in_flight: true, sidecar_ts: now, working_since: now - 5, sidecar_tool: 'read_file' };
  const overlay = new Map([[sid, packet]]);
  const start = source.indexOf('  function _applyLiveOverlayToRow(');
  const apply = vm.runInNewContext(source.slice(start, source.indexOf('\n  }\n', start) + 4)
    + '\n_applyLiveOverlayToRow;', {
    _sessionLiveOverlay: overlay,
    _LIVE_OVERLAY_KEYS: Object.keys(packet),
  });
  assert.equal(apply(row).state, 'working');
  assert.match(renderOutcome(row, apply(row)), /data-label="Working">Reading file<\/span>/);
  overlay.set(sid, { ...packet, sidecar_status: 'idle', sidecar_in_flight: false, working_since: null });
  const final = { ...row, state: 'working', modified: now, last_assistant_text: 'Background work finished.' };
  assert.equal(apply(final).state, 'idle');
  assert.match(renderOutcome(final, apply(final)), /Background work finished/);
  assert.doesNotMatch(renderOutcome(final, apply(final)), /data-label="Working"/);
});
