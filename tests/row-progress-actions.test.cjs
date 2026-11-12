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
const { actionSource, transcriptIdle } = vm.runInNewContext(
  ['sessionQuickAction', 'decisionQuickActions', 'sessionQuickActions', 'rowQuickActionSource',
    'sessionCompletionDetails', '_ultraFreshParseState', 'rowInvestigationTranscriptIdle'].map(source).join('\n')
    + '\n({actionSource:rowQuickActionSource,transcriptIdle:rowInvestigationTranscriptIdle});', {
    _applyLiveOverlayToRow: c => c, _rowQuickActionSending: new Set(), _rowInvestigationChecks: checks,
    rowOutcomeView: loadRowOutcomeView(),
  });
function row() {
  return {session_id:'callback-session',state:'idle',last_event_type:'assistant',
    session_state:{did:'Confirmed the release gate is active.',next_step_user:'None.',completion:{
      version:1,valid:true,ready_to_close:false,
      scope:{goal:'Finish release',done_when:'Release verified',closure_met:false,evidence:'Gate active'},
      resolution:{score:0.7,evidence:'Repairs pushed'}, verification:{score:0.7,evidence:'Gate active'},
      delivery:{score:0.5,evidence:'Release pending'},decision_dependency:{score:0,evidence:'No input needed'},caveats:[],
      remaining_actions:[{text:'agent: On the completion callback, inspect the gate result and repair remaining failures.',
        within_scope:true,reversible:true,confidence:0.95,downside:0.05}],
    }}};
}
test('callback waits offer the concrete pending work while preserving its prerequisites', () => {
  const r=row();
  assert.equal(actionSource(r),null,'a retained footer alone is insufficient');
  const candidate=actionSource(r,true);
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()+3000});
  assert.equal(actionSource(r).action.label,'Continue work');
  assert.match(candidate.action.reply,/Carry out the pending work: On the completion callback, inspect the gate result and repair remaining failures/);
  assert.match(candidate.action.reply,/Honor its stated trigger and prerequisites, then verify the result/);
  assert.match(candidate.action.reply,/Preserve existing approval and spending limits; do not bypass dependencies or fabricate evidence/);
  assert.doesNotMatch(candidate.action.reply,/Check progress|report its current status/);
  assert.equal(loadRowOutcomeView()(r).label,'Waiting');
  const footer='<session-state>\nDID: '+r.session_state.did+'\nNEXT_STEP_USER: None.\nCOMPLETION: '
    +JSON.stringify(r.session_state.completion)+'\n</session-state>';
  const final={type:'assistant',blocks:[{kind:'text',text:footer}]};
  assert.equal(transcriptIdle(candidate,[final]),true);
  for(const event of [{type:'user_text',text:'Continue'}, {type:'tool_result',text:'Running'},
    {type:'assistant',blocks:[{kind:'tool_use'}]}]) assert.equal(transcriptIdle(candidate,[final,event]),false);
  const plan=r.session_state.completion.remaining_actions[0];
  plan.owner='agent';plan.text=plan.text.replace('agent: ','');
  assert.equal(actionSource(r,true).action.label,'Continue work','structured and legacy owners both work');
});
test('work chips do not replace working, external, unscoped or authored steps', () => {
  for(const flags of [{state:'working'},{codex_state:'working'},{archived:true},{trashed:true},
    {needs_approval:true},{question_waiting:true},{pending_spawn:true},{last_event_type:'user'}])
    assert.equal(actionSource({...row(),...flags},true),null,JSON.stringify(flags));
  for(const change of [{text:'external: On the callback, inspect the result.'},
    {text:'agent: Inspect the result now.'},{text:'agent: On the callback, '+ 'x'.repeat(340)},
    {within_scope:false},{reversible:false}]) {
    const r=row();Object.assign(r.session_state.completion.remaining_actions[0],change);
    assert.equal(actionSource(r,true),null,JSON.stringify(change));
  }
  const r=row();r.session_state.next_step_user='Choose a region.';
  r.session_state.next_action={label:'Europe',reply:'Use Europe.'};
  assert.equal(actionSource(r,true).action.label,'Europe');
});

test('scheduled verification offers to perform the real task rather than summarize status', () => {
  const r=row();
  const task='On the next hourly review run, verify old questions are reconsidered using current authority and no redundant owner cards are created';
  r.session_state.completion.remaining_actions[0].text='Agent: '+task;
  const candidate=actionSource(r,true);
  assert.equal(candidate.action.label,'Run verification');
  assert.ok(candidate.action.reply.includes(task));
  assert.match(candidate.action.reply,/Carry out the pending work/);
  assert.match(candidate.action.reply,/Honor its stated trigger and prerequisites/);
  assert.equal(actionSource(r),null,'fresh transcript confirmation still gates the chip');
  checks.set(r.session_id,{key:candidate.key,idle:true,until:Date.now()+3000});
  assert.equal(actionSource(r).action.label,'Run verification');
  r.session_state.completion.remaining_actions[0].text='Agent: '+task+' changed';
  assert.equal(actionSource(r),null,'changed work invalidates transcript confirmation');
});

test('pending-work chips send the concrete task once and reject a resumed transcript', async () => {
  const puppeteer = require('../require-puppeteer.js');
  const { findChromePath } = require('../puppeteer-browser-config.js');
  const browser = await puppeteer.launch({executablePath:findChromePath(),args:['--no-sandbox']});
  try {
    const page = await browser.newPage();
    await page.setContent(`<div id="convList"><div class="conv-item" style="height:100px" data-session-id="callback-session">
      <div class="conv-outcome is-plan"></div><div class="wa-line2"></div></div></div>`);
    const helpers = app.slice(app.indexOf('  // Row actions are model-authored replies'),app.indexOf('  // CCC-1218:'));
    await page.addScriptTag({content:`
      window.row=${JSON.stringify(row())}; window.sent=[]; window.toasts=[]; window.running=false;
      const _ultraFreshRow=()=>row, _ultraFreshCurrent=c=>c, _applyLiveOverlayToRow=c=>c;
      const rowOutcomeView=c=>({kind:c.state==='working'?'working':'plan',planOwner:'agent',eventDriven:true,
        text:c.session_state.completion.remaining_actions[0].text.replace(/^agent: /i,'')});
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
    assert.equal(sent[0][0],'callback-session');
    assert.equal(sent[0][1],actionSource(row(),true).action.reply);
    assert.deepEqual(sent[0].slice(2),['send',{userInitiated:true}]);
  } finally {await browser.close();}
});
