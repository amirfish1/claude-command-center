const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');

const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const helpers = app.slice(app.indexOf('  // Row actions are model-authored replies'), app.indexOf('  // CCC-1218:'));
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');

test('row action replies, stale state, failure recovery and responsive rendering', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 800 });
    await page.setContent(`<style>${css}</style><div id="convList" style="width:380px">
      <div class="conv-item" data-session-id="restart-session">
        <div class="wa-row"><div class="wa-body"><div class="wa-line1"><span class="wa-title">Check the running version</span></div>
          <div class="wa-line2"><span class="wa-preview">Next: Say yes to a dashboard and worker restart.</span></div></div></div>
        <div class="conv-title-row"><div class="conv-main-row has-inline-step">
          <span class="conv-title">Check the running version</span>
          <div class="conv-outcome is-next is-inline"><span class="conv-outcome-step" data-label="Next">Say yes to a dashboard and worker restart.</span></div>
        </div></div>
      </div></div>`);
    await page.addScriptTag({ content: `
      window.row = {session_id:'restart-session', state:'idle', last_event_type:'assistant',
        session_state:{next_step_user:'Say yes to a dashboard and worker restart.',next_action:{label:'Restart',reply:'Yes, restart the dashboard and worker.'}}};
      window.sent = []; window.inferences = 0; window.sendFails = false; window.sendQueued = false; window.toasts = [];
      const _ultraFreshRow = () => window.row;
      const _ultraFreshCurrent = c => c;
      const _applyLiveOverlayToRow = c => c;
      const _ultraFreshExplanation = c => ({kind:c.state === 'working' ? 'working' : 'next', text:c.session_state.next_step_user});
      const postInjectInput = async (...args) => { sent.push(args); await new Promise(r => setTimeout(r, 120)); return sendFails ? {ok:false,error:'Offline'} : {ok:true,queued:sendQueued}; };
      const markSessionSending = () => { row.state = 'working'; syncRowQuickActions(); };
      const showOpToast = (...args) => toasts.push(args);
      const refreshConversationList = () => {};
      window.fetch = async () => { inferences++; throw new Error('Unexpected model request'); };
      ${helpers}
      window.syncActions = syncRowQuickActions;
      syncRowQuickActions();
    ` });
    const visibleButton = () => page.evaluate(() => [...document.querySelectorAll('.conv-quick-action')]
      .find(b => b.getBoundingClientRect().width > 0)?.outerHTML);
    await page.waitForFunction(() => document.querySelector('.conv-quick-action'));
    assert.match(await visibleButton(), />Restart</);
    assert.match(await visibleButton(), /Yes, restart the dashboard and worker/);
    assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.conv-outcome')).display),
      'flex', 'desktop rules keep chip beside the pending request');
    await page.evaluate(() => { syncActions(); syncActions(); });
    assert.equal(await page.evaluate(() => inferences), 0, 'rendering never requests a model');
    assert.equal(await page.evaluate(() => {
      const button = [...document.querySelectorAll('.conv-quick-action')].find(b => b.getBoundingClientRect().width);
      button.focus(); syncActions(); return document.activeElement === button;
    }), true, 'unchanged updates preserve button and keyboard focus');

    // The mobile markup renders the same originating answer metadata.
    await page.setViewport({ width: 390, height: 844 });
    await page.waitForFunction(() => document.querySelector('.wa-line2 .conv-quick-action'));
    const mobile = await page.evaluate(() => {
      const row = document.querySelector('.conv-item').getBoundingClientRect();
      const b = document.querySelector('.wa-line2 .conv-quick-action').getBoundingClientRect();
      return {width:b.width, height:b.height, right:b.right, rowRight:row.right};
    });
    assert.ok(mobile.width > 0 && mobile.height >= 32);
    assert.ok(mobile.right <= mobile.rowRight + 1, 'phone chip stays inside row');

    // Failure restores the chip, leaving the user able to retry.
    await page.evaluate(() => { sendFails = true; document.querySelector('.wa-line2 .conv-quick-action').click(); syncActions(); });
    assert.deepEqual(await page.$$eval('.conv-quick-action-status', els=>els.map(el=>el.textContent)), ['Sending…','Sending…']);
    await page.waitForFunction(() => { const b = document.querySelector('.wa-line2 .conv-quick-action'); return b && !b.disabled; });
    assert.equal(await page.evaluate(() => sent.length), 1);
    assert.match(await visibleButton(), />Restart</);
    assert.match(await page.evaluate(() => toasts.at(-1)[0]), /Could not send Restart: Offline/);
    await page.evaluate(() => {
      sendFails = false;
      const b = document.querySelector('.wa-line2 .conv-quick-action'); b.click(); b.click();
    });
    await page.waitForFunction(() => row.state === 'working');
    assert.deepEqual(await page.evaluate(() => sent[1]), ['restart-session', 'Yes, restart the dashboard and worker.', 'send', {userInitiated:true}]);
    assert.equal(await page.evaluate(() => sent.length), 2, 'double tap sends only once');
    assert.equal(await page.evaluate(() => document.querySelectorAll('.conv-quick-action').length), 0);
    assert.deepEqual(await page.$$eval('.conv-quick-action-status', els=>els.map(el=>el.textContent)), ['Sent','Sent']);
    await page.evaluate(() => syncActions());
    assert.equal(await page.$eval('.wa-line2 .conv-quick-action-status',el=>el.textContent),'Sent', 'successful send feedback survives refresh');

    // Old replies without metadata keep their text without model fallback.
    await page.evaluate(() => {
      row.state = 'idle'; delete row.session_state.next_action; syncActions();
    });
    assert.equal(await page.evaluate(() => document.querySelectorAll('.conv-quick-action').length), 0);
    assert.equal(await page.evaluate(() => inferences), 0);
    // A different label is used verbatim rather than guessed from restart text.
    await page.evaluate(() => {
      row.session_state.next_action = {label:'Proceed',reply:'Please restart only the dashboard.'}; syncActions();
    });
    assert.match(await visibleButton(), />Proceed</);
    // A short model-authored phrase must survive validation and send verbatim.
    await page.evaluate(() => {
      row.session_state.next_action = {label:'Build triage',reply:'Yes, build the triage routine.'}; syncActions();
    });
    assert.match(await visibleButton(), />Build triage</);
    assert.ok(await page.evaluate(() => {
      const button = document.querySelector('.wa-line2 .conv-quick-action').getBoundingClientRect();
      const row = document.querySelector('.conv-item').getBoundingClientRect();
      return button.width > 0 && button.right <= row.right + 1;
    }), 'short phrase stays inside the phone row');
    await page.evaluate(() => document.querySelector('.wa-line2 .conv-quick-action').click());
    await page.waitForFunction(() => row.state === 'working');
    assert.deepEqual(await page.evaluate(() => sent[2]), ['restart-session', 'Yes, build the triage routine.', 'send', {userInitiated:true}]);
    await page.evaluate(() => {
      row.state = 'idle';
      row.session_state.next_action = {label:'<script>',reply:'Yes'}; syncActions();
    });
    assert.equal(await page.evaluate(() => document.querySelectorAll('.conv-quick-action').length), 0);

    // A click on an already painted action also rechecks the source.
    await page.evaluate(() => {
      row.session_state.next_action = {label:'Restart',reply:'Yes, restart the dashboard and worker.'}; syncActions();
    });
    await page.waitForFunction(() => document.querySelector('.wa-line2 .conv-quick-action'));
    await page.evaluate(() => {
      row.session_state.next_action.reply = 'Restart only the dashboard.';
      document.querySelector('.wa-line2 .conv-quick-action').click();
    });
    assert.equal(await page.evaluate(() => sent.length), 3, 'stale click is rejected');
    assert.match(await page.evaluate(() => toasts.at(-1)[0]), /action changed/i);
    assert.equal(await page.evaluate(() => toasts.at(-1)[1]), 'error', 'changed action must be visible without debug mode');
    // Stream enrichment must not invalidate a still-identical authored action.
    await page.evaluate(() => {
      row.last_assistant_preview = 'A short archive preview.'; syncActions();
      row.last_assistant_text = 'The full live answer arrived, with exactly the same action.';
      sendQueued = true;
      document.querySelector('.wa-line2 .conv-quick-action').click();
      syncActions();
    });
    await page.waitForFunction(() => document.querySelector('.wa-line2 .conv-quick-action-status')?.textContent === 'Queued');
    assert.equal(await page.evaluate(() => sent.length), 4);
    assert.deepEqual(await page.evaluate(() => sent[3]), ['restart-session','Restart only the dashboard.','send', {userInitiated:true}]);
    assert.equal(await page.$$eval('.conv-quick-action',els=>els.length),0);
    assert.equal(await page.evaluate(() => inferences), 0, 'no inference request in any state');
  } finally {
    await browser.close();
  }
});

test('human row replies use composer provenance without promoting automated sends', async () => {
  const vm = require('node:vm');
  const start = app.indexOf('  async function postInjectInput(');
  const code = app.slice(start, app.indexOf('  function steeredToastText(', start));
  const sent = [];
  const post = vm.runInNewContext(code + '\npostInjectInput;', {
    durableActionId: () => 'inject-test-id',
    fetch: async (url, opts) => {
      sent.push({url, payload:JSON.parse(opts.body)});
      return {ok:true, json:async()=>({ok:true})};
    },
  });
  await post('test-session', 'Yes, enable the scoped test routine.', 'send', {userInitiated:true});
  assert.deepEqual(sent[0], {url:'/api/inject-input', payload:{session_id:'test-session',
    text:'Yes, enable the scoped test routine.', mode:'send', idempotency_key:'inject-test-id', source:'composer',
    composer_form:'ccc-composer-v1'}});
  await post('test-session', 'Automated nudge.', 'send');
  assert.equal(Object.hasOwn(sent[1].payload, 'source'), false);
  await post('test-session', 'Peer report.', 'send', {announcedFrom:'Verifier',userInitiated:false});
  assert.equal(Object.hasOwn(sent[2].payload, 'source'), false);
  assert.equal(sent[2].payload.announced_from, 'Verifier');
  // A Steer click is the operator's own input: unattributed steers rendered
  // as peer messages and never cleared their queued echo (CCC-236).
  const steerCalls = app.match(/postInjectInput\([^;]*'steer'[^;]*;/g) || [];
  const humanSteers = steerCalls.filter(c => /replaceQueued/.test(c));
  assert.equal(humanSteers.length, 3);
  for (const call of humanSteers) assert.match(call, /userInitiated: true/);
});
