const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function extract(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert(a >= 0 && b > a, start);
  return source.slice(a, b);
}
function helpers() {
  const context = vm.createContext({});
  vm.runInContext(extract('  function isRoutineCodexCoordination(', '  function renderConversationEvents(')
    + extract('  function codexActiveItemLabel(', '  function normalizeMarkdownLinkTarget('), context);
  return context;
}
test('hide routine turn observations, preserve recovery, unknown events and real messages', () => {
  const h = helpers();
  for (const kind of ['external_turn_started', 'external_turn_ended', 'ccc_turn_started', 'ccc_turn_completed']) {
    assert.equal(h.isRoutineCodexCoordination({type:'system', subtype:'codex_coordination', kind}), true);
    assert.equal(h.isRoutineCodexCoordination({type:'assistant', subtype:'codex_coordination', kind}), false);
  }
  for (const kind of ['input_queued', 'turn_recovery_exhausted', 'compaction_recovery_started', 'unknown']) {
    assert.equal(h.isRoutineCodexCoordination({type:'system', subtype:'codex_coordination', kind}), false);
  }
  assert.equal(h.isRoutineCodexCoordination(null), false);
});
test('activity labels follow actual item data and preserve specific tools', () => {
  const h = helpers();
  assert.equal(h.codexActiveItemLabel({type:'reasoning',tool:'Thinking'}).label, 'Thinking…');
  assert.equal(h.codexActiveItemLabel({type:'agentMessage'}).label, 'Writing…');
  assert.equal(h.codexActiveItemLabel({type:'plan',tool:'Plan'}).label, 'Planning…');
  assert.equal(h.codexActiveItemLabel({type:'commandExecution',tool:'Bash',detail:'node --check'}).label, 'Bash');
  assert.equal(h.codexActiveItemLabel({tool:'custom.tool',detail:'example'}).detail, 'example');
  assert.equal(h.codexActiveItemLabel({type:'toString'}).label, 'toString');
  assert.equal(h.codexActiveItemLabel(null).label, '');
});
