const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const start = source.indexOf('      // Outcome line (GOAL-1)');
const end = source.indexOf('      const _outcomeInline =', start);
assert.ok(start >= 0 && end > start);
const outcomeCode = source.slice(start, end);

function renderOutcome(row, overlay = row) {
  const escape = text => String(text).replace(/[&<>"]/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
  }[ch]));
  return vm.runInNewContext('(function () {\n' + outcomeCode + '\nreturn outcomeHtml;\n})()', {
    _readyToCloseRow: false, readyToCloseArchiveHtml: () => '',
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
