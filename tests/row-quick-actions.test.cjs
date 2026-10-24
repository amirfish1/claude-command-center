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
        session_state:{next_step_user:'Say yes to a dashboard and worker restart.'}};
      window.sent = []; window.inferences = 0; window.sendFails = false; window.deferModel = false;
      const _ultraFreshRow = () => window.row;
      const _ultraFreshCurrent = c => c;
      const _applyLiveOverlayToRow = c => c;
      const _ultraFreshExplanation = c => ({kind:c.state === 'working' ? 'working' : 'next', text:c.session_state.next_step_user});
      const postInjectInput = async (...args) => { sent.push(args); await new Promise(r => setTimeout(r, 30)); return sendFails ? {ok:false,error:'Offline'} : {ok:true}; };
      const markSessionSending = () => { row.state = 'working'; syncRowQuickActions(); };
      const showOpToast = () => {};
      const refreshConversationList = () => {};
      window.fetch = async () => {
        inferences++;
        if (deferModel) await new Promise(r => { window.releaseModel = r; });
        return {ok:true,json:async()=>({ok:true,action:{label:'Restart',reply:'Yes, restart the dashboard and worker.'}})};
      };
      ${helpers}
      window.syncActions = syncRowQuickActions;
      window.clearActions = () => _rowQuickActions.clear();
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
    assert.equal(await page.evaluate(() => inferences), 1, 'polling reuses inference');
    assert.equal(await page.evaluate(() => {
      const button = [...document.querySelectorAll('.conv-quick-action')].find(b => b.getBoundingClientRect().width);
      button.focus(); syncActions(); return document.activeElement === button;
    }), true, 'unchanged updates preserve button and keyboard focus');

    // The mobile markup has a separate slot but reuses the same inference.
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
    await page.evaluate(() => { sendFails = true; document.querySelector('.wa-line2 .conv-quick-action').click(); });
    await page.waitForFunction(() => !document.querySelector('.wa-line2 .conv-quick-action').disabled);
    assert.equal(await page.evaluate(() => sent.length), 1);
    assert.match(await visibleButton(), />Restart</);
    await page.evaluate(() => {
      sendFails = false;
      const b = document.querySelector('.wa-line2 .conv-quick-action'); b.click(); b.click();
    });
    await page.waitForFunction(() => row.state === 'working');
    assert.deepEqual(await page.evaluate(() => sent[1]), ['restart-session', 'Yes, restart the dashboard and worker.', 'send']);
    assert.equal(await page.evaluate(() => sent.length), 2, 'double tap sends only once');
    assert.equal(await page.evaluate(() => document.querySelectorAll('.conv-quick-action').length), 0);

    // An inference finishing after a new turn cannot install an old action.
    await page.evaluate(() => {
      row.state = 'idle'; row.session_state.next_step_user = 'Confirm the next restart.';
      deferModel = true; syncActions();
    });
    await page.waitForFunction(() => typeof releaseModel === 'function');
    await page.evaluate(() => { row.last_event_type = 'user'; releaseModel(); syncActions(); });
    await page.waitForFunction(() => !document.querySelector('.conv-quick-action-slot'));
    assert.equal(await page.evaluate(() => sent.length), 2);

    // A click on an already painted action also rechecks the source.
    await page.evaluate(() => {
      deferModel = false; row.last_event_type = 'assistant'; syncActions();
    });
    await page.waitForFunction(() => document.querySelector('.wa-line2 .conv-quick-action'));
    await page.evaluate(() => {
      row.session_state.next_step_user = 'Choose staging or production.';
      document.querySelector('.wa-line2 .conv-quick-action').click();
    });
    assert.equal(await page.evaluate(() => sent.length), 2, 'stale click is rejected');
  } finally {
    await browser.close();
  }
});
