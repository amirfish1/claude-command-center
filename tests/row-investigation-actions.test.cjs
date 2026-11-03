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
    version: 1, valid: true, ready_to_close: false, caveats: [],
    resolution: { score: 0.9, evidence: 'Earlier repair shipped.' },
    verification: { score: 0.8, evidence: 'Earlier checks passed.' },
    delivery: { score: 1, evidence: 'Earlier change delivered.' },
    decision_dependency: { score: 0, evidence: 'No decision needed.' }, remaining_actions: [
      { text: investigation, within_scope: true, reversible: true, confidence: 0.6, downside: 0.1 },
    ],
  } } });
const checks = new Map();
const { actionSource, transcriptIdle } = vm.runInNewContext(
  ['sessionQuickAction', 'decisionQuickActions', 'sessionQuickActions', 'rowQuickActionSource',
    'sessionCompletionDetails', '_ultraFreshParseState', 'rowInvestigationTranscriptIdle'].map(source).join('\n')
    + '\n({actionSource:rowQuickActionSource,transcriptIdle:rowInvestigationTranscriptIdle});', {
    _applyLiveOverlayToRow: c => c, _rowQuickActionSending: new Set(), rowOutcomeView: loadRowOutcomeView(),
    _rowInvestigationChecks: checks,
  });
const footer = r => '<session-state>\nNEXT_STEP_USER: None.\nCOMPLETION: '
  + JSON.stringify(r.session_state.completion) + '\n</session-state>';
const finalReply = r => ({ type: 'assistant', blocks: [{ kind: 'text', text: footer(r) }] });

test('unassigned investigations get a literal continuation without inventing approval', () => {
  const r = row();
  assert.equal(actionSource(r), null, 'a stale idle list snapshot is insufficient');
  const candidate = actionSource(r, true);
  checks.set(r.session_id, { key: candidate.key, idle: true, until: Date.now() + 3000 });
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

test('an older footer cannot confirm a resumed investigation is idle', () => {
  const r = row(), candidate = actionSource(r, true), oldFooter = finalReply(r);
  assert.equal(transcriptIdle(candidate, [oldFooter]), true);
  for (const last of [
    { type: 'user_text', text: 'Continue investigation.' },
    { type: 'assistant', blocks: [{ kind: 'tool_use', name: 'Bash' }] },
    { type: 'tool_result', text: 'Still running' },
    { type: 'assistant', blocks: [{ kind: 'thinking' }] },
    { type: 'assistant', blocks: [{ kind: 'text', text: 'Tracing the current delivery.' }] },
  ]) assert.equal(transcriptIdle(candidate, [oldFooter, last]), false, last.type);
  assert.equal(transcriptIdle(candidate, []), false, 'an unavailable tail cannot confirm idleness');
  assert.equal(transcriptIdle(candidate, [{ ...oldFooter, parent_tool_use_id: 'child' }]), false);
  checks.set(r.session_id, { key: candidate.key, idle: true, until: Date.now() - 1 });
  assert.equal(actionSource(r), null, 'expired confirmation cannot authorize a send');
  checks.set(r.session_id, { key: candidate.key, idle: false, until: Date.now() + 3000 });
  assert.equal(actionSource(r), null, 'running transcript overrides the retained list footer');
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
      window.transcriptRunning = false;
      window.fetch = async () => ({ok:true,json:async()=>({events:transcriptRunning
        ? [{type:'assistant',blocks:[{kind:'tool_use',name:'Bash'}]}]
        : [{type:'assistant',blocks:[{kind:'text',text:'<session-state>\\nNEXT_STEP_USER: None.\\nCOMPLETION: '
          + JSON.stringify(row.session_state.completion) + '\\n</session-state>'}]}]})});
      const _ultraFreshRow = () => row, _ultraFreshCurrent = c => c, _applyLiveOverlayToRow = c => c;
      const sessionIsOptimisticallySending = () => false, preSendPreviewFor = () => null;
      const rowHandoff = () => null, sessionAttentionStep = () => null;
      const postInjectInput = async (...args) => { sent.push(args); return {ok:true}; };
      const markSessionSending = () => { row.state = 'working'; syncRowQuickActions(); };
      const refreshConversationList = () => {}, showOpToast = (...args) => toasts.push(args);
      ${['_ultraFreshTime', 'sidebarSessionWorking', 'sessionReadyToClose', 'rowOutcomeView',
        'sessionCompletionDetails', '_ultraFreshParseState'].map(source).join('\n')}
      ${helpers}
      window.syncActions = syncRowQuickActions; syncActions();
    ` });
    await page.waitForSelector('.conv-quick-action');
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
    await page.waitForSelector('.wa-line2 .conv-quick-action');
    await page.evaluate(() => {
      transcriptRunning = true;
      document.querySelector('.wa-line2 .conv-quick-action').click();
    });
    await page.waitForFunction(() => toasts.some(t => /already running/.test(t[0])));
    assert.equal(await page.evaluate(() => sent.length), 0, 'click rechecks the transcript before sending');
    await page.waitForFunction(() => !document.querySelector('.conv-quick-action'));
    await page.evaluate(() => { transcriptRunning = false; _rowInvestigationChecks.clear(); syncActions(); });
    await page.waitForSelector('.wa-line2 .conv-quick-action');
    await page.evaluate(() => { transcriptRunning = true; });
    await page.waitForFunction(() => !document.querySelector('.conv-quick-action'), {timeout:10000});
    assert.equal(await page.evaluate(() => sent.length), 0, 'periodic confirmation removes a chip after a remote resume');
    await page.evaluate(() => { transcriptRunning = false; _rowInvestigationChecks.clear(); syncActions(); });
    await page.waitForSelector('.wa-line2 .conv-quick-action');
    await page.evaluate(() => {
      const savedFetch = window.fetch;
      window.fetch = async (...args) => {
        const response = await savedFetch(...args);
        row.state = 'working';
        return response;
      };
      document.querySelector('.wa-line2 .conv-quick-action').click();
      window.restoreFetch = () => { window.fetch = savedFetch; };
    });
    await page.waitForFunction(() => toasts.at(-1)[0].includes('Could not send') && toasts.at(-1)[0].includes('action changed'));
    assert.equal(await page.evaluate(() => sent.length), 0, 'a live update during confirmation invalidates the send');
    await page.evaluate(() => { restoreFetch(); row.state = 'idle'; _rowInvestigationChecks.clear(); syncActions(); });
    await page.waitForSelector('.wa-line2 .conv-quick-action');
    await page.evaluate(() => {
      syncActions(); const b = document.querySelector('.wa-line2 .conv-quick-action'); b.click(); b.click();
    });
    await page.waitForFunction(() => row.state === 'working');
    assert.deepEqual(await page.evaluate(() => sent), [['investigation',
      'Continue the investigation: Investigate a different delivery.', 'send', {userInitiated:true}]]);
    assert.equal(await page.$$eval('.conv-quick-action', els => els.length), 0);
  } finally { await browser.close(); }
});
