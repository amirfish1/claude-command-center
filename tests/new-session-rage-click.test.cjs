// A second Send/Enter while a new session is still launching must not spawn
// the same prompt twice; a different prompt, or a retry after the launch
// settles, still goes through.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
  const noop = () => {};
  const input = { value: 'Build the thing', style: {}, closest() { return {}; }, focus: noop };
  const spawned = [];
  const releases = [];
  const ctx = {
    document: { querySelector: () => null, activeElement: null },
    currentConversation: '__new__', currentSession: {}, conversationsData: [], liveStatus: {}, _ttsActive: false,
    $convInput: input, $convSendBtn: { disabled: false }, $convSteerBtn: null,
    activePaneId: () => 'p1', setActivePaneById: noop, liveStatusMatchesOpenConv: () => false,
    guardComposerSend: () => true, announcedFromForPane: () => '', hideSlashCommandMenu: noop,
    rememberComposerCommand: noop, currentConversationRow: () => null,
    spawnFromInlineInput: text => { spawned.push(text); return new Promise(r => releases.push(r)); },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('static/conversation-composer.js', 'utf8'), ctx);
  const send = ctx.CCCConversationComposer.createComposer({ state: ctx, helpers: ctx }).sendToTerminal;
  return { input, spawned, releases, send };
}

test('rapid repeat submits of the same new-session prompt spawn once', async () => {
  const h = harness();
  const first = h.send();
  const second = h.send();
  const third = h.send();
  await second; await third;
  assert.deepEqual(h.spawned, ['Build the thing']);
  h.releases[0]();
  await first;
});

test('a different prompt still launches while the first is in flight', async () => {
  const h = harness();
  const first = h.send();
  h.input.value = 'Another task';
  const other = h.send();
  assert.deepEqual(h.spawned, ['Build the thing', 'Another task']);
  h.releases.forEach(r => r());
  await first; await other;
});

test('the same prompt can be resent once its launch settles', async () => {
  const h = harness();
  const first = h.send();
  h.releases[0]();
  await first;
  const retry = h.send();
  assert.equal(h.spawned.length, 2);
  h.releases[1]();
  await retry;
});
