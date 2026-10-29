const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(process.env.CCC_APP_SOURCE || 'static/app.js', 'utf8');
function fn(name, indent = 2) {
  const start = app.indexOf('function ' + name + '(');
  assert.ok(start >= 0);
  return (app.slice(start - 6, start) === 'async ' ? 'async ' : '') + app.slice(start, app.indexOf('\n' + ' '.repeat(indent) + '}', start) + indent + 2);
}
function harness(split, options = {}) {
  const inputListeners = [];
  const input = { addEventListener(type, listener) { if (type === 'input') inputListeners.push(listener); }, value: 'Submitted message', style: {}, closest() { return {}; }, focus() {}, dispatchEvent() {} };
  const button = { disabled: false };
  const notices = [], removed = [], warnings = [];
  let resolve;
  const response = new Promise(r => { resolve = r; });
  const noop = () => {};
  const ctx = {
    console: { warn: (...args) => warnings.push(args) }, Event: class {},
    document: { querySelector: () => null },
    currentConversation: 'conversation', currentSession: { id: 'session', source: 'codex' },
    conversationsData: [], liveStatus: {}, _ttsActive: false,
    $convInput: input, $convSendBtn: button, $convSteerBtn: null,
    $cpInput: input, $cpSendBtn: button,
    activePaneId: () => 'p1', setActivePaneById: noop, liveStatusMatchesOpenConv: () => false,
    guardComposerSend: () => true, announcedFromForPane: () => '',
    hideSlashCommandMenu: noop, rememberComposerCommand: noop,
    appendPendingSendEcho: () => ({ id: 'echo' }), announcedInjectionPreview: text => text,
    updateInputBar: noop, _autosizeTextareaLike: noop, clearInputDraftForConversation: noop,
    looksDormantNoProcess: () => false, durableActionId: () => 'test-action', tapbackTakePending: () => '',
    fetch: () => response, getConvViewForPane: () => null, getConvView: () => null,
    renderConvWakeOutcome: () => { if (options.uiError) throw Error('Status rendering failed'); },
    markPendingSendQueued: () => { if (split && options.uiError) throw Error('Status rendering failed'); },
    showOpToast: (...args) => notices.push(args), setTimeout: noop, refreshConversationList: noop,
    removePendingSendEcho: pending => removed.push(pending),
    renderInlineWakeError: noop, formatInjectFailure: () => 'Rejected',
    isCursorUsageLimitFailure: () => false, repoNotAllowedToastAction: noop,
    moveInputCaretToEnd: noop,
  };
  vm.createContext(ctx);
  vm.runInContext(fn('composerUserEditRevision') + '\n' + fn('clearAcceptedComposerInput') + '\n' + fn('restoreInputAfterSendFailure') + '\n' + fn(split ? 'sendToSplitTerminal' : 'sendToTerminal', split ? 4 : 2), ctx);
  const send = split ? ctx.sendToSplitTerminal : ctx.sendToTerminal;
  return { input, edit(value) { input.value = value; inputListeners.forEach(listener => listener({ isTrusted: true })); }, switchConversation() { ctx.currentConversation = 'other'; }, button, notices, removed, warnings, send, respond(data, ok = true) { resolve({ ok, status: ok ? 200 : 500, json: async () => data }); } };
}
for (const split of [false, true]) {
  const label = split ? 'split composer' : 'pane composer';
  test(label + ': queued acceptance clears the submitted message', async () => {
    const h = harness(split);
    const sending = h.send();
    assert.equal(h.input.value, '');
    h.respond({ ok: true, queued: true, via: 'codex-app-queued' });
    await sending;
    assert.equal(h.input.value, '');
    assert.equal(h.removed.length, 0);
    assert.equal(h.warnings.length, 0);
    assert.equal(h.button.disabled, false);
  });
  test(label + ': accepted message stays cleared even if status rendering fails', async () => {
    const h = harness(split, { uiError: true });
    const sending = h.send();
    assert.equal(h.input.value, '');
    h.respond({ ok: true, queued: true, via: 'codex-app-queued' });
    await sending;
    assert.equal(h.input.value, '');
    assert.equal(h.removed.length, 0);
    assert.equal(h.warnings.length, 1);
    assert.match(h.notices[0][0], /Message accepted/);
    assert.equal(h.button.disabled, false);
  });
  test(label + ': delayed accepted response preserves the next draft', async () => {
    const h = harness(split, { uiError: true });
    const sending = h.send();
    h.input.value = 'Next message';
    h.respond({ ok: true, queued: true, via: 'codex-app-queued' });
    await sending;
    assert.equal(h.input.value, 'Next message');
    assert.equal(h.removed.length, 0);
  });
  test(label + ': acceptance retires a restored copy of the submitted draft', async () => {
    const h = harness(split);
    const sending = h.send();
    h.input.value = 'Submitted message';
    h.respond({ ok: true, via: 'acp-prompt' });
    await sending;
    assert.equal(h.input.value, '');
  });
  test(label + ': acceptance preserves an identical message typed again', async () => {
    const h = harness(split);
    const sending = h.send();
    h.edit('Submitted message');
    h.respond({ ok: true, via: 'acp-prompt' });
    await sending;
    assert.equal(h.input.value, 'Submitted message');
  });
  test(label + ': acceptance preserves a different conversation composer', async () => {
    const h = harness(split);
    const sending = h.send();
    h.switchConversation();
    h.input.value = 'Submitted message';
    h.respond({ ok: true, via: 'acp-prompt' });
    await sending;
    assert.equal(h.input.value, 'Submitted message');
  });
  test(label + ': rejected message is restored for retry', async () => {
    const h = harness(split);
    const sending = h.send();
    h.respond({ ok: false, error: 'Rejected' }, false);
    await sending;
    assert.equal(h.input.value, 'Submitted message');
    assert.equal(h.removed.length, 1);
  });
  test(label + ': rejection does not overwrite the next draft', async () => {
    const h = harness(split);
    const sending = h.send();
    h.input.value = 'Next message';
    h.respond({ ok: false, error: 'Rejected' }, false);
    await sending;
    assert.equal(h.input.value, 'Next message');
  });
}
