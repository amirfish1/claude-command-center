const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('  function sessionStuckAge(');
const end = source.indexOf('  function setSessionDensity(', start);
const h = vm.createContext({
  sessionDensityLane: () => 'coding', sessionDensity: () => 'cozy',
  escapeAttr: s => s,
});
vm.runInContext(source.slice(start, end), h);
const now = 10000;
const row = { is_live: true, sidecar_status: 'active', mtime: now - 600 };
test('quiet unfinished turns cross the threshold even after working state expires', () => {
  assert.equal(h.sessionStuckAge({ ...row, state: 'idle' }, now), 600);
  assert.equal(h.sessionStuckAge({ ...row, mtime: now - 299 }, now), 0);
  assert.equal(h.sessionStuckAge({ ...row, mtime: now - 300 }, now), 300);
  for (const field of ['transcript_mtime', 'modified', 'last_event_ts', 'sidecar_ts', 'pending_tool_ts', 'codex_app_server_last_activity_at']) {
    assert.equal(h.sessionStuckAge({ ...row, [field]: now - 1 }, now), 0, field);
  }
  assert.equal(h.sessionStuckAge({ ...row, last_interacted: now }, now), 600);
});
test('done, human-blocked, non-live, and unknown turns never warn', () => {
  for (const patch of [
    { is_live: false }, { archived: true }, { trashed: true }, { verified: true },
    { pending_spawn: true }, { spawn_failed: true }, { last_event_type: 'result' },
    { sidecar_status: 'waiting' }, { sidecar_status: 'idle' },
    { state: 'waiting' }, { state: 'ended' }, { codex_state: 'idle' },
    { needs_approval: true }, { question_waiting: true }, { sidecar_tool: 'AskUserQuestion' },
    { sidecar_status: undefined }, { mtime: undefined }, { mtime: 'bad' }, { mtime: now + 1 },
  ]) assert.equal(h.sessionStuckAge({ ...row, ...patch }, now), 0, JSON.stringify(patch));
});
test('unfinished tool and working states are engine-independent', () => {
  for (const signal of [{ pending_tool: 'Shell' }, { state: 'working' }, { codex_state: 'working' }, { last_event_type: 'user' }]) {
    assert.equal(h.sessionStuckAge({ is_live: true, mtime: now - 600, ...signal }, now), 600);
  }
});
test('warning has accessible tooltip and is limited to Coding Cozy', () => {
  const fixture = { ...row, mtime: Date.now() / 1000 - 600 };
  assert.match(h.sessionStuckWarningHtml(fixture), /role="img" aria-label="Possibly stuck/);
  for (const [lane, density] of [['coding', 'compact'], ['coding', 'detailed'], ['workers', 'cozy'], ['', 'cozy']]) {
    h.sessionDensityLane = () => lane; h.sessionDensity = () => density;
    assert.equal(h.sessionStuckWarningHtml(fixture), '');
  }
});
