const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(responses) {
  const source = fs.readFileSync('static/app.js', 'utf8');
  const start = source.indexOf('  // Desktop handover controls');
  const end = source.indexOf('  function updateAnnounceButton()', start);
  assert.ok(start >= 0 && end > start, 'handover controls exist');
  const button = { style: {}, disabled: false, textContent: '', title: '',
    addEventListener() {}, setAttribute() {} };
  const requests = [], timers = [], toasts = [];
  const context = vm.createContext({
    document: { getElementById: () => button },
    currentSession: { id: 'test-thread', source: 'codex', cwd: '/tmp/project', repoPath: '/tmp/project' },
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      return { json: async () => responses.shift() };
    },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
    showOpToast: (...args) => toasts.push(args),
  });
  vm.runInContext(source.slice(start, end), context);
  return { context, button, requests, timers, toasts };
}

test('handover waits, then opens the same selected thread', async () => {
  const ui = setup([{ ok: true, pending: true, message: 'Waiting for replies.' }, { ok: true, pending: false }]);
  ui.context.updateResumeButton();
  assert.equal(ui.button.style.display, '');
  await ui.context.handOverToCodexDesktop();
  assert.match(ui.button.textContent, /Waiting.*Cancel/);
  assert.equal(ui.requests[0].url, '/api/codex/client/handover');
  assert.equal(ui.requests[0].body.context.thread_id, 'test-thread');
  await ui.timers[0]();
  assert.equal(ui.button.textContent, 'Opened in Codex Desktop');
  assert.equal(ui.requests.length, 2);
});

test('cancel stops automatic retries', async () => {
  const ui = setup([{ ok: true, pending: true }]);
  await ui.context.handOverToCodexDesktop();
  await ui.context.handOverToCodexDesktop();
  await ui.timers[0]();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.button.textContent, 'Hand over to Codex Desktop');
});

test('switching conversations cancels a pending handover', async () => {
  const ui = setup([{ ok: true, pending: true }]);
  await ui.context.handOverToCodexDesktop();
  ui.context.currentSession = { id: 'other-thread', source: 'claude' };
  ui.context.updateResumeButton();
  await ui.timers[0]();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.button.style.display, 'none');
});

test('failed transfer shows the error and restores the button', async () => {
  const ui = setup([{ ok: false, error: 'Could not verify ownership.' }]);
  await ui.context.handOverToCodexDesktop();
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.button.textContent, 'Hand over to Codex Desktop');
  assert.match(ui.toasts[0][0], /Could not verify ownership/);
});
