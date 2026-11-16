const { test } = require('node:test');
const assert = require('node:assert/strict');
require('../static/conversation-state.js');
const model = globalThis.CCCConversationState;

test('panes own independent pending echoes, tool groups, and session contexts', () => {
  const first = model.createPane('p1'), second = model.createPane('p2');
  first.pendingSends.push({ text: 'First draft' });
  first.pendingSendsByConv.first = first.pendingSends;
  first.currentSession.id = 'first-session';
  assert.equal(second.pendingSends.length, 0);
  assert.deepEqual(second.pendingSendsByConv, {});
  assert.equal(second.currentSession.id, null);
});

test('compatibility properties follow focus and pane replacement without copying state', () => {
  const workspace = model.createWorkspace(), target = {};
  for (const name of ['currentConversation', 'convLastLine', 'convEventSource',
    '_firstUserMsgRendered', '_pendingSends', '_currentToolGroup', '_currentToolCount', 'currentSession']) {
    model.bindLegacyProperty(target, workspace, name);
  }
  const first = workspace.panes[0], second = model.createPane('p2');
  workspace.panes.push(second);
  target.currentConversation = 'first-session';
  const echoes = [{ text: 'Pending message' }];
  target._pendingSends = echoes;
  assert.equal(first.conversationId, 'first-session');
  assert.strictEqual(first.pendingSends, echoes);
  workspace.activeIndex = 1;
  target.currentConversation = 'second-session';
  target.convLastLine = 42;
  target.currentSession = { id: 'second-session' };
  assert.equal(first.lastLine, 0);
  assert.equal(second.lastLine, 42);
  assert.strictEqual(target.currentSession, second.currentSession);
  workspace.panes[1] = model.createPane('replacement');
  assert.equal(target.currentConversation, null);
  assert.equal(target._pendingSends.length, 0);
  assert.strictEqual(model.activePane(workspace), workspace.panes[1]);
});

test('unknown compatibility names cannot create detached state', () => {
  assert.throws(() => model.bindLegacyProperty({}, model.createWorkspace(), 'unknown'), /Unknown/);
});
