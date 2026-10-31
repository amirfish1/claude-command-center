const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
function extract(name) {
  const start = app.indexOf(`  function ${name}(`);
  const end = app.indexOf('\n  }', start) + 4;
  assert.ok(start >= 0 && end > start);
  return app.slice(start, end);
}
const ctx = vm.createContext({sessionIsOptimisticallySending: () => false});
vm.runInContext(extract('sidebarSessionWorking') + '\n' + extract('hasReadOnlyWork'), ctx);
const completed = {last_event_type: 'result', last_assistant_preview: 'Reviewed the settings.', state: 'idle'};

test('completed work stays read-only across process-presence refreshes', () => {
  for (const is_live of [false, true, false, true]) {
    assert.equal(ctx.hasReadOnlyWork({...completed, is_live}), true);
  }
  assert.equal(ctx.hasReadOnlyWork({is_live: true, state: 'idle', last_assistant_preview: 'Reviewed.'}), true);
});

test('active work and new user turns invalidate the completed-work badge', () => {
  for (const patch of [
    {state: 'working'}, {codex_state: 'working'},
    {is_live: true, sidecar_status: 'active', sidecar_in_flight: true},
    {pending_spawn: true}, {needs_approval: true}, {question_waiting: true},
    {last_event_type: 'user'},
  ]) assert.equal(ctx.hasReadOnlyWork({...completed, ...patch}), false, JSON.stringify(patch));
  assert.equal(ctx.hasReadOnlyWork({is_live: true, last_assistant_text: 'Earlier reply'}), false);
});

test('edits and terminal lifecycle states suppress the badge', () => {
  for (const patch of [{has_edit: true}, {has_commit: true}, {has_push: true},
    {tail_pr_number: 12}, {archived: true}, {verified: true}, {source: 'backlog'}]) {
    assert.equal(ctx.hasReadOnlyWork({...completed, ...patch}), false);
  }
  assert.equal(ctx.hasReadOnlyWork({is_live: false}), false);
});
