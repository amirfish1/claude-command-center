// Steering a delivered row re-sends words already in the transcript. The browser
// must say so, and name the original message, or the server records them as typed.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('static/app.js', 'utf8');
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

test('a delivered peer row names its original message', () => {
  assert.match(render({peer: {msg_id: 'peer-1'}}), /data-steer-origin-msg-id="peer-1"/);
  assert.match(render({attribution: {message_id: 'attr-1'}, peer: {msg_id: 'peer-1'}}), /data-steer-origin-msg-id="attr-1"/);
});

test('a row with no record names nothing, and the pending echo still renders', () => {
  assert.doesNotMatch(render({}), /data-steer-origin-msg-id/);
  assert.doesNotMatch(render(undefined), /data-steer-origin-msg-id/);
});

test('the delivered-row Steer click marks the send as an existing message', () => {
  const click = between("const btn = ev.target.closest('[data-steer-user-message]');", 'if (data && data.queue_pump_started)');
  assert.match(click, /userInitiated: true, steerExisting: true, steerOriginMsgId: btn\.dataset\.steerOriginMsgId/);
  const post = between('    const payload = {\n      session_id: sessionId,', 'if (opts && opts.replaceQueued) payload.replace_queued = true;');
  assert.match(post, /payload\.steer_existing = true/);
  assert.match(post, /payload\.steer_origin_msg_id = opts\.steerOriginMsgId/);
});
