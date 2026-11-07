const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const ctx = { window: {} };
vm.runInNewContext(fs.readFileSync('static/ticket-prose.js', 'utf8'), ctx);
const render = ctx.window.CCCTicketProse.renderTranscript;
const turn = (role) => render({ turns: [{ role, text: 'hello' }] });

test('people sit on the left, system rows in the middle', () => {
  assert.match(turn('client'), /tp-msg is-left is-client/);
  assert.match(turn('owner'), /tp-msg is-left is-owner/);
  assert.match(turn('system'), /tp-msg is-center is-system/);
  assert.match(turn('tool'), /tp-msg is-center is-tool/);
});

test('an assistant named by the provider sits on the right under its own name', () => {
  const html = turn('wren');
  assert.match(html, /tp-msg is-right is-wren/);
  assert.match(html, /<span class="tp-msg-role">wren<\/span>/);
  assert.match(turn('agent'), /tp-msg-role">Agent</);
});
