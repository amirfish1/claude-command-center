const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const { loadRowOutcomeView } = require('./row_outcome_view_harness.cjs');
const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
function source(name) {
  const start = app.indexOf('  function ' + name + '(');
  return app.slice(start, app.indexOf('\n  }\n', start) + 4);
}
const checks = new Map();
const { actions, actionSource, transcriptIdle } = vm.runInNewContext(
  ['sessionQuickAction', 'decisionQuickActions', 'sessionQuickActions', 'rowQuickActionSource',
    'sessionCompletionDetails', '_ultraFreshParseState', 'rowInvestigationTranscriptIdle'].map(source).join('\n')
    + '\n({actions:sessionQuickActions,actionSource:rowQuickActionSource,transcriptIdle:rowInvestigationTranscriptIdle});', {
    _applyLiveOverlayToRow: c => c, _rowQuickActionSending: new Set(), _rowInvestigationChecks: checks,
    rowOutcomeView: loadRowOutcomeView({sessionAttentionStep: c => c.stalled
      ? {kind:'next',label:'Stalled',text:'Delivery outcome unknown; inspect receipt before explicit recovery.'} : null}),
  });
function row() {
  return {session_id:'design-session',state:'idle',last_event_type:'result',stalled:true,
    session_state:{did:'Saved and verified the design handoff.',next_step_user:'None.',completion:{
      version:1,valid:true,ready_to_close:false,
      scope:{goal:'Build a feature',done_when:'Feature works',closure_met:false,
        evidence:'Current authorization is design only; implementation remains unauthorized.'},
      resolution:{score:0.4,evidence:'Design saved'}, verification:{score:0.4,evidence:'Design reviewed'},
      delivery:{score:0.4,evidence:'Design delivered'},decision_dependency:{score:0,evidence:'No design decision'},caveats:[],
      remaining_actions:[{owner:'agent',text:'Only if a later instruction authorizes implementation, implement the feature.',
        within_scope:false,reversible:true,confidence:0.85,downside:0.15}],
    }}};
}
test('saved design offers explicit Build choice through a paused supervision warning', () => {
  const r = row();
  assert.equal(actionSource(r), null, 'retained list state cannot confirm idleness');
  const candidate = actionSource(r,true);
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()+3000});
  const action = actionSource(r).action;
  assert.equal(action.label,'Build');
  assert.match(action.reply,/implementation only, not paid calls, spending, launch, deployment, or customer contact/);
  assert.match(action.reply,/do not resend an unknown delivery/);
  assert.ok(action.reply.length <= 400);
  assert.equal(actionSource({...r,stalled:false}).action.label,'Build');
  for (const flags of [{state:'working'},{archived:true},{trashed:true},{needs_approval:true},
    {question_waiting:true},{pending_spawn:true},{last_event_type:'user'}]) {
    assert.equal(actionSource({...r,...flags}),null,JSON.stringify(flags));
  }
  const footer = '<session-state>\nNEXT_STEP_USER: None.\nDID: '+r.session_state.did
    +'\nCOMPLETION: '+JSON.stringify(r.session_state.completion)+'\n</session-state>';
  const final = {type:'assistant',blocks:[{kind:'text',text:footer}]};
  assert.equal(transcriptIdle(candidate,[final]),true);
  assert.equal(transcriptIdle(candidate,[final,{type:'user_text',text:'Continue'}]),false);
  assert.equal(transcriptIdle(candidate,[final,{type:'assistant',blocks:[{kind:'tool_use'}]}]),false);
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()-1});
  assert.equal(actionSource(r),null);
});
test('Build is limited to completed design milestones and preserves authored choices', () => {
  for (const change of [s=>s.did='Researching the design.',s=>s.next_step_user='Choose a region.',
    s=>s.completion.valid=false,s=>s.completion.ready_to_close=true,
    s=>s.completion.scope.closure_met=true,s=>s.completion.scope.evidence='Implementation is authorized.',
    s=>s.completion.remaining_actions[0].within_scope=true,
    s=>s.completion.remaining_actions[0].reversible=false,
    s=>s.completion.remaining_actions[0].owner='external',
    s=>s.completion.remaining_actions[0].text='Wait for a design review.']) {
    const s=row().session_state; change(s); assert.equal(actions(s).length,0);
  }
  const s=row().session_state;
  s.next_action={label:'Review',reply:'Review the design first.'};
  assert.equal(actions(s)[0].label,'Review');
});
test('Build uses the checked reply once and rejects changed transcripts before sending', async () => {
  const puppeteer = require('../require-puppeteer.js');
  const { findChromePath } = require('../puppeteer-browser-config.js');
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setContent(`<div id="convList"><div class="conv-item" style="height:100px" data-session-id="design-session">
      <div class="conv-outcome is-next"></div><div class="wa-line2"></div></div></div>`);
    const helpers = app.slice(app.indexOf('  // Row actions are model-authored replies'),app.indexOf('  // CCC-1218:'));
    await page.addScriptTag({content:`
      window.row=${JSON.stringify(row())}; window.sent=[]; window.toasts=[]; window.running=false;
      const _ultraFreshRow=()=>row, _ultraFreshCurrent=c=>c, _applyLiveOverlayToRow=c=>c;
      const rowOutcomeView=c=>({kind:c.state==='working'?'working':'next',text:'Paused supervision'});
      const postInjectInput=async (...args)=>{sent.push(args);return {ok:true}};
      const markSessionSending=()=>{row.state='working';};
      const refreshConversationList=()=>{},showOpToast=(...args)=>toasts.push(args);
      ${['sessionCompletionDetails','_ultraFreshParseState'].map(source).join('\n')}
      window.fetch=async()=>({ok:true,json:async()=>({events:running
        ? [{type:'user_text',text:'Already resumed'}]
        : [{type:'assistant',blocks:[{kind:'text',text:'<session-state>\\nDID: '+row.session_state.did
          +'\\nNEXT_STEP_USER: None.\\nCOMPLETION: '+JSON.stringify(row.session_state.completion)+'\\n</session-state>'}]}]})});
      ${helpers}
      window.syncActions=syncRowQuickActions;syncActions();
    `});
    await page.waitForSelector('.conv-quick-action');
    await page.evaluate(()=>{running=true;document.querySelector('.conv-quick-action').click();});
    await page.waitForFunction(()=>toasts.some(t=>/transcript changed/.test(t[0])));
    assert.equal(await page.evaluate(()=>sent.length),0);
    await page.evaluate(()=>{running=false;_rowInvestigationChecks.clear();syncActions();});
    await page.waitForSelector('.conv-quick-action');
    await page.evaluate(()=>{const b=document.querySelector('.conv-quick-action');b.click();b.click();});
    await page.waitForFunction(()=>sent.length===1);
    const sent=await page.evaluate(()=>sent);
    assert.equal(sent[0][0],'design-session');
    assert.equal(sent[0][1],actions(row().session_state)[0].reply);
    assert.deepEqual(sent[0].slice(2),['send',{userInitiated:true}]);
  } finally {await browser.close();}
});
