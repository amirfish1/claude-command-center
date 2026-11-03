const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const helpers = app.slice(app.indexOf('  // Row actions are model-authored replies'), app.indexOf('  // CCC-1218:'));
function source(name) {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }\n', start) + 4);
}
const investigation = 'Investigate the lost queued messages by tracing their delivery IDs through the activity log.';
const row = () => ({ session_id: 'investigation', state: 'idle', last_event_type: 'assistant',
  last_assistant_text: 'The earlier repair shipped. The message investigation remains open.',
  session_state: { next_step_user: 'None.', next_action: null, completion: {
    valid: true, ready_to_close: false, remaining_actions: [
      { text: investigation, within_scope: true, reversible: true },
    ],
  } } });
const actionSource = vm.runInNewContext(
  ['sessionQuickAction', 'decisionQuickActions', 'sessionQuickActions', 'rowQuickActionSource'].map(source).join('\n')
    + '\nrowQuickActionSource;', {
    _applyLiveOverlayToRow: c => c, _rowQuickActionSending: new Set(), rowOutcomeView: loadRowOutcomeView(),
  });

test('unassigned investigations get a literal continuation without inventing approval', () => {
  const r = row();
  const action = actionSource(r).action;
  assert.equal(action.label, 'Continue investigation');
  assert.equal(action.reply, 'Continue the investigation: ' + investigation);
  assert.equal(actionSource({ ...r, state: 'working' }), null);
  assert.equal(actionSource({ ...r, codex_state: 'working' }), null);
  for (const flags of [{ archived: true }, { trashed: true }, { needs_approval: true },
    { question_waiting: true }, { pending_spawn: true }, { last_event_type: 'user' }]) {
    assert.equal(actionSource({ ...r, ...flags }), null);
  }
  const first = r.session_state.completion.remaining_actions[0];
  for (const change of [{ owner: 'agent' }, { owner: 'external' }, { owner: 'user' },
    { text: 'Agent: ' + investigation }, { text: 'External: ' + investigation },
    { within_scope: false }, { reversible: false }, { text: 'Deploy the pending fix.' },
    { text: investigation + '\nThen proceed.' }, { text: 'Investigate ' + 'x'.repeat(400) }]) {
    r.session_state.completion.remaining_actions[0] = { ...first, ...change };
    assert.equal(actionSource(r), null, JSON.stringify(change));
  }
  r.session_state.completion.remaining_actions[0] = first;
  r.session_state.completion.valid = false;
  assert.equal(actionSource(r), null);
  r.session_state.completion.valid = true;
  r.session_state.next_step_user = 'Choose a region.';
  r.session_state.next_action = { label: 'Europe', reply: 'Use Europe.' };
  assert.equal(actionSource(r).action.reply, 'Use Europe.', 'authored owner actions keep precedence');
});

test('continuation chips render on desktop and phone, reject stale clicks and send once', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
    await page.setContent(`<style>${css}</style><div id="convList" style="width:340px">
      <div class="conv-item" data-session-id="investigation">
        <div class="wa-row"><div class="wa-body"><div class="wa-line1">Investigate queued messages</div>
          <div class="wa-line2"><span class="wa-preview">Next: ${investigation}</span></div></div></div>
        <div class="conv-title-row"><div class="conv-main-row has-inline-step">
          <span class="conv-title">Investigate queued messages</span>
          <div class="conv-outcome is-plan is-inline"><span class="conv-outcome-step">${investigation}</span></div>
        </div></div>
      </div></div>`);
    await page.addScriptTag({ content: `
      window.row = ${JSON.stringify(row())}; window.sent = []; window.toasts = [];
      const _ultraFreshRow = () => row, _ultraFreshCurrent = c => c, _applyLiveOverlayToRow = c => c;
      const sessionIsOptimisticallySending = () => false, preSendPreviewFor = () => null;
      const rowHandoff = () => null, sessionAttentionStep = () => null;
      const postInjectInput = async (...args) => { sent.push(args); return {ok:true}; };
      const markSessionSending = () => { row.state = 'working'; syncRowQuickActions(); };
      const refreshConversationList = () => {}, showOpToast = (...args) => toasts.push(args);
      ${['_ultraFreshTime', 'sidebarSessionWorking', 'sessionReadyToClose', 'rowOutcomeView'].map(source).join('\n')}
      ${helpers}
      window.syncActions = syncRowQuickActions; syncActions();
    ` });
    for (const [width, height, host] of [[1440, 900, '.conv-outcome'], [390, 844, '.wa-line2']]) {
      await page.setViewport({ width, height });
      const chip = await page.$eval(host + ' .conv-quick-action', e => {
        const r = e.getBoundingClientRect();
        return { text: e.textContent, width: r.width, height: r.height,
          right: r.right, rowRight: e.closest('.conv-item').getBoundingClientRect().right };
      });
      assert.equal(chip.text, 'Continue investigation');
      assert.ok(chip.width > 0 && chip.height >= (width < 500 ? 32 : 26));
      assert.ok(chip.right <= chip.rowRight + 1);
    }
    await page.evaluate(() => {
      row.session_state.completion.remaining_actions[0].text = 'Investigate a different delivery.';
      document.querySelector('.wa-line2 .conv-quick-action').click();
    });
    assert.equal(await page.evaluate(() => sent.length), 0, 'changed investigation rejects the old chip');
    assert.match(await page.evaluate(() => toasts.at(-1)[0]), /action changed/i);
    await page.evaluate(() => {
      syncActions(); const b = document.querySelector('.wa-line2 .conv-quick-action'); b.click(); b.click();
    });
    await page.waitForFunction(() => row.state === 'working');
    assert.deepEqual(await page.evaluate(() => sent), [['investigation',
      'Continue the investigation: Investigate a different delivery.', 'send', {userInitiated:true}]]);
    assert.equal(await page.$$eval('.conv-quick-action', els => els.length), 0);
  } finally { await browser.close(); }
});
