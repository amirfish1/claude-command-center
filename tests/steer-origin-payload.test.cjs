// Delivered transcript rows must not offer Steer; unsent echoes still can.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = require('./legacy-dashboard-source.cjs');
function between(start, end) {
  const first = app.indexOf(start);
  assert.ok(first >= 0, start);
  const last = app.indexOf(end, first);
  assert.ok(last > first, end);
  return app.slice(first, last);
}
const html = between('  function userMessageSteerHtml(', "  document.addEventListener('click', (ev) => {");
const context = vm.createContext({
  currentSession: {source: 'codex'},
  codexTurnSteerable: () => true,
  devinTurnSteerable: () => false,
  escapeAttr: String,
});
vm.runInContext(html, context);
const render = ev => vm.runInContext('userMessageSteerHtml', context)('words', null, null, ev);

test('delivered owner and peer transcript events never offer Steer', () => {
  for (const source of ['codex', 'devin-cli']) {
    context.currentSession = {source};
    for (const ev of [{}, {peer: {msg_id: 'peer-1'}}, {attribution: {message_id: 'attr-1'}}]) {
      assert.equal(render(ev), '');
    }
  }
});

test('pending echoes retain Steer for supported engines', () => {
  for (const source of ['codex', 'devin-cli']) {
    context.currentSession = {source};
    assert.match(render(undefined), /data-steer-user-message/);
  }
});

test('stale delivered-row clicks return before the injection request', () => {
  const click = between("const btn = ev.target.closest('[data-steer-user-message]');", 'if (data && data.queue_pump_started)');
  assert.match(click, /if \(!isPending\) \{\s*btn\.remove\(\);\s*return;/);
  assert.ok(click.indexOf('if (!isPending)') < click.indexOf('await postInjectInput'));
  assert.doesNotMatch(click, /steerExisting/);
  assert.match(click, /replaceQueued: true/);
});
