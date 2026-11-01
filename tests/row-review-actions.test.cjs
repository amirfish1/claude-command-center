const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const puppeteer = require('../require-puppeteer.js');
const { findChromePath } = require('../puppeteer-browser-config.js');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
const css = fs.readFileSync(require.resolve('../static/app.css'), 'utf8');
const helpers = app.slice(app.indexOf('  // Row actions are model-authored replies'), app.indexOf('  // CCC-1218:'));
function source(name) {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }\n', start) + 4);
}
const actions = vm.runInNewContext(
  ['sessionQuickAction', 'decisionQuickActions', 'sessionQuickActions'].map(source).join('\n') + '\nsessionQuickActions;');
const next = 'Watch https://example.test/film.mp4 on your phone and say whether this direction works.';

test('review requests expose the artifact even without authored action or valid completion', () => {
  const state = { next_step_user: next, next_action: null, completion: { valid: false } };
  assert.deepEqual(JSON.parse(JSON.stringify(actions(state))), [
    { label: 'Watch video', href: 'https://example.test/film.mp4', derived: true },
  ]);
  assert.equal(loadRowOutcomeView()({ session_id: 'review', last_event_type: 'assistant',
    last_assistant_text: 'The film is ready for review.', session_state: state }).label, 'Next');
  state.next_action = { label: 'Revise', reply: 'Please revise the ending.' };
  assert.equal(actions(state)[0].reply, state.next_action.reply, 'authored replies keep precedence');
  assert.equal(actions({ next_step_user: 'Read https://example.test/draft and share your feedback.' })[0].label, 'Open review');
});

test('review fallback cannot invent approval or turn arbitrary URL text into an action', () => {
  for (const text of [
    'Watch the film and say whether it works.',
    'Watch https://example.test/film.mp4 on your phone.',
    'Watch javascript:alert(1) and give feedback.',
    'Watch https://user:password@example.test/film.mp4 and give feedback.',
    'Watch https://example.test\\@evil.test/film.mp4 and give feedback.',
    'Watch https://example.test/<script> and give feedback.',
    'Watch https://example.test/film.mp4\nand give feedback.',
    'Approve https://example.test/film.mp4 and give feedback.',
    'Say yes to a restart.',
  ]) assert.equal(actions({ next_step_user: text }).length, 0, text);
});

test('review chips open the artifact on desktop and phone without sending a session reply', async () => {
  const browser = await puppeteer.launch({ executablePath: findChromePath(), args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.setContent(`<style>${css}</style><div id="convList" style="width:340px">
      <div class="conv-item" data-session-id="review">
        <div class="wa-row"><div class="wa-body"><div class="wa-line1">Review the film</div>
          <div class="wa-line2"><span class="wa-preview">Next: Review the film</span></div></div></div>
        <div class="conv-title-row"><div class="conv-main-row has-inline-step">
          <span class="conv-title">Review the film</span>
          <div class="conv-outcome is-next is-inline"><span class="conv-outcome-step">Review the film</span></div>
        </div></div>
      </div></div>`);
    await page.addScriptTag({ content: `
      window.row = {session_id:'review', state:'idle', last_event_type:'assistant',
        session_state:{next_step_user:${JSON.stringify(next)},next_action:null,completion:{valid:false}}};
      window.opened=[]; window.sent=[]; window.toasts=[];
      window.open=(...args)=>opened.push(args);
      const _ultraFreshRow=()=>row, _ultraFreshCurrent=c=>c, _applyLiveOverlayToRow=c=>c;
      const rowOutcomeView=c=>({kind:c.state==='working'?'working':'next',text:c.session_state.next_step_user});
      const postInjectInput=async (...args)=>{sent.push(args);return {ok:true}};
      const markSessionSending=()=>{};
      const refreshConversationList=()=>{};
      const showOpToast=(...args)=>toasts.push(args);
      ${helpers}
      window.syncActions=syncRowQuickActions;
      syncActions();
    ` });
    for (const [width, height, host] of [[1440, 900, '.conv-outcome'], [390, 844, '.wa-line2']]) {
      await page.setViewport({ width, height });
      const selector = host + ' .conv-quick-action';
      const chip = await page.$eval(selector, el => {
        const r = el.getBoundingClientRect();
        return { text: el.textContent, width: r.width, height: r.height, right: r.right,
          rowRight: el.closest('.conv-item').getBoundingClientRect().right, title: el.title };
      });
      assert.equal(chip.text, 'Watch video');
      assert.ok(chip.width > 0 && chip.height >= (width < 500 ? 32 : 26));
      assert.ok(chip.right <= chip.rowRight + 1);
      assert.match(chip.title, /^Open for review:/);
      await page.click(selector);
    }
    assert.deepEqual(await page.evaluate(() => opened), [
      ['https://example.test/film.mp4', '_blank', 'noopener,noreferrer'],
      ['https://example.test/film.mp4', '_blank', 'noopener,noreferrer'],
    ]);
    assert.equal(await page.evaluate(() => sent.length), 0);
    await page.evaluate(() => {
      row.session_state.next_step_user='Watch https://example.test/new.mp4 and give feedback.';
      document.querySelector('.wa-line2 .conv-quick-action').click();
    });
    assert.equal(await page.evaluate(() => opened.length), 2, 'stale links are not opened');
    await page.evaluate(() => { row.state='working'; syncActions(); });
    assert.equal(await page.$$eval('.conv-quick-action', els => els.length), 0);
  } finally {
    await browser.close();
  }
});
